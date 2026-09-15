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

import json
import logging
from datetime import datetime, timezone

import jdatetime
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Customer, SmsMessage, SmsTemplate, to_english_digits
from services._common import get_setting_int, is_archived_customer, jalali_str, marketing_opt_in
from services.customers import TAG_LABELS, TAG_PALETTE, parse_tags
from services.sms import queue_sms
from services.tier import TIER_LABELS
from services.sms_templates import (
    CATEGORY_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    grouped_templates,
    render_template,
    sms_metrics,
    template_variables,
    templates_for,
    values_for_customer,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100

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
                              employee_id=employee_id, body=body, values=values)
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


# ── the manager page's list ───────────────────────────────────────────────────
# A shop with forty templates cannot see which of them have gone quiet, so the
# list can be sliced by how a template has actually been used and ordered by when
# it last spoke. Both read the same four figures each row already shows, so the
# filter can never disagree with the badge beside the name.

# The words the owner reads, next to the card they describe.
USAGE_FILTERS = {
    "all": "همه قالب‌ها",
    "never": "هرگز فرستاده‌نشده",
    "used": "فرستاده‌شده",
    "failed": "دارای ارسال ناموفق",
}

TEMPLATE_SORTS = {
    "default": "ترتیب قالب‌ها (دسته‌بندی)",
    "last_sent_desc": "آخرین ارسال — جدیدترین",
    "last_sent_asc": "آخرین ارسال — قدیمی‌ترین",
}


def last_sent_stamp(card: dict) -> str:
    """When this template last actually sent something, or "" when it never has.

    ``sent_at`` only, on a row the queue has already accepted: a message that is
    still waiting on the phone, or one the gateway refused, is not a send. This is
    the single answer to «has this one ever spoken?», and the badge, the filter and
    the date order all read it — three different notions of «never sent» on one
    page is how a filter comes to disagree with the badge beside the name.
    """
    stamp = (card or {}).get("last_sent_at")
    return stamp.isoformat() if stamp is not None else ""


def usage_matches(card: dict, usage: str) -> bool:
    """Whether one template belongs in the slice of usage the owner asked for."""
    if usage == "never":
        return not last_sent_stamp(card)
    if usage == "used":
        return bool(last_sent_stamp(card))
    if usage == "failed":
        return bool(card.get("failed"))
    return True


def ordered_templates(templates, cards: dict, sort: str):
    """The templates in the order asked for; «default» keeps the catalogue order.

    A template that has never sent anything has no date to sort on. It sits at
    the *oldest* end of the list either way, because that is where the eye is
    when someone sorts by date: with «قدیمی‌ترین» first the ones that have never
    spoken lead, and with «جدیدترین» first they close the list rather than being
    scattered through it by a made-up date.
    """
    if sort not in TEMPLATE_SORTS or sort == "default":
        return list(templates)
    spoken = [row for row in templates if last_sent_stamp(cards.get(row.id) or {})]
    never = [row for row in templates if not last_sent_stamp(cards.get(row.id) or {})]
    spoken.sort(key=lambda row: (last_sent_stamp(cards.get(row.id) or {}), row.id or 0),
                reverse=(sort == "last_sent_desc"))
    # A stable tie-break, so two templates that have never sent anything do not
    # swap places between one page load and the next.
    never.sort(key=lambda row: (row.sort_order or 0, row.id or 0))
    return never + spoken if sort == "last_sent_asc" else spoken + never


def manager_view(db: Session, *, usage: str = "all", sort: str = "default") -> dict:
    """What the manager page lists, filtered and ordered by how it has been used.

    Untouched, the page keeps its category grouping — the built-ins read as a
    catalogue, each in its place. As soon as a usage filter or a date order is
    asked for, the answer is *one* list: grouping by category would scatter the
    thing being looked for (the oldest-used text can be in any group) and would
    quietly defeat the very sort that was requested.

    Unknown values fall back to «همه» and the catalogue order rather than
    emptying the page — a hand-edited URL is not a reason to show nothing.
    """
    usage = usage if usage in USAGE_FILTERS else "all"
    sort = sort if sort in TEMPLATE_SORTS else "default"

    templates = templates_for(db)
    cards = {row.id: template_card(db, row) for row in templates}
    matched = [row for row in templates if usage_matches(cards[row.id], usage)]
    ordered = ordered_templates(matched, cards, sort)
    filtered = usage != "all" or sort != "default"

    if not filtered:
        sections = grouped_templates(db)
    elif ordered:
        sections = [{
            "key": "filtered",
            "label": USAGE_FILTERS[usage] if usage != "all" else "قالب‌های این فهرست",
            "templates": ordered,
            "active": sum(1 for row in ordered if row.is_active),
        }]
    else:
        # No match at all: an empty heading over an empty table reads like a bug,
        # so the page says so instead and offers the way back.
        sections = []

    never = sum(1 for row in templates if not last_sent_stamp(cards[row.id]))
    return {
        "sections": sections,
        "cards": cards,
        "usage": usage,
        "sort": sort,
        "usage_label": USAGE_FILTERS[usage],
        "sort_label": TEMPLATE_SORTS[sort],
        "filters_active": filtered,
        "total": len(templates),
        "shown": len(ordered),
        "never": never,
        "active": sum(1 for row in templates if row.is_active),
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


def _recorded_state(message) -> tuple[str, list[dict]]:
    """Read one row's recorded values, keeping "absent" apart from "broken".

    Never raises, and never guesses: a history page that cannot be opened
    because one row holds odd JSON would hide every other message with it, but
    blaming a broken record on the message itself would be a lie. The body
    survives either way, so a bad record costs the explanation, never the
    message.
    """
    raw = str(getattr(message, "values_json", "") or "").strip()
    if not raw:
        return "unrecorded", []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, list):
        logger.warning("SMS message %s has unreadable recorded values",
                       getattr(message, "id", "?"))
        return "unreadable", []
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "")
        if not token:
            continue
        value = item.get("value")
        out.append({
            "token": token,
            "label": str(item.get("label") or token),
            "value": "" if value is None else str(value),
        })
    if not out:
        return "none", []
    return "recorded", out


def recorded_rows(message) -> list[dict]:
    """The values this message was rendered from, or ``[]`` when there are none.

    The forgiving accessor: for anything that needs the values and does not care
    why they are missing (a report, an export, a test) rather than a page that
    must say which of the three it is.
    """
    return _recorded_state(message)[1]


def message_values(message, template=None) -> dict:
    """What one log entry was built out of, and whether it can be replayed.

    Reading the frozen body tells you what the customer received; this tells you
    *why* it reads that way — which customer values were behind it at that
    moment. Replaying those values through the template answers the question a
    log is actually kept for: does this row still add up?

    ``state`` is deliberately fine-grained, because "no values" has three
    different meanings and only one of them is a problem:

    * ``unrecorded`` — the row predates the record, so nothing is claimed;
    * ``unreadable`` — the record is there but cannot be trusted;
    * ``none`` — recorded, and the text used no placeholders at all;
    * ``match`` — a replay through the template reproduces the body exactly;
    * ``differs`` — the template changed since, so the replay does not;
    * ``template_gone`` — the template was deleted; the values survive without it.
    """
    state, recorded = _recorded_state(message)
    empty = {"recorded": [], "replay_body": None, "template": template}
    if state != "recorded":
        # «unrecorded» (the row predates the record), «unreadable» (the record is
        # there but cannot be trusted) and «none» (recorded: the text used no
        # placeholders) are three different facts and are never merged into one.
        return {"state": state, **empty}
    if template is None:
        return {"state": "template_gone", "recorded": recorded, "replay_body": None, "template": None}
    replay = render_template(template, {item["token"]: item["value"] for item in recorded})
    return {
        "state": "match" if replay == (message.body or "") else "differs",
        "recorded": recorded,
        "replay_body": replay,
        "template": template,
    }


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

    # The templates behind these rows, for one query rather than one per row:
    # a replay needs the template, and the audit line goes on every row that has
    # a record — including the rows whose template was deleted since.
    templates: dict[int, SmsTemplate] = {}
    template_ids = [row.template_id for row in rows if row.template_id]
    if template_ids:
        templates = {row.id: row for row in db.query(SmsTemplate).filter(
            SmsTemplate.id.in_(template_ids),
        ).all()}

    return {
        "rows": [{
            "message": row,
            "customer": names.get(row.customer_id),
            "status_label": STATUS_LABELS.get(row.status, row.status),
            "journey_label": journey_label(row),
            "source_label": SOURCE_LABELS.get(row.source, row.source),
            "segments": sms_metrics(row.body or "")["segments"],
            # Not called ``values``: on a dict, Jinja resolves ``row.values`` to
            # the built-in method before it ever looks for the key, so the page
            # would render a bound method instead of the record.
            "audit": message_values(row, templates.get(row.template_id)),
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
