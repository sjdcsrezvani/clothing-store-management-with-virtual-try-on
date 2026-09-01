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

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
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
    if not user or ROLE_ORDER.get(user.role, 0) < ROLE_ORDER.get(minimum_role, 99):
        raise HTTPException(status_code=403, detail="شما اجازه انجام این عملیات را ندارید.")
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


# ── CSRF protection ───────────────────────────────────────────────────────────

# /api/* endpoints are protected by the API token (or admin session), not CSRF,
# because the phone app is a separate client that can't read our session cookie.
CSRF_EXEMPT_PREFIXES = ("/api",)


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

def require_api_token(request: Request) -> None:
    """Dependency for /api/* routers. Accepts the `X-API-Token` header (phone
    app) or an admin login session (browser). When API_TOKEN is empty, only the
    admin session is accepted — /api/* is never open to the world."""
    header_token = request.headers.get("X-API-Token", "")
    if API_TOKEN and header_token and hmac.compare_digest(header_token, API_TOKEN):
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
