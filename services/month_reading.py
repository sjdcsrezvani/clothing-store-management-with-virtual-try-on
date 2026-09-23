"""The month's reading, said once and said the same way everywhere.

سود و زیان already knows how to say what happened in a period: one sentence per
chart, built from the very figures the chart draws. Those sentences live on a
page the owner opens on purpose. But a month ends whether or not anybody opens
the page, so the same sentences are read in two more places:

* **The dashboard's «روایت این ماه»** — a strip of prose under the month cards,
  fed from the *same* ``month_reading`` call the SMS is built from, so the page
  and the phone can never disagree about what the month said. It is owner-only:
  the sentences name profit, margins and customer money, which are the owner's
  figures, and the strip is simply never computed for a manager.
* **The owner's monthly SMS** — the month's reading, and the reading alone,
  condensed to what one text message holds. It fires shortly after the Persian
  month turns and speaks of the month that *finished*: a summary of a month
  still running would be half a story, and the dashboard strip already covers
  the month so far. The first month it can describe is the first complete one.
  On the پیامک page the owner sees that text before opting in — the preview is
  composed by the same ``compose_digest`` the send uses, from last month's own
  figures, so what is shown is what would be sent.

Three disciplines, all inherited from the automatic triggers this sits beside:

1. **Nothing sends without the owner opting in** — a phone in the settings is
   the opt-in. With no phone, the scheduler's pass is a cheap query that finds
   nothing to do.
2. **One message per month.** The digest records its month in
   ``sms_messages.ref`` (``digest:1404-06``), and a pass that finds that ref
   already logged stays quiet — so a retried scheduler, a restart, or an owner
   pressing save twice cannot text the same month twice.
3. **The scheduler never fails because of the digest.** It swallows its own
   errors and reports what happened.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import jdatetime
from sqlalchemy.orm import Session

from models import SmsMessage
from services._common import JALALI_MONTHS, jalali_month_start
from services.chart_notes import chart_notes
from services.analytics import (
    get_daily_revenue,
    get_revenue_by_category,
    get_revenue_by_tier,
)
from services.sms import queue_sms
from services.sms_send import parse_phone_list

logger = logging.getLogger(__name__)

SETTING_DIGEST_PHONE = "monthly_digest_phone"
SETTING_DIGEST_DAY = "monthly_digest_day"
DEFAULT_DIGEST_DAY = 3

# The sentence the phone reads when the month had no sales at all: the same
# fact the analytics page states, with the month's name where the page names
# its range.
EMPTY_MONTH = "در {month} فروشی ثبت نشده."

SOURCE = "monthly_digest"
_LABEL = "خلاصه ماهانه"


def month_name_of(month_start: datetime) -> tuple[str, int]:
    """The Persian month a span opens in, as (name, year) the shop reads."""
    jd = jdatetime.date.fromgregorian(date=month_start.astimezone(timezone.utc).date())
    return JALALI_MONTHS[jd.month - 1], jd.year


def _ref_for(month_start: datetime) -> str:
    name, year = month_name_of(month_start)
    return f"digest:{year}-{JALALI_MONTHS.index(name) + 1:02d}"


# ── the settings ─────────────────────────────────────────────────────────────

def digest_phones(db: Session) -> list[str]:
    """Where the digest goes. Empty means the owner has not opted in."""
    from services.tier import get_setting
    return parse_phone_list(get_setting(db, SETTING_DIGEST_PHONE, ""))


def digest_day(db: Session) -> int:
    """How far into the new month the digest of the last one may fire."""
    from services._common import get_setting_int
    return get_setting_int(db, SETTING_DIGEST_DAY, DEFAULT_DIGEST_DAY)


# ── the reading ──────────────────────────────────────────────────────────────

def month_reading(db: Session, *, start: datetime, end: datetime,
                  span: str) -> dict:
    """The month's sentences, from the figures the analytics page draws.

    One function, two readers: the dashboard strip and the SMS both call this,
    so «روایت این ماه» on the screen and the summary on the phone are the same
    words by construction rather than by anyone remembering to keep them in
    step. Every query here is an aggregate or a page-load-sized walk — the
    daily reader loops day by day, which the analytics page pays on every
    visit and a once-a-month digest certainly can. It runs once per call and
    is shared between the sentences and the digest's own sums.
    """
    daily = get_daily_revenue(db, start, end)
    return {
        "notes": chart_notes(
            daily=daily,
            categories=get_revenue_by_category(db, start, end),
            tier_revenue=get_revenue_by_tier(db, start, end),
            span=span,
        ),
        "daily": daily,
    }


def digest_text(reading: dict, *, month: str, year: int,
                top_products: list[dict] | None = None,
                drifted_counters: int = 0) -> str:
    """The month's SMS: the reading, condensed to what one text holds.

    Four sentences — takings, the day worth naming, what sold, who bought —
    and the best seller, which is the one figure an owner repeats out loud.
    The sentences come from ``reading["notes"]`` unchanged: whatever the
    analytics page says about this data, this text says too.

    The last line, when there is one, is not a month figure at all: the count
    of customers whose stored counters disagree with their invoices — a
    standing wrongness no profile visit may have surfaced, reported monthly
    so the owner hears about it even when nobody opened the page. Silent at
    zero, like every honest figure here.
    """
    notes = reading.get("notes") or {}
    daily = [row for row in (reading.get("daily") or [])
             if float(row.get("revenue") or 0)]
    drift = _drift_sentence(drifted_counters)
    parts: list[str] = [f"گزارش {month} {year}:"]

    if not daily:
        # The reading already said the month was quiet — in its own words. It
        # is reused verbatim so the text cannot hold two spellings of the same
        # fact (its own fallback below only covers a reading that never came).
        parts.append(notes.get("dailyChart") or EMPTY_MONTH.format(month=month))
        # Drift is a standing state, not a month figure: a quiet month is
        # exactly when nobody is looking, so the line rides along here too.
        if drift:
            parts.append(drift)
        return " ".join(parts)

    revenue = sum(float(row.get("revenue") or 0) for row in daily)
    profit = sum(float(row.get("profit") or 0) for row in daily)
    count = sum(int(row.get("count") or 0) for row in daily)
    parts.append(f"فروش {_fmt(revenue)} ت در {count} فاکتور، سود {_fmt(profit)} ت.")

    best = max(daily, key=lambda row: float(row.get("revenue") or 0))
    parts.append(f"بیشترین فروش {best['date']} با {_fmt(best.get('revenue'))} ت.")
    parts.append(notes.get("categoryChart", ""))
    parts.append(notes.get("tierChart", ""))

    top = [row for row in (top_products or []) if float(row.get("qty_sold") or 0)]
    if top:
        first = top[0]
        parts.append(f"پرفروش‌ترین: {first.get('name')} ({int(float(first.get('qty_sold') or 0))} عدد).")
    if drift:
        parts.append(drift)
    return " ".join(part for part in parts if part)


def _drift_sentence(count: int) -> str:
    """The drift line, or nothing: a count of zero states no fact."""
    if count <= 0:
        return ""
    from services._common import _to_persian_digits as to_persian_digits
    return (f"شمارنده‌ی {to_persian_digits(str(count))} مشتری با فاکتورها نمی‌خواند — "
            "از پروفایل مشتری، هم‌ساز کنید.")


def _fmt(amount: float) -> str:
    from services._common import fmt
    return fmt(int(round(float(amount or 0))))


# ── the firing ───────────────────────────────────────────────────────────────

def _finished_month_window(at: datetime) -> tuple[datetime, datetime]:
    """The month that finished before ``at``, as the window the readers take."""
    today = jdatetime.date.fromgregorian(date=at.astimezone(timezone.utc).date())
    # The month that finished is the one before the one we are in.
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    start = jdatetime.date(year, month, 1).togregorian()
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    return start_dt, jalali_month_start(at)  # midnight where the finished month ended


def _expected_digest_period(at: datetime, day: int) -> tuple[datetime, datetime] | None:
    """The finished month the digest should describe today, or None.

    The window opens on the ``day``-th of the new Persian month: day 3 means
    the pass that first runs on or after the third speaks of the month that
    ended. Every earlier pass of that month is a quiet no-op.
    """
    today = jdatetime.date.fromgregorian(date=at.astimezone(timezone.utc).date())
    if today.day < day:
        return None
    return _finished_month_window(at)


def digest_ref(db: Session, *, at: datetime | None = None) -> str:
    """The month the *next* digest may speak of — also the review surface."""
    at = at or datetime.now(timezone.utc)
    period = _expected_digest_period(at, digest_day(db))
    return _ref_for(period[0]) if period else ""


def digest_month_label(db: Session, *, at: datetime | None = None) -> str:
    """The month the next digest describes, in the words the shop reads."""
    at = at or datetime.now(timezone.utc)
    period = _expected_digest_period(at, digest_day(db))
    if period is None:
        return ""
    name, year = month_name_of(period[0])
    from services._common import _to_persian_digits as to_persian_digits
    return f"{name} {to_persian_digits(str(year))}"


def already_fired(db: Session, ref: str) -> bool:
    if not ref:
        return False
    return db.query(SmsMessage).filter(
        SmsMessage.ref == ref,
        SmsMessage.source == SOURCE,
    ).first() is not None


def compose_digest(db: Session, *, start: datetime, end: datetime) -> dict:
    """Everything a digest send needs, composed once from the figures.

    The scheduler's send and the settings page's preview both call this, so
    what the owner reads on the page is exactly what the scheduler will
    compose — one composer, not two that agree by discipline.
    """
    from services.analytics import get_top_products
    from services.customers import drifted_counter_count
    month, year = month_name_of(start)
    reading = month_reading(db, start=start, end=end, span=f"ماه {month}")
    body = digest_text(reading, month=month, year=year,
                       top_products=get_top_products(db, start, end, limit=1),
                       drifted_counters=drifted_counter_count(db))
    return {"ref": _ref_for(start), "month": month, "year": year, "body": body}


def digest_preview(db: Session, *, at: datetime | None = None) -> dict:
    """What the owner is opting into: last month's own text, composed now.

    Same period rule as the send — the month that *finished*, whatever today's
    date — so an owner deciding on the first of a month sees the month that
    just ended, not a notice that the window has not opened yet. It
    deliberately ignores the opt-in and the once-per-month ref: the point is
    the text, not the queue's bookkeeping.
    """
    at = at or datetime.now(timezone.utc)
    start, end = _finished_month_window(at)
    try:
        composed = compose_digest(db, start=start, end=end)
    except Exception:                                  # noqa: BLE001 — a broken reading must not take the page down
        logger.exception("Digest preview failed for %s", start)
        return {"state": "unavailable"}
    return {"state": "ready", **composed}


def digest_month_of_ref(ref: str) -> str:
    """«مرداد ۱۴۰۵» out of a digest row's ref — the name the shop reads.

    The history page labels each digest with its month through this, so the
    owner can find a past month's summary among the log's rows. A ref that
    does not parse (an old row, a hand-edited one) labels nothing rather than
    guessing a month it cannot prove.
    """
    try:
        year_text, month_text = ref.split(":", 1)[1].split("-", 1)
        month = JALALI_MONTHS[int(month_text) - 1]
    except (IndexError, ValueError):
        return ""
    from services._common import _to_persian_digits as to_persian_digits
    return f"{month} {to_persian_digits(year_text)}"


async def fire_monthly_digest(db: Session, *, at: datetime | None = None) -> dict:
    """One scheduler pass: maybe the month turned, maybe it did not.

    There is deliberately no force switch. The once-per-month ref is what
    makes a retried scheduler, a restart or a double-fired pass safe, and a
    switch that could bypass it would put a second text for one month one
    mistake away. A test uses a fresh database or a different month; an owner
    who wants a second copy asks for it from the history page, by hand.
    """
    at = at or datetime.now(timezone.utc)
    phones = digest_phones(db)
    if not phones:
        return {"sent": 0, "skipped": "no_phone"}

    day = digest_day(db)
    period = _expected_digest_period(at, day)
    if period is None:
        return {"sent": 0, "skipped": "not_due"}

    start, end = period
    ref = _ref_for(start)
    if already_fired(db, ref):
        return {"sent": 0, "skipped": "already_sent"}

    try:
        composed = compose_digest(db, start=start, end=end)
        body = composed["body"]
        if not body.strip():
            return {"sent": 0, "skipped": "empty"}

        sent = 0
        for phone in phones:
            row = await queue_sms(body, phone, {}, db, source=SOURCE,
                                  body=body, ref=ref)
            if row is not None:
                sent += 1
        if sent:
            db.commit()
        else:
            db.rollback()
        return {"sent": sent, "ref": ref}
    except Exception:                                  # noqa: BLE001 — the loop must survive
        logger.exception("Monthly digest failed for %s", ref)
        db.rollback()
        return {"sent": 0, "skipped": "error"}


async def send_digest_now(db: Session, *, at: datetime | None = None) -> dict:
    """Queue last month's digest by hand — the «ارسال همین حالا» button.

    The calendar gate is the scheduler's alone: it paces the automatic send to
    the owner's chosen day, while a human pressing the button has already
    decided the time is right. What is *not* the scheduler's alone is the
    once-per-month ref — the same ``already_fired`` check stands here, so this
    send and the scheduler's send cannot both speak for one month. Whichever
    happens first covers the month; the other becomes a quiet no-op.
    """
    at = at or datetime.now(timezone.utc)
    phones = digest_phones(db)
    if not phones:
        return {"queued": 0, "state": "no_phone"}
    start, end = _finished_month_window(at)
    ref = _ref_for(start)
    if already_fired(db, ref):
        return {"queued": 0, "state": "already_sent", "ref": ref}
    try:
        composed = compose_digest(db, start=start, end=end)
        body = composed["body"]
        if not body.strip():
            return {"queued": 0, "state": "empty", "ref": ref}
        queued = 0
        for phone in phones:
            row = await queue_sms(body, phone, {}, db, source=SOURCE,
                                  body=body, ref=ref)
            if row is not None:
                queued += 1
        if queued:
            db.commit()
        else:
            db.rollback()
        return {"queued": queued,
                "state": "queued" if queued else "failed",
                "ref": ref, "month": composed["month"]}
    except Exception:                                  # noqa: BLE001 — a broken reading must not take the page down
        logger.exception("Hand digest send failed for %s", ref)
        db.rollback()
        return {"queued": 0, "state": "error", "ref": ref}
