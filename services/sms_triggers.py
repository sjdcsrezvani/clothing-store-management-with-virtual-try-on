"""The automatic sends a custom template can opt into.

Two moments exist, and both are deliberately narrow:

* **پس از هر خرید** — the moment a sale is completed, for that one customer.
  Transactional: it is about their own order, so it does not depend on marketing
  consent, exactly like the نسیه reminder.
* **پیگیری پس از چند روز** — a daily sweep over customers whose *last purchase*
  is far enough behind them. Marketing, so it obeys consent, the archive flag and
  the «بلاک» tag.

Three rules hold this together, because an automatic SMS spends money and cannot
be taken back:

1. **Nothing changes without consent.** A template only fires when the owner gave
   it a trigger *and* it is switched on. The rules themselves are not re-invented
   here — :func:`services.sms_send.message_block_reason` is the same test the
   «ارسال پیامک» page applies, so an automatic send can never reach somebody the
   page would have skipped.
2. **One message per event.** Every automatic message records the event it
   belongs to in ``sms_messages.ref``: ``sale:12`` for a purchase, and
   ``purchase:2026-09-01`` for a follow-up. A trigger that already has that ref
   for that customer stays quiet — so a retried checkout, a double-click or a
   sweep that runs every five minutes cannot message anyone twice, and buying
   again re-arms the follow-up on its own.
3. **A sale never fails because of SMS.** The purchase trigger is called after
   the sale is committed and swallows its own errors — a template mistake or a
   broken gateway must not undo a completed sale.

The follow-up sweep is *throttled, never truncated*: at most
``trigger_sms_limit`` messages go out per run, and whoever was left over is
picked up by the next run five minutes later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import Customer, SmsMessage, SmsTemplate
from services._common import get_setting_int
from services.sms import queue_sms
from services.sms_send import normalise_phone, message_block_reason
from services.sms_templates import (
    AUTO_SEND_LIMIT_DEFAULT,
    TRIGGERS_BY_KEY,
    render_template,
    template_variables,
    trigger_days,
    unfilled_in_body,
    values_for_customer,
)

logger = logging.getLogger(__name__)

SETTING_AUTO_SEND_LIMIT = "trigger_sms_limit"


def _as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime (SQLite rows come back without tzinfo)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── who may fire ──────────────────────────────────────────────────────────────

def triggered_templates(db: Session, trigger: str) -> list[SmsTemplate]:
    """Switched-on custom templates that opted into this trigger and are ready.

    An automatic message has nobody watching it, so it has to be safe on its own:
    a template with no body, or one whose text uses a slot that nothing fills,
    is skipped here rather than putting a blank or a hole on a phone. The editor
    refuses to switch that combination on in the first place; this is the last
    gate, for a row that reached the table some other way.
    """
    if trigger not in TRIGGERS_BY_KEY:
        return []
    rows = db.query(SmsTemplate).filter(
        SmsTemplate.trigger_key == trigger,
        SmsTemplate.is_active == True,        # noqa: E712 — SQL comparison
    ).all()
    ready = []
    for row in rows:
        body = row.body or ""
        if not body.strip():
            continue
        holes = unfilled_in_body(body, template_variables(row))
        if holes:
            logger.warning(
                "SMS template %s (%s) uses %s with no value bound; not firing it",
                row.id, row.name, ", ".join(item["token"] for item in holes),
            )
            continue
        ready.append(row)
    return ready


def auto_send_limit(db: Session) -> int:
    """How many automatic messages one run may queue."""
    return max(1, get_setting_int(db, SETTING_AUTO_SEND_LIMIT, AUTO_SEND_LIMIT_DEFAULT))


def already_fired(db: Session, template: SmsTemplate, customer, ref: str) -> bool:
    """Whether this customer already had this template about this exact event."""
    if not ref or customer is None:
        return False
    return db.query(SmsMessage).filter(
        SmsMessage.template_id == template.id,
        SmsMessage.customer_id == customer.id,
        SmsMessage.ref == ref,
    ).first() is not None


async def _queue_one(db: Session, template: SmsTemplate, customer, *, trigger: str,
                     ref: str) -> SmsMessage | None:
    """Render one automatic message for one customer, or explain why not."""
    phone = normalise_phone(customer.phone)
    if not phone:
        return None
    values = values_for_customer(customer, template)
    body = render_template(template, values)
    if not body.strip():
        # Every slot came out empty, so the shop's text is not ready — better a
        # silent skip (reported in the sweep) than a message of pure whitespace.
        return None
    return await queue_sms(body, phone, {}, db, template=template, source=trigger,
                           customer=customer, body=body, ref=ref)


# ── trigger 1: a completed sale ───────────────────────────────────────────────

async def fire_purchase_sms(db: Session, *, sale, customer) -> list[SmsMessage]:
    """Queue every «پس از هر خرید» template for the customer who just bought.

    Never raises: it is called on the way out of a committed sale, and the shop's
    money is worth more than a thank-you.
    """
    queued: list[SmsMessage] = []
    try:
        if customer is None or sale is None:
            return queued
        spec = TRIGGERS_BY_KEY["purchase"]
        ref = f"sale:{sale.id}"
        for template in triggered_templates(db, "purchase"):
            if already_fired(db, template, customer, ref):
                continue
            if message_block_reason(customer, transactional=spec["kind"] == "transactional"):
                continue
            row = await _queue_one(db, template, customer, trigger=spec["source"], ref=ref)
            if row is not None:
                queued.append(row)
        if queued:
            db.commit()
    except Exception:                                  # noqa: BLE001 — see docstring
        logger.exception("Purchase SMS trigger failed for sale %s", getattr(sale, "id", None))
        db.rollback()
        return []
    return queued


# ── trigger 2: the follow-up sweep ────────────────────────────────────────────

def _follow_up_ref(customer: Customer) -> str:
    """The purchase a follow-up belongs to: the same buy never nags twice."""
    stamp = _as_utc(customer.last_purchase_date)
    if stamp is None:
        return ""
    return f"purchase:{stamp.date().isoformat()}"


def follow_up_candidates(db: Session, *, template: SmsTemplate, at: datetime | None = None,
                         limit: int | None = None) -> dict:
    """Who is due a follow-up now, and who was left out.

    Only customers who have *bought before* qualify: «پیگیری پس از آخرین خرید»
    is about a customer the shop already has a relationship with, and someone who
    never bought is not late — they were never there. The sweep reports them as
    «بدون خرید» instead of guessing that a welcome nudge is what the owner meant.
    """
    at = at or datetime.now(timezone.utc)
    days = trigger_days(template)
    cutoff = at - timedelta(days=days)
    cap = limit if limit is not None else auto_send_limit(db)

    due: list[Customer] = []
    skipped = {"no_purchase": 0, "too_soon": 0, "already_sent": 0,
               "archived": 0, "blocked": 0, "opted_out": 0, "invalid": 0, "empty": 0}
    for customer in db.query(Customer).order_by(Customer.id.asc()).all():
        last = _as_utc(customer.last_purchase_date)
        if last is None:
            skipped["no_purchase"] += 1
            continue
        if last > cutoff:
            skipped["too_soon"] += 1
            continue
        ref = _follow_up_ref(customer)
        if already_fired(db, template, customer, ref):
            skipped["already_sent"] += 1
            continue
        reason = message_block_reason(customer, transactional=False)
        if reason:
            skipped[reason] += 1
            continue
        if not normalise_phone(customer.phone):
            skipped["invalid"] += 1
            continue
        if not render_template(template, values_for_customer(customer, template)).strip():
            skipped["empty"] += 1
            continue
        due.append(customer)
        if len(due) >= cap:
            break
    return {"due": due, "skipped": skipped, "days": days, "limit": cap}


async def fire_follow_up_sms(db: Session, *, at: datetime | None = None) -> dict:
    """One pass of the follow-up trigger, for every template that opted in.

    Runs from the scheduler; the per-purchase ref makes a second pass a no-op, so
    a sweep every five minutes is safe and a customer who buys again becomes
    eligible for the *next* follow-up on their own.
    """
    at = at or datetime.now(timezone.utc)
    sent = 0
    empty = 0
    try:
        for template in triggered_templates(db, "follow_up"):
            plan = follow_up_candidates(db, template=template, at=at)
            for customer in plan["due"]:
                row = await _queue_one(db, template, customer, trigger="follow_up",
                                       ref=_follow_up_ref(customer))
                if row is None:
                    empty += 1
                    continue
                sent += 1
        if sent:
            db.commit()
    except Exception:                                  # noqa: BLE001 — the loop must survive
        logger.exception("Follow-up SMS sweep failed")
        db.rollback()
        return {"sent": 0, "empty": 0}
    return {"sent": sent, "empty": empty}


def trigger_summary(db: Session) -> list[dict]:
    """The opted-in templates, for the manager page's automatic section."""
    out = []
    for key, spec in TRIGGERS_BY_KEY.items():
        for template in triggered_templates(db, key):
            out.append({"template": template, "trigger": spec})
    return out
