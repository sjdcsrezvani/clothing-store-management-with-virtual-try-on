import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import Settings, SmsMessage
from services._common import BIRTHDAY_SMS_NAMES
from services.sms_templates import (
    active_pattern,
    customer_for_phone,
    get_template,
    log_message,
    render_text,
)

logger = logging.getLogger(__name__)


def get_sms_setting(db: Session, key: str) -> str:
    """Get SMS setting from database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    return setting.value if setting and setting.value else ""


# The built-in patterns live in ``sms_templates`` now and mirror their old
# settings rows, so a template switched off reads as an empty pattern — which is
# exactly how the sending code has always spelled «off».
PATTERN_KEYS = {
    "welcome_pattern": "welcome",
    "birthday_pattern": "birthday",
    "tier_up_gold_pattern": "tier_up_gold",
    "tier_up_diamond_pattern": "tier_up_diamond",
    "campaign_pattern": "campaign",
    "credit_reminder_pattern": "credit_reminder",
}


def get_sms_config(db: Session) -> dict:
    """The pattern keys every sender reads. The gateway itself no longer needs
    credentials — it *is* this app — but the keys stay so the settings mirror
    and any shop fallback keep their meaning."""
    config = {
        "api_key": get_sms_setting(db, "sms_api_key"),
        "device_id": get_sms_setting(db, "sms_device_id"),
    }
    for field, key in PATTERN_KEYS.items():
        config[field] = active_pattern(db, key)
    return config


def _render(template: str, attributes: dict) -> str:
    """Fill %varN% / {varN} placeholders in a pattern template to build the message string."""
    return render_text(template, attributes)


async def send_sms(message: str, recipient: str, db: Session) -> bool:
    """Hand one message to the on-premise gateway queue.

    The old version posted to a VPS that answered 400 when the phone was
    offline — after five retries a birthday wish queued overnight was *lost*.
    There is no middleman now: the row is already in the gateway's queue (they
    are the same table), the phone's 15-second poll is the only latency, and a
    phone that is not here yet simply picks the message up later. So this
    always reports success — the *gateway* keeps the verdict honest, from the
    phone's own report, in ``report_result``.
    """
    if not message.strip() or not recipient:
        return False
    return True


async def send_pattern_sms(pattern: str, recipient: str, attributes: dict, db: Session, *,
                           source: str = "manual", template_key: str | None = None,
                           customer=None, log: bool = True) -> bool:
    """Render a pattern template into a message string and send it.

    Direct sends (the birthday and tier-up paths) are logged too, so the history
    page is the same story as the queue. The worker passes ``log=False`` because
    it is finishing a row that already exists.
    """
    message = _render(pattern, attributes)
    if log:
        template = get_template(db, template_key) if template_key else None
        if customer is None:
            customer = customer_for_phone(db, recipient)
        # Direct sends go straight into the queue too — same table, same phone,
        # same honest verdict from the device report instead of a guessed one.
        return await queue_sms("", recipient, {}, db, template=template,
                               source=source, customer=customer, body=message,
                               values=attributes)
    return None


# ========== queueing ==========
async def queue_sms(pattern: str, recipient: str, attributes: dict, db: Session, *,
                    template=None, template_key: str | None = None,
                    source: str = "manual", kind: str | None = None,
                    customer=None, employee_id: int | None = None,
                    body: str | None = None, ref: str = "", values=None):
    """Queue one message and record it in the log.

    The body is rendered **now**, at queue time, and that rendered text is what
    the worker sends — so editing a template afterwards can never rewrite a
    message that is already on its way, and the history always shows what the
    customer actually received.

    The customer values behind that text are recorded beside it, so the entry
    can be read, replayed and audited later. A caller that rendered the body
    itself (``body=``) must hand those values over in ``values=``; a caller
    passing a ``pattern`` needs to do nothing, because ``attributes`` are
    already exactly what the text was built from — and recording anything else
    would be a guess.
    """
    if not pattern and not body:
        return None
    rendered = body if body is not None else _render(pattern, attributes)
    if template is None and template_key:
        template = get_template(db, template_key)
    if customer is None:
        customer = customer_for_phone(db, recipient)
    if values is None and body is None:
        values = attributes
    row = log_message(db, phone=recipient, body=rendered, template=template,
                      customer=customer, source=source, kind=kind,
                      employee_id=employee_id, ref=ref, values=values,
                      pattern=pattern if body is None else "")
    # No BackgroundJob: the scheduler batch used to add up to five minutes in
    # front of every message, and the gateway claim is atomic on its own. The
    # log row *is* the queue item now; ``job_id`` stays for old rows.
    db.commit()
    return row


async def queue_welcome_sms(phone: str, first_name: str, referral_code: str, db: Session,
                            customer=None):
    config = get_sms_config(db)
    if not config["welcome_pattern"]:
        return None
    return await queue_sms(
        config["welcome_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": referral_code},
        db,
        template_key="welcome",
        source="welcome",
        customer=customer,
    )


async def send_welcome_sms(phone: str, first_name: str, referral_code: str, db: Session,
                           customer=None) -> bool:
    """Send welcome SMS after checkout completion."""
    config = get_sms_config(db)
    if not config["welcome_pattern"]:
        return False

    return await send_pattern_sms(
        config["welcome_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": referral_code},
        db,
        source="welcome",
        template_key="welcome",
        customer=customer,
    )


# ========== Pattern 2: Birthday SMS (Gold & Diamond) ==========
# Keys: var1=customer's name, var2=the name being celebrated, var3=whose birthday
# it is. var1/var2 keep their original meaning when the occasion is the child's,
# so a pattern written before this existed still reads correctly.
def birthday_sms_vars(first_name: str, child_name: str, occasion: str) -> dict:
    """Payload for the birthday pattern, covering both kinds of birthday."""
    if occasion == "child":
        celebrated = child_name or BIRTHDAY_SMS_NAMES["child"]
    else:
        celebrated = first_name or "مشتری"
    return {
        "var1": first_name or "مشتری",
        "var2": celebrated,
        "var3": BIRTHDAY_SMS_NAMES.get(occasion, BIRTHDAY_SMS_NAMES["child"]),
    }


async def send_birthday_sms(phone: str, first_name: str, child_name: str, db: Session,
                            occasion: str = "child", customer=None) -> bool:
    """Send the birthday wish for whichever birthday the store celebrates."""
    config = get_sms_config(db)
    if not config["birthday_pattern"]:
        return False

    return await send_pattern_sms(
        config["birthday_pattern"],
        phone,
        birthday_sms_vars(first_name, child_name, occasion),
        db,
        source="birthday",
        template_key="birthday",
        customer=customer,
    )


# ========== نسیه reminder ==========
# Keys: var1=customer's name, var2=what they still owe, var3=the سررسید they
# passed (or — when the shop agreed no date). Transactional: it is about the
# customer's own balance, so it does not depend on marketing consent — but it is
# only ever sent by hand, and the cool-down in the credit service keeps one
# customer from being reminded twice in an hour.
async def queue_credit_reminder_sms(phone: str, attributes: dict, db: Session,
                                    customer=None):
    """Queue the owner's reminder pattern for one debtor.

    Queued rather than sent inline, like the birthday and campaign messages, so
    a slow gateway cannot fail a collection round and the shop can still see
    what was accepted. Returns the job, or None when no pattern is written.
    """
    pattern = get_sms_config(db)["credit_reminder_pattern"]
    if not pattern:
        return None
    return await queue_sms(pattern, phone, attributes, db, template_key="credit_reminder",
                           source="credit_reminder", customer=customer)


# ========== Pattern 3: Tier-up (Silver → Gold) ==========
# Keys: var1=first_name, var2=points
async def send_tier_up_gold_sms(phone: str, first_name: str, points: int, db: Session,
                                customer=None) -> bool:
    """Send tier-up SMS to a customer who reached gold."""
    config = get_sms_config(db)
    if not config["tier_up_gold_pattern"]:
        return False

    return await send_pattern_sms(
        config["tier_up_gold_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": str(points)},
        db,
        source="tier_up",
        template_key="tier_up_gold",
        customer=customer,
    )


# ========== Pattern 4: Tier-up (Gold → Diamond) ==========
# Keys: var1=first_name, var2=points
async def send_tier_up_diamond_sms(phone: str, first_name: str, points: int, db: Session,
                                   customer=None) -> bool:
    """Send tier-up SMS to a customer who reached diamond."""
    config = get_sms_config(db)
    if not config["tier_up_diamond_pattern"]:
        return False

    return await send_pattern_sms(
        config["tier_up_diamond_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": str(points)},
        db,
        source="tier_up",
        template_key="tier_up_diamond",
        customer=customer,
    )


# ========== Pattern 5: Campaign SMS ==========
# Keys: var1=first_name, var2=campaign_name, var3=campaign_code, var4=campaign_discount_percent
async def send_campaign_sms(phone: str, first_name: str, campaign_name: str, campaign_code: str,
                            campaign_discount: int, db: Session, customer=None) -> bool:
    """Send the campaign invitation to one customer."""
    config = get_sms_config(db)
    if not config["campaign_pattern"]:
        return False

    return await send_pattern_sms(
        config["campaign_pattern"],
        phone,
        {
            "var1": first_name or "مشتری",
            "var2": campaign_name,
            "var3": campaign_code,
            "var4": str(campaign_discount),
        },
        db,
        source="campaign",
        template_key="campaign",
        customer=customer,
    )


def device_status_label(db: Session) -> str | None:
    """The paired phone's health, read from our own row — no HTTP, no VPS."""
    from services.sms_gateway import device_health

    device = device_health(db)
    if device is None:
        return None
    labels = {"online": "آنلاین", "offline": "آفلاین", "never_connected": "در انتظار اتصال گوشی"}
    return labels.get(device.status, device.status)
