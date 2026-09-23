"""Terminal attempts whose money nobody has confirmed yet.

A card-terminal attempt is only *settled* once it is either linked to a sale or
marked reconciled by hand; ``cancelled`` and ``declined`` are answers the
terminal gave us. Everything in between is money of unknown fate, and a shop
needs one number for it.

The definition lives here once because two pages print it — the reconciliation
page's own KPI and the dashboard's «نیازمند توجه» count — and a count that
disagrees with the page it links to is worse than no count at all.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import POSTransaction

# The states a terminal can sit in when the outcome is genuinely unknown.
UNRESOLVED_STATUSES = ("created", "sent", "uncertain", "approved")


def unresolved_transactions(db: Session) -> int:
    """Every attempt the shop has not settled, counted over the whole table.

    Deliberately not a window. The page *lists* the newest 200 because a human
    has to read them, but this is a count of what is outstanding, so a shop with
    more than 200 unresolved attempts must not be told it has fewer.
    """
    return (
        db.query(POSTransaction)
        .filter(
            POSTransaction.status.in_(UNRESOLVED_STATUSES),
            POSTransaction.sale_id.is_(None),
            POSTransaction.reconciled == False,  # noqa: E712
        )
        .count()
    )
