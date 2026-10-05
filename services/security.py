"""Security helpers: admin auth (hashed password + sessions), CSRF protection,
API-token gate for /api/*, login rate limiting, try-on generation caps, and a
lightweight admin action log.

Everything here is deliberately dependency-free beyond the stdlib + FastAPI so
the app stays easy to install on a shop owner's computer.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db

# Late-bound so the module import graph stays acyclic: capture_device reads
# models, and security is imported by nearly every router.
def capture_auth_header():
    from services.capture_device import AUTH_HEADER
    return AUTH_HEADER


def capture_device_authenticated(db: Session, key: str) -> bool:
    from services.capture_device import authenticate_device
    return authenticate_device(db, key)
from starlette.responses import JSONResponse, RedirectResponse

from config import ADMIN_PASSWORD, API_TOKEN

logger = logging.getLogger(__name__)

# ── Password hashing (PBKDF2-HMAC-SHA256, stdlib only) ────────────────────────

_PBKDF2_ITERATIONS = 100_000
_PASSWORD_SETTING_KEY = "admin_password_hash"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def get_admin_password_hash(db) -> str | None:
    """Return the stored admin password hash, seeding it from the ADMIN_PASSWORD
    env var on first use. Returns None (login disabled) when neither exists."""
    from models import Settings
    row = db.query(Settings).filter(Settings.key == _PASSWORD_SETTING_KEY).first()
    if row and row.value:
        return row.value
    if not ADMIN_PASSWORD:
        return None
    hashed = hash_password(ADMIN_PASSWORD)
    db.add(Settings(key=_PASSWORD_SETTING_KEY, value=hashed))
    db.commit()
    return hashed


def set_admin_password(db, new_password: str) -> None:
    """Hash and store a new admin password (from the settings page)."""
    from models import Settings
    hashed = hash_password(new_password)
    row = db.query(Settings).filter(Settings.key == _PASSWORD_SETTING_KEY).first()
    if row:
        row.value = hashed
    else:
        db.add(Settings(key=_PASSWORD_SETTING_KEY, value=hashed))
    db.commit()


def check_admin_password(db, password: str) -> bool:
    stored = get_admin_password_hash(db)
    if not stored:
        return False
    return verify_password(password, stored)


ROLE_ORDER = {"cashier": 1, "manager": 2, "owner": 3}

# What each level is called in Persian, for the pages that name the viewer: the
# dashboard's own title and the 403 page both say whose view they are showing.
ROLE_LABELS = {"cashier": "صندوقدار", "manager": "مدیر", "owner": "مالک"}


# The sentence every refused action carries. It lives here so the 403 page can
# recognise it and stay quiet: the page says the same thing in its own words, so
# printing the raw detail under them would only repeat it.
PERMISSION_DENIED_DETAIL = "شما اجازه انجام این عملیات را ندارید."


def role_allows(role: str | None, minimum_role: str) -> bool:
    """Whether a role may open something that asks for ``minimum_role``.

    One comparison, in one place: the route guards, the sidebar and the
    dashboard's cards all have to answer this question the same way, or a link
    is drawn for somebody who will be refused at the door. An unknown role or an
    unknown requirement is never allowed.
    """
    return ROLE_ORDER.get(role or "", 0) >= ROLE_ORDER.get(minimum_role, 99)


# Per-person capability toggles layered on the role ladder. Each entry names
# the StaffUser column and the minimum role that may hold the capability at
# all — the toggle narrows, the role gates. Unknown capability: deny.
CAPABILITIES = {
    "can_refund": {"column": "can_refund", "minimum_role": "manager"},
    "can_discount": {"column": "can_discount", "minimum_role": "cashier"},
    "can_view_payroll": {"column": "can_view_payroll", "minimum_role": "manager"},
    "can_reconcile_pos": {"column": "can_reconcile_pos", "minimum_role": "manager"},
}

# What an unset (NULL) toggle reads as, per role. New capabilities resolve
# through the role alone until the owner says otherwise per person.
ROLE_CAP_DEFAULTS = {
    "cashier": {"can_refund": False, "can_discount": False,
                "can_view_payroll": False, "can_reconcile_pos": False},
    "manager": {"can_refund": True, "can_discount": True,
                "can_view_payroll": False, "can_reconcile_pos": True},
    "owner": {"can_refund": True, "can_discount": True,
              "can_view_payroll": True, "can_reconcile_pos": True},
}


def effective_cap(user, capability: str) -> bool:
    """Whether this person may exercise ``capability`` right now.

    Narrow-only intersection: the role must allow the capability's minimum
    level AND the person's toggle (or their role's default when unset) must
    be on. A toggle can take away what the role grants; it can never grant
    what the role denies. Owners bypass toggles so a mis-set switch can
    never lock the shop's own administrator out.
    """
    spec = CAPABILITIES.get(capability)
    if spec is None or user is None:
        return False
    if not getattr(user, "is_active", False):
        return False
    if (user.role or "") == "owner":
        return role_allows(user.role, spec["minimum_role"])
    if not role_allows(user.role, spec["minimum_role"]):
        return False
    toggle = getattr(user, spec["column"], None)
    if toggle is None:
        return bool(ROLE_CAP_DEFAULTS.get(user.role or "", {}).get(capability, False))
    return bool(toggle)


def require_cap(request: Request, db, capability: str):
    """Like :func:`require_html_role` for a capability: return the active user
    or an HTML response suitable for route guards (login redirect when
    anonymous, 403 otherwise)."""
    user = _session_staff_user(db, request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    if not effective_cap(user, capability):
        raise HTTPException(status_code=403, detail=PERMISSION_DENIED_DETAIL)
    if request.session.get("staff_role") != user.role:
        request.session["staff_role"] = user.role
    return user


# Per-invoice manual-discount ceilings per role, in the same narrow-only
# spirit as the toggles: a personal ceiling can only sit at or under the
# role's. None means unbounded (the owner answers to nobody here).
ROLE_DISCOUNT_LIMITS = {
    "cashier": {"amount": 0, "percent": 0},
    "manager": {"amount": 200_000, "percent": 10},
    "owner": {"amount": None, "percent": None},
}


def discount_limit(user, unit: str):
    """The effective ceiling for ``unit`` (``amount`` or ``percent``):
    personal value clamped to the role's, role default when unset, None when
    unbounded. Unknown unit or person: the strictest answer, zero."""
    if unit not in ("amount", "percent"):
        return 0
    role_default = ROLE_DISCOUNT_LIMITS.get(user.role if user else "", {}).get(unit, 0)
    personal = getattr(user, f"max_discount_{unit}", None) if user else None
    if personal is None:
        return role_default
    if role_default is None:
        return personal
    return min(personal, role_default)


def discount_allowed(user, amount=0, percent=0) -> tuple[bool, str]:
    """Whether this person may grant this manual discount on one invoice.

    Zero discounts pass without a key; otherwise the toggle gates first,
    then each unit is checked against its own ceiling, whichever hits first
    refusing with its own sentence. Owners bypass ceilings the way they
    bypass toggles.
    """
    if user is not None and (user.role or "") == "owner":
        return True, ""
    try:
        amount = int(amount or 0)
    except (TypeError, ValueError):
        amount = 0
    try:
        percent = int(percent or 0)
    except (TypeError, ValueError):
        percent = 0
    # Granting nothing needs no key: every till posts zero discounts by
    # default, and refusing those would lock honest cashiers out of sales.
    if amount <= 0 and percent <= 0:
        return True, ""
    if not effective_cap(user, "can_discount"):
        return False, "تخفیف دستی برای حساب شما فعال نیست."
    amount_limit = discount_limit(user, "amount")
    if amount_limit is not None and amount > amount_limit:
        return False, f"تخفیف مبلغی از سقف {amount_limit:,} تومان شما بیشتر است."
    percent_limit = discount_limit(user, "percent")
    if percent_limit is not None and percent > percent_limit:
        return False, f"تخفیف درصدی از سقف {percent_limit}٪ شما بیشتر است."
    return True, ""


def _session_staff_user(db, request: Request):
    """Return the active staff account for this session, with legacy owner fallback."""
    from models import StaffUser
    staff_id = request.session.get("staff_user_id")
    if staff_id:
        user = db.query(StaffUser).filter(StaffUser.id == staff_id, StaffUser.is_active == True).first()
        if user:
            return user
        request.session.clear()
    return None


def current_staff_user(db, request: Request):
    return _session_staff_user(db, request)


def require_role(request: Request, db, minimum_role: str = "cashier"):
    """Require an active staff account whose role meets the requested level."""
    user = _session_staff_user(db, request)
    if not user or not role_allows(user.role, minimum_role):
        raise HTTPException(status_code=403, detail=PERMISSION_DENIED_DETAIL)
    # The account, not the login, is the authority — so the role it just
    # confirmed is written back for the shell to read. Without this, promoting a
    # manager left the sidebar drawn for their old role while the dashboard drew
    # its cards for the new one: one page showing two different viewers.
    if request.session.get("staff_role") != user.role:
        request.session["staff_role"] = user.role
    return user


def check_role(request: Request, db, minimum_role: str = "cashier") -> bool:
    try:
        require_role(request, db, minimum_role)
        return True
    except HTTPException:
        return False


def require_html_role(request: Request, db, minimum_role: str = "cashier"):
    """Return the active user or an HTML response suitable for route guards."""
    try:
        return require_role(request, db, minimum_role)
    except HTTPException as error:
        if not request.session.get("staff_user_id"):
            return RedirectResponse(url="/admin/login", status_code=303)
        raise error


def ensure_owner_account(db):
    """Migrate the existing single admin password into the owner staff account."""
    from models import StaffUser
    user = db.query(StaffUser).filter(StaffUser.username == "owner").first()
    password_hash = get_admin_password_hash(db)
    if not password_hash:
        return None
    if not user:
        user = StaffUser(username="owner", password_hash=password_hash, role="owner", is_active=True)
        db.add(user)
        db.commit()
    elif user.password_hash != password_hash:
        user.password_hash = password_hash
        db.commit()
    return user


def authenticate_staff(db, username: str, password: str):
    from models import StaffUser
    user = db.query(StaffUser).filter(StaffUser.username == username.strip(), StaffUser.is_active == True).first()
    if user and verify_password(password, user.password_hash):
        return user
    # Existing installations have only the old password; owner is the migrated identity.
    owner = ensure_owner_account(db)
    if username.strip() == "owner" and owner and verify_password(password, owner.password_hash):
        return owner
    return None


# ── Login rate limiting (in-memory; per client IP) ────────────────────────────

_login_attempts: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_LOCK_SECONDS = 15 * 60


def _client_ip(request: Request) -> str:
    # Behind a proxy the real client is X-Forwarded-For; locally the socket is fine.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def login_locked(request: Request) -> bool:
    ip = _client_ip(request)
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_LOCK_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS


def login_failure(request: Request) -> None:
    ip = _client_ip(request)
    _login_attempts.setdefault(ip, []).append(time.time())


def login_success(request: Request) -> None:
    _login_attempts.pop(_client_ip(request), None)


# ── Admin action log ──────────────────────────────────────────────────────────

def log_action(db, action: str, detail: str = "", request: Request | None = None,
               target_type: str | None = None, target_id: int | None = None,
               before: dict | None = None, after: dict | None = None) -> None:
    """Record an authenticated staff action without breaking the real operation."""
    try:
        from models import AdminLog
        user = _session_staff_user(db, request) if request is not None else None
        if request is not None and user is None and action not in {"login_failed", "login_blocked"}:
            logger.error("Refusing anonymous audit event: %s", action)
            return
        db.add(AdminLog(
            action=action,
            detail=str(detail)[:500],
            staff_user_id=user.id if user else None,
            target_type=target_type,
            target_id=target_id,
            ip_address=_client_ip(request) if request is not None else None,
            request_id=request.headers.get("X-Request-ID") if request is not None else None,
            before_json=json.dumps(before, ensure_ascii=False, default=str)[:4000] if before else None,
            after_json=json.dumps(after, ensure_ascii=False, default=str)[:4000] if after else None,
        ))
        db.commit()
    except Exception as e:  # never let logging break the real operation
        logger.warning("log_action failed: %s", e)


# ── Admin action labels ───────────────────────────────────────────────────

# One Persian ledger-voice label per audit action, painted wherever an
# AdminLog row surfaces (logs page, staff timeline). Unknown codes fall back
# to the raw code — the audit trail never hides a row for want of a label.
ADMIN_ACTION_LABELS = {
    "attendance_mark": "ثبت حضور",
    "backup": "پشتیبان‌گیری",
    "birthday_sms": "پیامک تولد",
    "campaign_archive": "بایگانی کمپین",
    "campaign_delete": "حذف کمپین",
    "campaign_unarchive": "بازگشت کمپین از بایگانی",
    "capture_pair": "جفت‌سازی گوشی عکاسی",
    "capture_unpair": "قطع گوشی عکاسی",
    "cash_session_close": "بستن صندوق",
    "cash_session_open": "باز کردن صندوق",
    "cash_session_withdrawal": "برداشت از صندوق",
    "cash_session_withdrawal_reverse": "برگشت برداشت صندوق",
    "change_password": "تغییر گذرواژه",
    "check_add": "ثبت چک",
    "check_delete": "حذف چک",
    "check_edit": "ویرایش چک",
    "check_resolve": "پایان پیگیری چک",
    "check_status": "تغییر وضعیت چک",
    "credit_due_dates": "ثبت سررسیدها",
    "credit_limit": "تعیین سقف اعتبار",
    "credit_payment": "دریافت طلب",
    "customer_bulk_tag": "برچسب گروهی مشتری",
    "customer_delete": "حذف مشتری",
    "customer_discount": "اعمال تخفیف مشتری",
    "expense_add": "ثبت هزینه",
    "expense_reverse": "ابطال هزینه",
    "follow_up_sms": "پیامک پیگیری",
    "login": "ورود",
    "login_blocked": "مسدودی ورود",
    "login_failed": "ورود ناموفق",
    "logout": "خروج",
    "logs_archive": "بایگانی گزارش",
    "owner_profile_update": "به‌روزرسانی هویت کسب‌وکار",
    "payment_reverse": "برگشت دریافت",
    "pos_reconciliation": "تطبیق کارت‌خوان",
    "position_add": "افزودن سمت سازمانی",
    "position_update": "ویرایش سمت سازمانی",
    "purchase_reverse": "برگشت خرید",
    "recurring_expense_add": "افزودن هزینه ماهانه",
    "recurring_expense_delete": "حذف هزینه ماهانه",
    "recurring_expense_pause": "توقف هزینه ماهانه",
    "recurring_expense_resume": "ازسرگیری هزینه ماهانه",
    "refund": "ابطال فاکتور",
    "salary_bulk": "پرداخت گروهی",
    "salary_payment": "پرداخت حقوق",
    "salary_void": "ابطال حقوق",
    "sales_goal": "تعیین هدف فروش",
    "settings_update": "به‌روزرسانی تنظیمات",
    "setup_complete": "راه‌اندازی اولیه",
    "sms_audience_delete": "حذف مخاطبان",
    "sms_config": "تنظیم درگاه پیامک",
    "sms_digest_resend": "ارسال مجدد گزارش",
    "sms_digest_send_now": "ارسال فوری گزارش",
    "sms_gateway_pair": "جفت‌سازی درگاه پیامک",
    "sms_gateway_unpair": "قطع درگاه پیامک",
    "sms_retry": "تلاش مجدد پیامک",
    "sms_template_create": "ساخت قالب پیامک",
    "sms_template_delete": "حذف قالب پیامک",
    "sms_template_duplicate": "کپی قالب پیامک",
    "sms_template_test": "تست قالب پیامک",
    "sms_template_toggle": "تغییر وضعیت قالب پیامک",
    "sms_template_update": "ویرایش قالب پیامک",
    "staff_caps": "تغییر کلیدها",
    "staff_create": "استخدام",
    "staff_disable": "بستن دسترسی",
    "staff_enable": "بازگشایی دسترسی",
    "staff_update": "ویرایش پرونده",
    "supplier_add": "افزودن تأمین‌کننده",
    "supplier_delete": "حذف تأمین‌کننده",
    "supplier_edit": "ویرایش تأمین‌کننده",
    "supplier_payment": "پرداخت به تأمین‌کننده",
    "tag_settings_update": "به‌روزرسانی طرح تگ",
    "tag_template_update": "به‌روزرسانی قالب تگ",
    "theme_update": "به‌روزرسانی ظاهر فروشگاه",
    "tier_downgrade": "تنزل سطح مشتری",
    "tier_up_sms": "پیامک ارتقا",
    "variant_demand_reset": "صفر کردن تقاضا",
    "variant_demand_up": "ثبت تقاضا",
    "variant_update": "ویرایش تنوع",
}

# Destructive actions paint danger; everything else stays neutral. Suffixes,
# not a second list — a new `*_delete` tomorrow is red without a code change.
_ADMIN_ACTION_DANGER_SUFFIXES = (
    "_delete", "_void", "_failed", "_blocked", "_reverse",
    "_disable", "_unpair", "_downgrade",
)
_ADMIN_ACTION_DANGER_EXACT = {"refund"}


def admin_action_tone(action: str) -> str:
    """Badge tone for an audit action: 'danger' or '' (neutral)."""
    code = str(action or "")
    if code in _ADMIN_ACTION_DANGER_EXACT or code.endswith(_ADMIN_ACTION_DANGER_SUFFIXES):
        return "danger"
    return ""


# ── Audit target links ────────────────────────────────────────────────────

# Record types with an owner-readable detail page, as (Persian noun, URL
# template). Anything absent here — ambiguous ids (payment), list-only pages
# (checks, expenses), settings — renders as plain text, never a hopeful 404.
ADMIN_TARGET_LINKS = {
    "sale": ("فاکتور", "/admin/invoice/{id}"),
    "staff_user": ("کاربر", "/admin/staff/{id}"),
    "customer": ("مشتری", "/admin/customers/{id}"),
    "campaign": ("کمپین", "/admin/campaigns/{id}"),
    "salary_payment": ("فیش حقوق", "/admin/payroll/{id}/receipt"),
    "variant": ("تنوع", "/admin/variants/{id}/edit"),
    "sms_template": ("قالب پیامک", "/admin/sms/templates/{id}/edit"),
    "cash_session": ("صندوق", "/admin/cashbox/sessions/{id}"),
    "purchase": ("خرید", "/admin/purchases/{id}"),
}


def admin_target_link(target_type: str | None, target_id: int | None) -> tuple[str, str] | None:
    """(URL, link text) for an audit row's record, or None when unmappable."""
    if not target_type or target_id is None:
        return None
    entry = ADMIN_TARGET_LINKS.get(str(target_type))
    if entry is None:
        return None
    noun, template = entry
    return (template.format(id=target_id), f"{noun} #{target_id}")


def admin_log_diff(before_json: str | None, after_json: str | None) -> tuple[str, str] | None:
    """(before, after) pretty text for the change dialog, or None if neither."""
    if not before_json and not after_json:
        return None

    def _pretty(raw: str | None) -> str:
        if not raw:
            return "—"
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            return str(raw)

    return (_pretty(before_json), _pretty(after_json))

# ── CSRF protection ───────────────────────────────────────────────────────────

# /api/* endpoints are protected by the API token (or admin session), not CSRF,
# because the phone app is a separate client that can't read our session cookie.
# /gateway/* is the SMS device listener: it authenticates with the phone's own
# device key (X-Device-API-Key) and never sees a browser session, so CSRF has
# nothing to protect there either.
CSRF_EXEMPT_PREFIXES = ("/api", "/gateway")


class CSRFMiddleware:
    """Rejects state-changing requests that don't carry the session-bound CSRF
    token (form field `csrf_token` or `X-CSRF-Token` header). Buffers the body
    so downstream handlers still receive it."""

    def __init__(self, app, exempt_prefixes=CSRF_EXEMPT_PREFIXES):
        self.app = app
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith(self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        # Buffer the request body so we can inspect the token and still replay
        # the exact same bytes to the route handler below.
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            if not message.get("more_body", False):
                break

        state = {"index": 0}

        async def replay_receive():
            if state["index"] < len(messages):
                message = messages[state["index"]]
                state["index"] += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, replay_receive)
        expected = request.session.get("csrf_token", "") or ""
        token = ""
        try:
            form = await request.form()
            token = form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        except Exception:
            token = request.headers.get("X-CSRF-Token", "")

        if not expected or not token or not hmac.compare_digest(str(expected), str(token)):
            response = JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
            await response(scope, receive, send)
            return

        # Rewind the buffer so the route handler below can read the body too.
        state["index"] = 0
        await self.app(scope, replay_receive, send)


def csrf_token_for(request: Request) -> str:
    """Get (and lazily create) the session-bound CSRF token."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return request.session["csrf_token"]


def csrf_context_processor(request: Request) -> dict:
    """Jinja2 context processor: makes `csrf_token` available in every template."""
    return {"csrf_token": csrf_token_for(request)}


# ── API token gate ────────────────────────────────────────────────────────────

def require_api_token(request: Request, db: Session = Depends(get_db)) -> None:
    """Dependency for /api/* routers. Accepts the `X-API-Token` header (phone
    app), the capture phone's own paired key, or an admin login session
    (browser). When API_TOKEN is empty, only the capture key and the admin
    session are accepted — /api/* is never open to the world."""
    header_token = request.headers.get("X-API-Token", "")
    if API_TOKEN and header_token and hmac.compare_digest(header_token, API_TOKEN):
        return
    # The capture phone pairs by scanning the QR on the try-on page and holds a
    # per-device key (hash-only at rest, rotated on every re-pair) — it earns
    # the same /api/* access the static token used to grant the typed phone.
    capture_key = request.headers.get(capture_auth_header(), "")
    if capture_key and capture_device_authenticated(db, capture_key):
        return
    if request.session.get("api_token") == API_TOKEN and request.session.get("staff_user_id"):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


# ── Try-on generation caps (protects the paid AI API) ─────────────────────────

_generation_day: dict[str, int] = {}   # date(UTC) → count
_generation_ip: dict[str, list[float]] = {}  # ip → recent timestamps

_TRYON_IP_LIMIT = 8        # generations per hour per client
_TRYON_IP_WINDOW = 3600


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def tryon_daily_remaining(db) -> int:
    """How many generations are left today under the configured daily cap."""
    from services._common import get_setting_int
    limit = get_setting_int(db, "tryon_daily_limit", 20)
    used = _generation_day.get(_today_key(), 0)
    return max(0, limit - used)


def tryon_can_generate(request: Request, db) -> bool:
    """True if today's cap and the per-IP/hour rate limit both allow a call."""
    today = _today_key()
    try:
        from services._common import get_setting_int
        limit = get_setting_int(db, "tryon_daily_limit", 20)
    except Exception:
        limit = 20
    if _generation_day.get(today, 0) >= limit:
        return False
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _generation_ip.get(ip, []) if now - t < _TRYON_IP_WINDOW]
    _generation_ip[ip] = recent
    if len(recent) >= _TRYON_IP_LIMIT:
        return False
    return True


def tryon_record_generation(request: Request) -> None:
    today = _today_key()
    _generation_day[today] = _generation_day.get(today, 0) + 1
    ip = _client_ip(request)
    _generation_ip.setdefault(ip, []).append(time.time())
