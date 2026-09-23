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
import logging
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Settings, SmsMessage, SmsTemplate, to_english_digits
from services.tier import TIER_LABELS

logger = logging.getLogger(__name__)

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
# «follow_up» nags a lapsed customer to come back, so it needs their consent;
# «purchase» thanks them for their own order, so it does not.
MARKETING_SOURCES = ("welcome", "birthday", "tier_up", "campaign", "manual", "follow_up")
SOURCE_LABELS = {
    "manual": "ارسال دستی",
    "welcome": "خوش‌آمدگویی",
    "birthday": "تولد",
    "tier_up": "ارتقای سطح",
    "campaign": "کمپین",
    "credit_reminder": "یادآوری نسیه",
    "purchase": "پس از خرید",
    "follow_up": "پیگیری",
    "monthly_digest": "خلاصه ماهانه",
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
        "mode": "auto",
        "trigger": "با ثبت‌نام هر مشتری تازه، خودبه‌خود در صف ارسال قرار می‌گیرد.",
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
        "mode": "manual",
        "send_url": "/admin/birthdays",
        "send_action": "صفحه تولدها",
        "trigger": ("تولدهای نزدیک را در صفحه «تولدها» می‌بینید، هرکدام را که خواستید تیک "
                    "می‌زنید و می‌فرستید."),
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
        "mode": "manual",
        "send_url": "/admin/tier-up",
        "send_action": "صفحه ارتقای سطح",
        "trigger": ("مشتریان واجد شرایط را در صفحه «ارتقای سطح» می‌بینید، تیک می‌زنید و "
                    "می‌فرستید."),
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
        "mode": "manual",
        "send_url": "/admin/tier-up",
        "send_action": "صفحه ارتقای سطح",
        "trigger": ("مشتریان واجد شرایط را در صفحه «ارتقای سطح» می‌بینید، تیک می‌زنید و "
                    "می‌فرستید."),
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
        "mode": "manual",
        "send_url": "/admin/campaigns",
        "send_action": "صفحه کمپین‌ها",
        "trigger": ("از صفحه کمپین، مخاطبان را می‌بینید و برایشان می‌فرستید؛ خودبه‌خود "
                    "فرستاده نمی‌شود."),
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
        "mode": "manual",
        "send_url": "/admin/credit",
        "send_action": "صفحه نسیه",
        "trigger": ("از صفحه «نسیه»، برای یک بدهکار یا همه بدهکاران سررسیدشده "
                    "می‌فرستید."),
        "variables": (
            {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
            {"token": "var2", "label": "مبلغ بدهی (تومان)", "sample": "1,250,000", "field": None},
            {"token": "var3", "label": "سررسید", "sample": "1405/06/30", "field": None},
        ),
    },
)

MODE_LABELS = {"auto": "خودکار", "manual": "دستی"}

# Nothing in the app queues a custom template, so its answer to «چگونه فرستاده
# می‌شود؟» is the hand-send page — spelled out rather than left to be guessed.
CUSTOM_TRIGGER = (
    "این قالب خودبه‌خود فرستاده نمی‌شود — از صفحه «ارسال پیامک» مخاطبان را انتخاب "
    "می‌کنید (همه، یک سطح، یک برچسب یا شماره‌های دستی)، تعدادشان را می‌بینید و "
    "دستی می‌فرستید."
)
CUSTOM_SEND_URL = "/admin/sms/send"
CUSTOM_SEND_ACTION = "ارسال پیامک"

# ── triggers: leaving the building without a click ────────────────────────────
# A custom template can opt into exactly one of these. The vocabulary lives here
# beside the rest of the labelled words, while the code that actually fires them
# lives in ``services.sms_triggers`` — so a template can never *say* «خودکار»
# somewhere that nothing sends it.
#
# ``kind`` is the consent rule the trigger obeys: a thank-you for the customer's
# own purchase is transactional, while a nudge to come back is marketing and
# needs their consent like any campaign.
TRIGGER_NONE = ""
DEFAULT_FOLLOW_UP_DAYS = 30
MAX_TRIGGER_DAYS = 365
AUTO_SEND_LIMIT_DEFAULT = 50

TRIGGERS = (
    {
        "key": "purchase",
        "label": "پس از هر خرید",
        "mode": "auto",
        "kind": "transactional",
        "source": "purchase",
        "action_label": "خرید",
        "needs_days": False,
        "text": ("هر بار که برای این مشتری فروشی ثبت می‌شود، خودبه‌خود برای همان مشتری "
                 "فرستاده می‌شود."),
        "hint": ("متن باید دربارهٔ همان خرید باشد — چیزی مثل «ممنون از خریدتان». "
                 "برای هر فاکتور فقط یک‌بار فرستاده می‌شود."),
    },
    {
        "key": "follow_up",
        "label": "پیگیری پس از چند روز",
        "mode": "auto",
        "kind": "marketing",
        "source": "follow_up",
        "action_label": "پیگیری",
        "needs_days": True,
        "text": ("هر روز بررسی می‌شود و برای مشتریانی که {days} روز است خرید نکرده‌اند "
                 "فرستاده می‌شود — برای هر دورهٔ خرید فقط یک‌بار."),
        # The two moments this trigger has. The sweep runs unattended; the review
        # page only lets the owner get there first, so the page is offered as an
        # «همچنین» and never as a gate — a page that implies nothing goes out
        # until you tick it would be the same kind of lie as a switch that does
        # nothing.
        "send_url": "/admin/follow-ups",
        "send_action": "صفحه پیگیری",
        "also": ("می‌خواهید پیش از نوبت خودتان تصمیم بگیرید؟ در صفحه «پیگیری» می‌بینید "
                 "الان چه کسی موعدش رسیده و می‌توانید همان‌جا بفرستید — ولی ارسال "
                 "خودکار متوقف نمی‌شود."),
        "hint": ("به‌خاطر تبلیغاتی بودن، فقط برای مشتریانی می‌رود که رضایت پیامک "
                 "تبلیغاتی داده‌اند؛ بایگانی‌شده‌ها و برچسب «بلاک» کنار گذاشته می‌شوند."),
    },
)
TRIGGERS_BY_KEY = {item["key"]: item for item in TRIGGERS}


def trigger_days(template) -> int:
    """The wait this template asks for, clamped to something a shop can mean."""
    raw = getattr(template, "trigger_days", 0) or 0
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return DEFAULT_FOLLOW_UP_DAYS
    return min(days, MAX_TRIGGER_DAYS)


def trigger_from_form(key: str, days) -> tuple[str, int]:
    """What the editor asked for, with anything unrecognised meaning hand-sent.

    A form is not a trusted source of behaviour: an unknown trigger must fail
    towards «دستی» rather than towards a message going out on its own.
    """
    spec = TRIGGERS_BY_KEY.get((key or "").strip())
    if spec is None:
        return TRIGGER_NONE, 0
    if not spec["needs_days"]:
        return spec["key"], 0
    cleaned = to_english_digits(str(days or "")).strip()
    try:
        value = int(cleaned) if cleaned else DEFAULT_FOLLOW_UP_DAYS
    except (TypeError, ValueError):
        value = DEFAULT_FOLLOW_UP_DAYS
    return spec["key"], max(1, min(value, MAX_TRIGGER_DAYS))


def trigger_info(template) -> dict | None:
    """This template's trigger, or None while it is hand-sent.

    Built-ins keep their own senders, so an automatic mode is only ever read off
    a *custom* template's stored trigger.
    """
    if template is None or getattr(template, "is_builtin", False):
        return None
    spec = TRIGGERS_BY_KEY.get(getattr(template, "trigger_key", "") or "")
    if spec is None:
        return None
    return {
        **spec,
        "days": trigger_days(template),
        "text": spec["text"].format(days=trigger_days(template)),
        # Where the owner can get there first, for the triggers that have a page.
        "url": spec.get("send_url"),
        "action": spec.get("send_action", ""),
        "also": spec.get("also", ""),
    }


def send_info(template) -> dict:
    """How this template actually fires: the mode, the sentence, and where from.

    The manager page and the editor both ask this one function, so a template
    can never be labelled «خودکار» in one place and sent by hand in another.
    """
    key = getattr(template, "key", None)
    spec = next((item for item in BUILTIN_TEMPLATES if item["key"] == key), None)
    if spec is None:
        # A custom template that opted into a trigger is no longer hand-sent, and
        # telling the owner to go send it from «ارسال پیامک» would be a lie.
        trigger = trigger_info(template)
        if trigger is not None:
            # An automatic trigger can still offer a page of its own: the
            # follow-up sweep runs by itself, and «صفحه پیگیری» is where the
            # owner can see who is waiting and act before the next pass.
            return {"mode": "auto", "label": MODE_LABELS["auto"], "text": trigger["text"],
                    "url": trigger.get("url"), "action": trigger.get("action", ""),
                    "also": trigger.get("also", ""), "trigger": trigger["key"]}
        return {"mode": "manual", "label": MODE_LABELS["manual"], "text": CUSTOM_TRIGGER,
                "url": CUSTOM_SEND_URL, "action": CUSTOM_SEND_ACTION, "also": "", "trigger": ""}
    mode = spec.get("mode", "manual")
    return {
        "mode": mode,
        "label": MODE_LABELS.get(mode, MODE_LABELS["manual"]),
        "text": spec.get("trigger", ""),
        "url": spec.get("send_url"),
        "action": spec.get("send_action", ""),
        "also": "",
        "trigger": spec["key"],
    }

# ── what a placeholder can be filled from ─────────────────────────────────────
# `field` names the customer column a token reads at send time. The names here
# are the ones a message can honestly promise: money and level arrive formatted
# (a customer never reads «gold» or «1250000»), and everything else is a column.


CUSTOMER_SOURCES = (
    {"field": "first_name", "label": "نام مشتری", "sample": "سارا"},
    {"field": "last_name", "label": "نام خانوادگی", "sample": "رضایی"},
    {"field": "full_name", "label": "نام و نام خانوادگی", "sample": "سارا رضایی"},
    {"field": "phone", "label": "شماره موبایل", "sample": "09121110000"},
    {"field": "tier", "label": "سطح باشگاه", "sample": "طلایی"},
    {"field": "total_points", "label": "امتیاز", "sample": "1,200"},
    {"field": "total_purchases", "label": "تعداد خرید", "sample": "7"},
    {"field": "total_spent", "label": "مجموع خرید (تومان)", "sample": "1,250,000"},
    {"field": "total_debt", "label": "بدهی نسیه (تومان)", "sample": "450,000"},
    {"field": "referral_code", "label": "کد معرف", "sample": "REF1234"},
    {"field": "child_name", "label": "نام فرزند", "sample": "نیلوفر"},
)
SOURCE_FIELDS = {item["field"]: item for item in CUSTOMER_SOURCES}

# Columns a message should show with thousands separators rather than raw.
MONEY_FIELDS = ("total_points", "total_purchases", "total_spent", "total_debt")

CUSTOM_VARIABLES = (
    {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
    {"token": "var2", "label": "متن دلخواه", "sample": "متن نمونه", "field": None},
    {"token": "var3", "label": "متن دلخواه", "sample": "متن نمونه", "field": None},
)

# ── ready-made sentences ─────────────────────────────────────────────
# What the editor's chips insert. A shop writes «%var1% عزیز، سلام!», not
# «%var1%»: a bare token is a blank to fill in, while a sentence is something to
# say. Each entry names the tokens it uses and what should read them.
#
# A custom sentence may only use values a customer row really holds, because an
# unbound token is refused on save — a chip that offered an unsaveable template
# would be worse than no chip at all. A built-in needs no binding, since its own
# sender hands it the values; its sentences therefore use exactly the tokens that
# sender fills (the campaign code, the birthday relationship, the amount owed),
# which the tests hold to the template's own declaration.
SMS_SENTENCES = (
    # ── a message the shop writes by hand ──
    {
        "key": "custom-greeting",
        "label": "سلام و احوال‌پرسی",
        "text": "%var1% عزیز، سلام! امیدواریم حالتان خوب باشد.",
        "bind": {"var1": "first_name"},
        "categories": ("custom",),
    },
    {
        "key": "custom-birthday",
        "label": "تبریک تولد",
        "text": "%var1% عزیز، تولدت مبارک! برایتان آرزوی سلامتی و شادی داریم.",
        "bind": {"var1": "first_name"},
        "categories": ("custom",),
    },
    {
        "key": "custom-debt",
        "label": "یادآوری بدهی نسیه",
        "text": "%var1% عزیز، %var2% تومان بدهی نسیه دارید. لطفاً هرچه زودتر تسویه کنید.",
        "bind": {"var1": "first_name", "var2": "total_debt"},
        "categories": ("custom",),
    },
    {
        "key": "custom-tier",
        "label": "ارتقای سطح باشگاه",
        "text": "%var1% عزیز، تبریک! سطح باشگاه شما %var2% شد و امتیازتان %var3% است.",
        "bind": {"var1": "first_name", "var2": "tier", "var3": "total_points"},
        "categories": ("custom",),
    },
    {
        "key": "custom-order-ready",
        "label": "آماده بودن سفارش",
        "text": "%var1% عزیز، سفارش شما آماده تحویل است. منتظر حضورتان هستیم.",
        "bind": {"var1": "first_name"},
        "categories": ("custom",),
    },
    {
        "key": "custom-come-back",
        "label": "دعوت دوباره",
        "text": "%var1% عزیز، دلمان برایتان تنگ شده! منتظرتان هستیم.",
        "bind": {"var1": "first_name"},
        "categories": ("custom",),
    },
    # ── the built-ins, worded with the tokens their own sender fills ──
    {
        "key": "welcome-join",
        "label": "خوش‌آمدگویی",
        "text": "%var1% عزیز، خوش آمدید! کد معرف شما %var2% است.",
        "bind": {},
        "categories": ("welcome",),
    },
    {
        "key": "birthday-you",
        "label": "تبریک تولد",
        "text": "%var1% عزیز، تولدت مبارک!",
        "bind": {},
        "categories": ("birthday",),
    },
    {
        "key": "birthday-whose",
        "label": "تبریک با ذکر صاحب تولد",
        "text": "%var1% عزیز، تولد %var3% مبارک!",
        "bind": {},
        "categories": ("birthday",),
    },
    {
        "key": "tier-up-reached",
        "label": "ارتقای سطح",
        "text": "%var1% عزیز، تبریک! به سطح بالاتر باشگاه رسیدید و امتیاز شما %var2% است.",
        "bind": {},
        "categories": ("tier_up",),
    },
    {
        "key": "campaign-invite",
        "label": "دعوت کمپین",
        "text": "%var1% عزیز، %var2% با کد %var3% و %var4%٪ تخفیف منتظر شماست.",
        "bind": {},
        "categories": ("campaign",),
    },
    {
        "key": "credit-overdue",
        "label": "یادآوری سررسید",
        "text": "%var1% عزیز، %var2% تومان بدهی نسیه دارید؛ سررسید %var3% بود. لطفاً تسویه کنید.",
        "bind": {},
        "categories": ("credit_reminder",),
    },
)


def sentences_for(category: str) -> list[dict]:
    """The ready-made sentences the editor offers for this kind of template."""
    return [item for item in SMS_SENTENCES if category in item["categories"]]


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

    The template row wins when it has ever been *saved* (any body at all, on or
    off). A template that was seeded inactive and empty — the shop never wrote
    that message — falls through to the legacy settings row, so a pattern
    written outside this page (a direct settings write, an older import) is
    still honoured: the row exists to mirror those writes, and an owner who
    sets ``sms_pattern_birthday`` by hand expects the wish to start.
    """
    ensure_seeded(db)
    template = db.query(SmsTemplate).filter(SmsTemplate.key == key).first()
    if template is None:
        spec = next((item for item in BUILTIN_TEMPLATES if item["key"] == key), None)
        return _setting_value(db, spec["setting_key"]) if spec else ""
    if template.is_active:
        return template.body or ""
    if (template.body or "").strip():
        return ""                             # deliberately switched off
    setting = _setting_value(db, template.setting_key) if template.setting_key else ""
    return setting


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


def fire_summary(db: Session) -> dict:
    """Templates split by how they fire, for the manager page's help card.

    Custom templates land in «دستی» beside the manual built-ins, so the page
    answers the whole question in one place instead of only naming a trigger per
    row.
    """
    auto: list[SmsTemplate] = []
    manual: list[SmsTemplate] = []
    for template in templates_for(db):
        (auto if send_info(template)["mode"] == "auto" else manual).append(template)
    return {"auto": auto, "manual": manual}


# ── rendering ─────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"%(\w+)%|\{(\w+)\}")


def render_text(body: str, values: dict) -> str:
    """Fill ``%var1%`` / ``{var1}`` placeholders, in either spelling."""
    text = str(body or "")
    for key, value in (values or {}).items():
        replacement = "" if value is None else str(value)
        text = text.replace(f"%{key}%", replacement).replace("{" + key + "}", replacement)
    return text


def source_value(customer, field: str) -> str:
    """One customer column, formatted the way a message should read it.

    Money arrives with thousands separators and the level as its Persian name,
    because a customer must never read «gold» or «1250000».
    """
    if customer is None or not field:
        return ""
    if field == "full_name":
        value = getattr(customer, "full_name", None) or \
            f"{getattr(customer, 'first_name', '') or ''} {getattr(customer, 'last_name', '') or ''}".strip()
    elif field == "tier":
        return TIER_LABELS.get(getattr(customer, "tier", "") or "", "")
    else:
        value = getattr(customer, field, None)
    if value in (None, ""):
        return ""
    if field in MONEY_FIELDS:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def slot_is_filled(template: SmsTemplate, item: dict) -> bool:
    """Whether anything will ever fill this token at send time.

    A built-in's unfilled slots are handed over by its own sender (the campaign
    row, the debt amount); a custom template has no sender at all, so a slot
    bound to no customer field is simply blank on the phone.
    """
    if item.get("field"):
        return True
    return template.category != "custom"


def slot_sample(template: SmsTemplate, item: dict) -> str:
    """What the preview should show for a token — a sample, or the truth."""
    return item["sample"] if slot_is_filled(template, item) else ""


def sample_values(template: SmsTemplate) -> dict:
    """Preview values: the declared samples, blanked where nothing can fill one."""
    return {item["token"]: slot_sample(template, item)
            for item in template_variables(template)}


def unfilled_in_body(body: str, variables) -> list[dict]:
    """Slots this text *uses* that nothing will ever fill — a hole on the phone.

    Honest for a custom template, where there is no sender to hand the slot a
    value: writing ``%var2%`` and leaving it unbound is how a customer gets
    « عزیز، عید مبارک». A built-in is judged by :func:`slot_is_filled` instead,
    because its own sender does fill its slots.
    """
    used = []
    for item in variables or ():
        if item.get("field"):
            continue
        if uses_token(body or "", item["token"]):
            used.append(item)
    return used


def unfilled_tokens(template: SmsTemplate | None) -> list[dict]:
    """The same question about a stored template, so the editor can warn."""
    if template is None or template.category != "custom":
        return []
    return unfilled_in_body(template.body or "", template_variables(template))


def render_template(template: SmsTemplate, values: dict | None = None, *,
                    use_samples: bool = False) -> str:
    """Render a template, blanking any declared token the caller did not fill.

    A placeholder that survives into an outgoing message would show a literal
    ``%var2%`` on the customer's phone, so unresolved **declared** tokens are
    removed rather than left in place. The token's *name* is what decides its
    value — never its position — so moving ``%var1%`` to the end of the text
    simply moves the name there.
    """
    provided = dict(values or {})
    resolved = {}
    for item in template_variables(template):
        token = item["token"]
        if token in provided and provided[token] is not None:
            resolved[token] = provided[token]
        elif use_samples:
            resolved[token] = slot_sample(template, item)
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
        token, field = item["token"], item.get("field")
        if field and token not in values:
            value = source_value(customer, field)
            if value:
                values[token] = value
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


def create_custom(db: Session, *, name: str, body: str, variables=None,
                  trigger_key: str = TRIGGER_NONE, trigger_days: int = 0) -> SmsTemplate:
    template = SmsTemplate(
        key=f"custom-{secrets.token_hex(6)}",
        name=name.strip(),
        category="custom",
        body=body,
        variables=_dump_variables(variables or CUSTOM_VARIABLES),
        setting_key=None,
        trigger_key=trigger_key,
        trigger_days=trigger_days,
        is_active=True,
        is_builtin=False,
        sort_order=100,
    )
    db.add(template)
    db.flush()
    return template


def duplicate(db: Session, template: SmsTemplate) -> SmsTemplate:
    """A starting point for a custom message, without touching the original.

    The copy starts hand-sent even when the original fires on its own: copying a
    live trigger would quietly double every message, and switching one on is
    meant to be a deliberate act.
    """
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

def uses_token(text: str, token: str) -> bool:
    """Whether this text carries the placeholder at all, in either syntax."""
    escaped = re.escape(str(token))
    return bool(re.search(rf"%{escaped}%", text or "")
                or re.search(r"\{" + escaped + r"\}", text or ""))


def recorded_values(template: SmsTemplate | None, values, pattern: str = "") -> list[dict]:
    """Which customer values a message was rendered from, labelled for the log.

    The frozen body says what went out; this says what it was built out of. The
    shop's own label travels with each value («نام مشتری», «بدهی نسیه») rather
    than the placeholder name, because the template may be renamed, re-bound or
    deleted long before anyone reads this row — and a record that needs the
    template to still exist to be readable is not a record.

    Only the placeholders the text actually *carries* are kept. A slot the
    template declares but the sentence never mentions, and a value handed in for
    a token that is not in the text (a campaign's code on a template that never
    asks for it), both changed nothing about what the customer read — so neither
    belongs in the record of why it reads the way it does.
    """
    provided = dict(values or {})
    text = ((template.body if template is not None else pattern) or "")
    out: list[dict] = []

    if template is not None:
        for item in template_variables(template):
            token = item["token"]
            if not uses_token(text, token):
                continue
            value = provided.get(token)
            out.append({
                "token": token,
                "label": item.get("label") or token,
                "value": "" if value is None else str(value),
            })
        return out

    # No template row: the values came from the ``attributes`` of a raw pattern,
    # so the only honest question is whether the text uses that token at all.
    for token, value in provided.items():
        if uses_token(text, str(token)):
            out.append({
                "token": str(token),
                "label": str(token),
                "value": "" if value is None else str(value),
            })
    return out


def dump_recorded(recorded) -> str:
    """The recorded values as the log stores them — never a crash on odd input."""
    try:
        return json.dumps(list(recorded or []), ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


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
    ref: str = "",
    values=None,
    pattern: str = "",
) -> SmsMessage:
    """Write one row of the log — the single writer every message goes through.

    ``values`` are the customer values this body was rendered from and
    ``pattern`` the raw text when there is no template row to declare them; the
    two are turned into the recorded list here rather than by each caller, so a
    new sender cannot log a message without saying what it was made of.
    """
    # The vocabulary lives in SOURCE_LABELS. A sender that invents a source is a
    # bug, not a new kind of message: log it and file the row under «دستی» so the
    # history never shows an unlabelled sender. (The table has no CHECK on this
    # column any more — see the note in ``models.SmsMessage``.)
    if source not in SOURCE_LABELS:
        logger.warning("Unknown SMS source %r; recording it as manual", source)
        source = "manual"
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
        ref=ref or "",
        # Always written, even when there is nothing to write: ``"[]"`` records
        # that the question was asked, which is how an old row (``""``) can be
        # told apart from a message that genuinely had no placeholders.
        values_json=dump_recorded(recorded_values(template, values, pattern)),
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
