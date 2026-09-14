"""Who a message goes to, what it says, and what the log says afterwards.

The manager page answers one question — «what did we send?» — and the sender
page answers another — «who should get this?». Both live here so the router
stays a thin HTTP layer.

Two rules are enforced here rather than trusted to the form:

* **Consent.** A marketing message never goes to an archived customer, an
  explicit opt-out, or anyone tagged «بلاک». A transactional message (a نسیه
  reminder, a test) is about the customer's own money, so only the phone number
  matters.
* **The cap.** A bulk send stops at the store's «سقف پیامک» and reports how many
  people were left for the next round, because every message costs money.
"""
from __future__ import annotations

from datetime import datetime, timezone

import jdatetime
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Customer, SmsMessage, SmsTemplate, to_english_digits
from services._common import get_setting_int, is_archived_customer, jalali_str, marketing_opt_in
from services.customers import TAG_LABELS, TAG_PALETTE, parse_tags
from services.sms import queue_sms
from services.sms_templates import (
    CATEGORY_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    render_template,
    sms_metrics,
    template_variables,
    values_for_customer,
)

DEFAULT_LIMIT = 100

TIER_LABELS = {"silver": "نقره‌ای", "gold": "طلایی", "diamond": "الماس"}

MODE_LABELS = {
    "all": "همه مشتریان رضایت‌دار",
    "tier": "یک سطح",
    "tag": "یک برچسب",
    "picked": "مشتریان دست‌چین‌شده",
    "numbers": "شماره‌های دستی",
}

# Tags that always mean «do not message», whatever the consent flag says.
BLOCKING_TAGS = ("blocked",)


def message_block_reason(customer, *, transactional: bool = False) -> str:
    """Why this customer must not be messaged, or "" when they may be.

    The audience picker and the automatic triggers both ask *here*, so an
    opted-in answer cannot drift into an opted-out send: archived customers
    asked to be forgotten, «بلاک» meant it, and a marketing message needs
    consent while a transactional one (the customer's own order or balance)
    does not.
    """
    if customer is None:
        return ""
    if is_archived_customer(customer):
        return "archived"
    if any(key in parse_tags(customer.tags) for key in BLOCKING_TAGS):
        return "blocked"
    if not transactional and not marketing_opt_in(customer):
        return "opted_out"
    return ""


# ── phone numbers ─────────────────────────────────────────────────────────────

def normalise_phone(value) -> str:
    """A typed number → 11-digit ``09xxxxxxxxx``, or "" when it isn't one.

    Accepts Persian/Arabic digits, spaces, dashes, ``+98``, ``0098`` and the
    bare ``9xxxxxxxxx`` people paste from a contact card.
    """
    digits = "".join(char for char in to_english_digits(str(value or "")) if char.isdigit())
    if not digits:
        return ""
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("+98"):
        digits = digits[3:]
    if digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    if len(digits) != 11 or not digits.startswith("09"):
        return ""
    return digits


def parse_phone_list(value) -> list[str]:
    """A textarea of numbers → valid, de-duplicated, in the order given."""
    import re

    parts = re.split(r"[\s,;،\n\r]+", str(value or ""))
    out: list[str] = []
    for part in parts:
        phone = normalise_phone(part)
        if phone and phone not in out:
            out.append(phone)
    return out


# ── the audience ──────────────────────────────────────────────────────────────

def _limit(db: Session, limit: int | None = None) -> int:
    if limit:
        return max(1, int(limit))
    return max(1, get_setting_int(db, "campaign_sms_limit", DEFAULT_LIMIT))


def _candidate_row(customer: Customer) -> dict:
    return {"customer": customer, "phone": customer.phone, "name": customer.full_name}


def resolve_recipients(
    db: Session,
    *,
    mode: str = "all",
    tier: str = "",
    tag: str = "",
    picked=None,
    numbers: str = "",
    transactional: bool = False,
    limit: int | None = None,
) -> dict:
    """The people one send would reach, and why everyone else was skipped.

    ``transactional`` relaxes the consent rules (a نسیه reminder is not
    marketing) but archived customers are still left out — they asked to be
    forgotten, not to be billed at.
    """
    cap = _limit(db, limit)
    skipped = {"archived": 0, "opted_out": 0, "blocked": 0, "invalid": 0, "duplicate": 0}
    recipients: list[dict] = []
    seen: set[str] = set()

    def take(customer: Customer | None, phone: str) -> None:
        if not phone:
            skipped["invalid"] += 1
            return
        if phone in seen:
            skipped["duplicate"] += 1
            return
        reason = message_block_reason(customer, transactional=transactional)
        if reason:
            skipped[reason] += 1
            return
        seen.add(phone)
        recipients.append(
            {"customer": customer, "phone": phone, "name": _candidate_row(customer)["name"] if customer else ""}
        )

    pool: list[Customer] = []
    if mode in {"all", "tier", "tag"}:
        query = db.query(Customer).order_by(Customer.id.asc())
        if mode == "tier" and tier in TIER_LABELS:
            query = query.filter(Customer.tier == tier)
        pool = query.all()
        if mode == "tag":
            if tag not in TAG_LABELS:
                pool = []
            else:
                pool = [customer for customer in pool if tag in parse_tags(customer.tags)]
    elif mode == "picked":
        ids = [int(value) for value in (picked or []) if str(value).strip().isdigit()]
        if ids:
            pool = db.query(Customer).filter(Customer.id.in_(ids)).order_by(Customer.id.asc()).all()
    elif mode == "numbers":
        import re

        for part in re.split(r"[\s,;،\n\r]+", str(numbers or "")):
            if not part.strip():
                continue
            phone = normalise_phone(part)
            if not phone:
                skipped["invalid"] += 1
                continue
            customer = db.query(Customer).filter(Customer.phone == phone).first()
            take(customer, phone)
    else:
        mode = "all"
        pool = db.query(Customer).order_by(Customer.id.asc()).all()

    if mode != "numbers":
        for customer in pool:
            # A customer whose number cannot be dialled is skipped, not guessed
            # at: normalise_phone blanks anything that is not an 09x number.
            take(customer, normalise_phone(customer.phone))

    matched = len(recipients)
    window = recipients[:cap]
    return {
        "recipients": window,
        "matched": matched,
        "count": len(window),
        "capped": matched > len(window),
        "limit": cap,
        "skipped": skipped,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, MODE_LABELS["all"]),
        "tier_label": TIER_LABELS.get(tier, ""),
        "tag_label": TAG_LABELS.get(tag, ""),
    }


def audience_choices(db: Session, *, transactional: bool = False) -> list[dict]:
    """Every audience mode with the number of people it would reach now.

    «دست‌چین» and «شماره‌های دستی» carry no count because nothing is chosen yet;
    the form counts them once a selection or a number list exists.
    """
    choices = [{
        "key": "all",
        "label": MODE_LABELS["all"],
        "count": resolve_recipients(db, transactional=transactional)["count"],
    }]
    for key, label in TIER_LABELS.items():
        plan = resolve_recipients(db, mode="tier", tier=key, transactional=transactional)
        choices.append({"key": f"tier:{key}", "label": f"سطح {label}", "count": plan["count"]})
    for key, label in TAG_PALETTE:
        plan = resolve_recipients(db, mode="tag", tag=key, transactional=transactional)
        choices.append({"key": f"tag:{key}", "label": f"برچسب {label}", "count": plan["count"]})
    choices.append({"key": "picked", "label": MODE_LABELS["picked"], "count": None})
    choices.append({"key": "numbers", "label": MODE_LABELS["numbers"], "count": None})
    return choices


def plan_from_form(db: Session, *, audience: str, picked=None, numbers: str = "",
                   transactional: bool = False) -> dict:
    """``tier:gold`` / ``tag:vip`` / ``picked`` / ``numbers`` → a real plan."""
    raw = (audience or "all").strip()
    if raw.startswith("tier:"):
        return resolve_recipients(db, mode="tier", tier=raw.split(":", 1)[1],
                                 transactional=transactional)
    if raw.startswith("tag:"):
        return resolve_recipients(db, mode="tag", tag=raw.split(":", 1)[1],
                                 transactional=transactional)
    if raw in {"picked", "numbers", "all"}:
        return resolve_recipients(db, mode=raw, picked=picked, numbers=numbers,
                                 transactional=transactional)
    return resolve_recipients(db, mode="all", transactional=transactional)


# ── sending ───────────────────────────────────────────────────────────────────

async def send_bulk(
    db: Session,
    *,
    template: SmsTemplate,
    plan: dict,
    source: str = "manual",
    employee_id: int | None = None,
    extra: dict | None = None,
) -> dict:
    """Queue one rendered message per recipient. Returns an honest count.

    A recipient whose rendered text comes out empty is skipped rather than sent
    a blank SMS, and that count is reported alongside the queued one.
    """
    queued = 0
    empty = 0
    for recipient in plan["recipients"]:
        customer = recipient.get("customer")
        values = values_for_customer(customer, template, extra)
        body = render_template(template, values)
        if not body.strip():
            empty += 1
            continue
        job = await queue_sms(body, recipient["phone"], {}, db, template=template,
                              source=source, customer=customer,
                              employee_id=employee_id, body=body)
        if job is not None:
            queued += 1
    db.commit()
    return {"queued": queued, "empty": empty, "matched": plan["matched"], "plan": plan}


def preview_body(template: SmsTemplate, values: dict | None = None) -> str:
    """What one message looks like, using samples for anything unfilled."""
    return render_template(template, values, use_samples=True)


def template_card(db: Session, template: SmsTemplate) -> dict:
    """Everything the editor and the manager list show about one template.

    The row answers «has this one ever spoken?» without opening the history,
    because a template nobody has ever sent is usually a template somebody
    forgot to switch on — and «هرگز» is a more useful thing to see than a zero.
    """
    metrics = sms_metrics(template.body or "")
    mine = SmsMessage.template_id == template.id

    def count(*conditions) -> int:
        return int(db.query(func.count(SmsMessage.id)).filter(mine, *conditions).scalar() or 0)

    # The four states kept apart, so «ارسال‌شده» can no longer flatter a message
    # that is still in the queue or that the gateway refused.
    total = count()
    sent = count(SmsMessage.status == "sent")
    failed = count(SmsMessage.status == "failed")
    # When a message was sent, but its `sent_at` predates that column, the row's
    # own creation time is the honest answer — never the epoch.
    last_sent_at = db.query(
        func.max(func.coalesce(SmsMessage.sent_at, SmsMessage.created_at)),
    ).filter(mine, SmsMessage.status == "sent").scalar()
    last_activity_at = db.query(func.max(SmsMessage.created_at)).filter(mine).scalar()
    return {
        "template": template,
        "variables": template_variables(template),
        "metrics": metrics,
        "total": total,
        "sent": sent,
        "failed": failed,
        "queued": total - sent - failed,
        "last_sent_at": last_sent_at,
        "last_activity_at": last_activity_at,
        "preview": preview_body(template),
        "category_label": CATEGORY_LABELS.get(template.category, template.category),
    }


# ── the log ───────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    today = jdatetime.date.today().togregorian()
    return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)


def message_overview(db: Session) -> dict:
    """The manager page's KPI row."""
    from services.sms_templates import ensure_seeded

    ensure_seeded(db)
    total = db.query(func.count(SmsMessage.id)).scalar() or 0
    sent = db.query(func.count(SmsMessage.id)).filter(SmsMessage.status == "sent").scalar() or 0
    queued = db.query(func.count(SmsMessage.id)).filter(SmsMessage.status == "queued").scalar() or 0
    failed = db.query(func.count(SmsMessage.id)).filter(SmsMessage.status == "failed").scalar() or 0
    today = db.query(func.count(SmsMessage.id)).filter(
        SmsMessage.created_at >= _today_start(),
    ).scalar() or 0
    marketing = db.query(func.count(SmsMessage.id)).filter(
        SmsMessage.kind == "marketing",
    ).scalar() or 0
    active_templates = db.query(func.count(SmsTemplate.id)).filter(
        SmsTemplate.is_active == True,  # noqa: E712
    ).scalar() or 0
    all_templates = db.query(func.count(SmsTemplate.id)).scalar() or 0
    return {
        "total": int(total),
        "sent": int(sent),
        "queued": int(queued),
        "failed": int(failed),
        "today": int(today),
        "marketing": int(marketing),
        "active_templates": int(active_templates),
        "all_templates": int(all_templates),
    }


HISTORY_ORDERS = {"newest": "جدیدترین", "oldest": "قدیمی‌ترین"}


def journey_label(message: SmsMessage) -> str:
    """The gateway leg beyond the three queue states, in the owner's words.

    «در صف» → the phone has not claimed it; «دست گوشی» → claimed, not yet
    reported; «تحویل شد» → the carrier confirmed it; «نرسید» → the carrier
    refused. Empty for everything the queue already says plainly.
    """
    if message.status == "queued":
        return "دست گوشی" if message.delivery_state == "claimed" else ""
    if message.status == "sent":
        return {"delivered": "تحویل شد", "undelivered": "نرسید"}.get(message.delivery_state, "")
    return ""


def message_filtered(
    db: Session,
    *,
    search: str = "",
    status: str = "all",
    source: str = "all",
    order: str = "newest",
    page: int = 1,
    per_page: int = 25,
) -> dict:
    """One page of the log: what went out, to whom, and what the queue said."""
    page = max(1, int(page or 1))
    query = db.query(SmsMessage)
    if status in STATUS_LABELS:
        query = query.filter(SmsMessage.status == status)
    if source in SOURCE_LABELS:
        query = query.filter(SmsMessage.source == source)
    if search.strip():
        needle = f"%{search.strip()}%"
        matching_ids = [row[0] for row in db.query(Customer.id).filter(
            or_(Customer.first_name.ilike(needle),
                Customer.last_name.ilike(needle),
                Customer.phone.ilike(needle)),
        ).all()]
        conditions = [SmsMessage.phone.ilike(needle), SmsMessage.body.ilike(needle)]
        if matching_ids:
            conditions.append(SmsMessage.customer_id.in_(matching_ids))
        query = query.filter(or_(*conditions))

    if order == "oldest":
        query = query.order_by(SmsMessage.id.asc())
    else:
        query = query.order_by(SmsMessage.id.desc())

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = query.offset((page - 1) * per_page).limit(per_page).all()

    names: dict[int, Customer] = {}
    ids = [row.customer_id for row in rows if row.customer_id]
    if ids:
        names = {customer.id: customer for customer in db.query(Customer).filter(
            Customer.id.in_(ids),
        ).all()}

    return {
        "rows": [{
            "message": row,
            "customer": names.get(row.customer_id),
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "journey_label": journey_label(row),
            "source_label": SOURCE_LABELS.get(row.source, row.source),
            "segments": sms_metrics(row.body or "")["segments"],
        } for row in rows],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "status": status,
        "source": source,
        "order": order,
        "status_labels": STATUS_LABELS,
        "source_labels": SOURCE_LABELS,
        "category_labels": CATEGORY_LABELS,
        "jalali_str": jalali_str,
    }
