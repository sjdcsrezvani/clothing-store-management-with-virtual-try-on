"""The month's reading: the prose strip under the dashboard's month cards.

سود و زیان already knows how to say what happened in a period: one sentence per
chart, built from the very figures the chart draws. The dashboard reuses those
sentences through ``month_reading``, so the strip and the page cannot disagree
about what the month said. It is owner-only: the sentences name profit, margins
and customer money, which are the owner's figures, and the strip is simply never
computed for a manager.

The owner's monthly SMS digest used to be composed from the same reading. It is
gone; the reading has one reader again. Rows it left in the SMS log keep their
month's name through ``digest_month_of_ref`` — a label, never a send.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from services._common import JALALI_MONTHS
from services.chart_notes import chart_notes
from services.analytics import (
    get_daily_revenue,
    get_revenue_by_category,
    get_revenue_by_tier,
)


def month_reading(db: Session, *, start: datetime, end: datetime,
                  span: str) -> dict:
    """The month's sentences, from the figures the analytics page draws.

    Every query here is an aggregate or a page-load-sized walk — the daily
    reader loops day by day, which the analytics page pays on every visit and
    the owner's dashboard pays once per visit. The same call feeds the strip's
    sentences and its own sums.
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


def digest_month_of_ref(ref: str) -> str:
    """«مرداد ۱۴۰۵» out of a historical digest row's ref.

    Only the log reads this now: the monthly SMS is gone, but the rows it left
    behind keep their month's name instead of a raw ``digest:1405-05``. A ref
    that does not parse labels nothing rather than guessing.
    """
    try:
        year_text, month_text = ref.split(":", 1)[1].split("-", 1)
        month = JALALI_MONTHS[int(month_text) - 1]
    except (IndexError, ValueError):
        return ""
    from services._common import _to_persian_digits as to_persian_digits
    return f"{month} {to_persian_digits(year_text)}"
