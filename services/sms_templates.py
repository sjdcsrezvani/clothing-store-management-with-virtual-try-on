"""SMS templates: the six built-ins, the custom ones, and the legacy mirror.

Before this module every pattern lived in a ``settings`` row and nothing
recorded what was sent. Both of those change here:

* A built-in template *mirrors* its old ``settings`` row through
  ``setting_key``. Saving one writes the effective body back, so every reader
  that still asks for ``sms_pattern_welcome`` keeps working, and switching a
  template off writes an **empty** body — which is exactly what an empty
  pattern has always meant to the sending code.
* Seeding never invents text. A built-in whose settings row is empty is created
  switched off, so upgrading a shop that never wrote a birthday pattern cannot
  suddenly start messaging its customers.

The module owns rendering too, because the same placeholder syntax is used by
the live preview, the test send, and the queue.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Settings, SmsMessage, SmsTemplate

# ── vocabulary ────────────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "welcome": "خوش‌آمدگویی",
    "birthday": "تولد",
    "tier_up": "ارتقای سطح",
    "campaign": "کمپین",
    "credit_reminder": "یادآوری نسیه",
    "custom": "سفارشی",
}

CATEGORY_ORDER = ("welcome", "birthday", "tier_up", "campaign", "credit_reminder", "custom")

# Which sends count as marketing (gated by the customer's consent) and which are
# transactional (about the customer's own money, so always allowed).
MARKETING_SOURCES = ("welcome", "birthday", "tier_up", "campaign", "manual")
SOURCE_LABELS = {
    "manual": "ارسال دستی",
    "welcome": "خوش‌آمدگویی",
    "birthday": "تولد",
    "tier_up": "ارتقای سطح",
    "campaign": "کمپین",
    "credit_reminder": "یادآوری نسیه",
    "test": "آزمایشی",
}
STATUS_LABELS = {"queued": "در صف", "sent": "ارسال‌شده", "failed": "ناموفق"}


def kind_for_source(source: str) -> str:
    return "marketing" if source in MARKETING_SOURCES else "transactional"


# ── the built-ins ─────────────────────────────────────────────────────────────
# ``field`` names a real customer column the token is filled from when a send
# targets a specific person; a token without one is only ever a sample.

BUILTIN_TEMPLATES = (
    {
        "key": "welcome",
        "name": "پیامک خوش‌آمدگویی",
        "category": "welcome",
        "setting_key": "sms_pattern_welcome",
        "sort_order": 10,
        "trigger": "با ثبت‌نام مشتری تازه، خودکار در صف قرار می‌گیرد.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "کد معرف مشتری", "sample": "REF1234", "field": "referral_code"},
        ),
    },
    {
        "key": "birthday",
        "name": "پیامک تبریک تولد",
        "category": "birthday",
        "setting_key": "sms_pattern_birthday",
        "sort_order": 20,
        "trigger": "روزی که برای تولد مشتری یا فرزندش تخفیف فعال می‌شود.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "نام صاحب تولد", "sample": "نیلوفر", "field": "child_name"},
            {"token": "var3", "label": "مناسبت", "sample": "تولد فرزند", "field": None},
        ),
    },
    {
        "key": "tier_up_gold",
        "name": "پیامک ارتقا (نقره‌ای ← طلایی)",
        "category": "tier_up",
        "setting_key": "sms_pattern_tier_up_gold",
        "sort_order": 30,
        "trigger": "لحظه‌ای که سطح مشتری به طلایی می‌رسد.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "امتیاز", "sample": "1200", "field": "total_points"},
        ),
    },
    {
        "key": "tier_up_diamond",
        "name": "پیامک ارتقا (طلایی ← الماس)",
        "category": "tier_up",
        "setting_key": "sms_pattern_tier_up_diamond",
        "sort_order": 40,
        "trigger": "لحظه‌ای که سطح مشتری به الماس می‌رسد.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "امتیاز", "sample": "5000", "field": "total_points"},
        ),
    },
    {
        "key": "campaign",
        "name": "پیامک دعوت کمپین",
        "category": "campaign",
        "setting_key": "sms_pattern_campaign",
        "sort_order": 50,
        "trigger": "وقتی از صفحه کمپین، پیامک ارسال می‌کنید.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "نام کمپین", "sample": "جشنواره پاییز", "field": None},
            {"token": "var3", "label": "کد تخفیف", "sample": "AUTUMN20", "field": None},
            {"token": "var4", "label": "درصد تخفیف", "sample": "20", "field": None},
        ),
    },
    {
        "key": "credit_reminder",
        "name": "پیامک یادآوری بدهی نسیه",
        "category": "credit_reminder",
        "setting_key": "sms_pattern_credit_reminder",
        "sort_order": 60,
        "trigger": "دستی، از صفحه حساب نسیه برای یک بدهکار.",
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "مبلغ بدهی (تومان)", "sample": "1,250,000", "field": None},
            {"token": "var3", "label": "سررسید", "sample": "1405/06/30", "field": None},
        ),
    },
)

# What fires each built-in, shown on the manager page so «فعال» is never a
# mystery — the owner can see which automatic message they just switched on.
TRIGGER_LABELS = {spec["key"]: spec["trigger"] for spec in BUILTIN_TEMPLATES}

CUSTOM_VARIABLES = (
    {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
    {"token": "var2", "label": "متن دلخواه", "sample": "متن نمونه", "field": None},
    {"token": "var3", "label": "متن دلخواه", "sample": "متن نمونه", "field": None},
)

MAX_BODY = 612  # 9 segments of 68 — past this the send is almost certainly wrong
MAX_NAME = 120


# ── seeding & the settings mirror ─────────────────────────────────────────────

def _setting_value(db: Session, key: str) -> str:
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "") if row else ""


def _dump_variables(variables) -> str:
    return json.dumps(list(variables or ()), ensure_ascii=False)


def template_variables(template: SmsTemplate) -> list[dict]:
    """The declared placeholders, tolerating a hand-edited or empty column."""
    try:
        parsed = json.loads(template.variables or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if isinstance(item, dict) and item.get("token"):
            out.append({
                "token": str(item["token"]),
                "label": str(item.get("label") or item["token"]),
                "sample": str(item.get("sample") or ""),
                "field": item.get("field") or None,
            })
    return out


def ensure_seeded(db: Session, *, commit: bool = True) -> int:
    """Create any missing built-in template rows. Idempotent, additive.

    A row starts active only when the shop had already written that pattern, so
    this can never turn a silent feature on behind the owner's back.
    """
    existing = {row.key for row in db.query(SmsTemplate).all()}
    created = 0
    for spec in BUILTIN_TEMPLATES:
        if spec["key"] in existing:
            continue
        body = _setting_value(db, spec["setting_key"])
        db.add(SmsTemplate(
            key=spec["key"],
            name=spec["name"],
            category=spec["category"],
            body=body,
            variables=_dump_variables(spec["variables"]),
            setting_key=spec["setting_key"],
            is_active=bool(body.strip()),
            is_builtin=True,
            sort_order=spec["sort_order"],
        ))
        created += 1
    if created:
        db.flush()
        if commit:
            db.commit()
    return created


def sync_to_settings(db: Session, template: SmsTemplate) -> None:
    """Write the effective body back to the legacy settings row.

    Deactivating writes an empty string, because that is how every existing
    reader already spells «off».
    """
    if not template.setting_key:
        return
    value = (template.body or "") if template.is_active else ""
    row = db.query(Settings).filter(Settings.key == template.setting_key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=template.setting_key, value=value))


def get_template(db: Session, key: str) -> SmsTemplate | None:
    ensure_seeded(db)
    return db.query(SmsTemplate).filter(SmsTemplate.key == key).first()


def active_pattern(db: Session, key: str) -> str:
    """The body a send should use, or "" when there is nothing to send.

    Falls back to the settings row so a pattern written before this table
    existed is still honoured even if seeding has not run yet.
    """
    ensure_seeded(db)
    template = db.query(SmsTemplate).filter(SmsTemplate.key == key).first()
    if template is None:
        spec = next((item for item in BUILTIN_TEMPLATES if item["key"] == key), None)
        return _setting_value(db, spec["setting_key"]) if spec else ""
    if not template.is_active:
        return ""
    return template.body or ""


def templates_for(db: Session, *, category: str = "", search: str = "") -> list[SmsTemplate]:
    ensure_seeded(db)
    rows = db.query(SmsTemplate).all()
    if category in CATEGORY_LABELS:
        rows = [row for row in rows if row.category == category]
    if search.strip():
        needle = search.strip().lower()
        rows = [row for row in rows
                if needle in (row.name or "").lower() or needle in (row.body or "").lower()
                or needle in (row.key or "").lower()]
    order = {key: index for index, key in enumerate(CATEGORY_ORDER)}
    rows.sort(key=lambda row: (order.get(row.category, 99), row.sort_order or 0, row.id or 0))
    return rows


def grouped_templates(db: Session) -> list[dict]:
    """Templates bucketed by category, in a stable order, for the manager page."""
    rows = templates_for(db)
    groups: list[dict] = []
    for key in CATEGORY_ORDER:
        members = [row for row in rows if row.category == key]
        if members:
            groups.append({
                "key": key,
                "label": CATEGORY_LABELS[key],
                "templates": members,
                "active": sum(1 for row in members if row.is_active),
            })
    return groups


# ── rendering ─────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"%(\w+)%|\{(\w+)\}")


def render_text(body: str, values: dict) -> str:
    """Fill ``%var1%`` / ``{var1}`` placeholders, in either spelling."""
    text = str(body or "")
    for key, value in (values or {}).items():
        replacement = "" if value is None else str(value)
        text = text.replace(f"%{key}%", replacement).replace("{" + key + "}", replacement)
    return text


def sample_values(template: SmsTemplate) -> dict:
    """Preview values: the declared samples, with a real name where there is one."""
    return {item["token"]: item["sample"] for item in template_variables(template)}


def render_template(template: SmsTemplate, values: dict | None = None, *,
                    use_samples: bool = False) -> str:
    """Render a template, blanking any declared token the caller did not fill.

    A placeholder that survives into an outgoing message would show a literal
    ``%var2%`` on the customer's phone, so unresolved **declared** tokens are
    removed rather than left in place.
    """
    provided = dict(values or {})
    resolved = {}
    for item in template_variables(template):
        token = item["token"]
        if token in provided and provided[token] is not None:
            resolved[token] = provided[token]
        elif use_samples:
            resolved[token] = item["sample"]
        else:
            resolved[token] = ""
    text = render_text(template.body or "", resolved)
    return _PLACEHOLDER.sub("", text)


def values_for_customer(customer, template: SmsTemplate | None = None,
                        extra: dict | None = None) -> dict:
    """Fill `field`-backed tokens from a real customer row."""
    values = dict(extra or {})
    if customer is None:
        return values
    for item in template_variables(template) if template is not None else ():
        field = item.get("field")
        if field and field not in values:
            value = getattr(customer, field, None)
            if value not in (None, ""):
                values[item["token"]] = value
    return values


# ── measuring ─────────────────────────────────────────────────────────────────

def sms_metrics(body: str) -> dict:
    """Length and segment count, so the owner can see what one send costs."""
    text = str(body or "")
    unicode_text = any(ord(char) > 127 for char in text)
    per_segment = 70 if unicode_text else 160
    length = len(text)
    return {
        "length": length,
        "segments": 0 if length == 0 else -(-length // per_segment),
        "per_segment": per_segment,
        "encoding": "یونیکد (فارسی)" if unicode_text else "لاتین",
    }


# ── writing ───────────────────────────────────────────────────────────────────

def validate(name: str, body: str) -> str:
    """The sentences the form shows instead of failing."""
    if not (name or "").strip():
        return "نام قالب را وارد کنید."
    if len(name.strip()) > MAX_NAME:
        return f"نام قالب نباید بیش از {MAX_NAME} نویسه باشد."
    if not (body or "").strip():
        return "متن پیامک را وارد کنید — قالب خالی چیزی برای ارسال ندارد."
    if len(body) > MAX_BODY:
        return f"متن پیامک بیش از حد بلند است (حداکثر {MAX_BODY} نویسه)."
    return ""


def create_custom(db: Session, *, name: str, body: str, variables=None) -> SmsTemplate:
    template = SmsTemplate(
        key=f"custom-{secrets.token_hex(6)}",
        name=name.strip(),
        category="custom",
        body=body,
        variables=_dump_variables(variables or CUSTOM_VARIABLES),
        setting_key=None,
        is_active=True,
        is_builtin=False,
        sort_order=100,
    )
    db.add(template)
    db.flush()
    return template


def duplicate(db: Session, template: SmsTemplate) -> SmsTemplate:
    """A starting point for a custom message, without touching the original."""
    return create_custom(
        db,
        name=f"{template.name} (کپی)",
        body=template.body or "",
        variables=template_variables(template),
    )


def delete_blocked_reason(db: Session, template: SmsTemplate) -> str:
    """Why this one cannot be deleted, or "" when it can."""
    if template.is_builtin:
        return "قالب‌های پیش‌فرض حذف نمی‌شوند — برای خاموش‌کردن آن را غیرفعال کنید."
    sent = db.query(func.count(SmsMessage.id)).filter(
        SmsMessage.template_id == template.id,
    ).scalar() or 0
    if sent:
        return (f"این قالب روی {sent} پیامک ثبت شده و حذف آن سابقه ارسال را از بین می‌برد — "
                f"برای توقف آن را غیرفعال کنید.")
    return ""


# ── the log ───────────────────────────────────────────────────────────────────

def log_message(
    db: Session,
    *,
    phone: str,
    body: str,
    template: SmsTemplate | None = None,
    customer=None,
    source: str = "manual",
    kind: str | None = None,
    employee_id: int | None = None,
    status: str = "queued",
    error: str | None = None,
) -> SmsMessage:
    row = SmsMessage(
        template_id=template.id if template is not None else None,
        template_key=template.key if template is not None else None,
        template_name=template.name if template is not None else None,
        customer_id=getattr(customer, "id", None),
        employee_id=employee_id,
        phone=phone,
        body=body,
        status=status,
        kind=kind or kind_for_source(source),
        source=source,
        error=error,
    )
    db.add(row)
    db.flush()
    return row


def mark_message(db: Session, message_id: int | None, *,
                 status: str, error: str | None = None) -> SmsMessage | None:
    """Record what the worker did with a queued message."""
    if not message_id:
        return None
    row = db.query(SmsMessage).filter(SmsMessage.id == message_id).first()
    if row is None:
        return None
    row.status = status
    row.error = error
    if status == "sent":
        row.sent_at = datetime.now(timezone.utc)
    return row


def customer_for_phone(db: Session, phone: str):
    """Best-effort match so automatic sends land in the history with a name."""
    from models import Customer

    return db.query(Customer).filter(Customer.phone == phone).first()
