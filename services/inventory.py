"""Inventory ledger helpers.

ProductVariant.stock_quantity is kept as a cached balance for existing reports
and fast checkout reads. Every mutation must also append a StockMovement row;
there are deliberately no helpers that delete or edit old movements.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text

from models import Product, ProductVariant, Purchase, PurchaseItem, StockMovement


MOVEMENT_TYPES = {
    "opening_stock",
    "purchase",
    "purchase_reversal",
    "sale",
    "sale_refund",
    "adjustment",
    "cost_adjustment",
}

# Persian labels live here so the ledger page does not branch on raw strings.
MOVEMENT_LABELS = {
    "opening_stock": "موجودی اولیه",
    "purchase": "ورود از خرید",
    "purchase_reversal": "برگشت خرید",
    "sale": "فروش",
    "sale_refund": "برگشت فروش",
    "adjustment": "اصلاح دستی",
    "cost_adjustment": "اصلاح بهای تمام‌شده",
}

# Since purchases stopped moving stock, only historical rows carry this type.
LEGACY_MOVEMENT_TYPES = {"purchase", "purchase_reversal"}

LEDGER_OPENING_NOTE = "ثبت اولیه دفتر انبار — موجودی از پیش موجود"

_MOVEMENT_EVENT_TYPES = {
    "opening_stock": "StockReceived",
    "purchase": "StockReceived",
    "purchase_reversal": "StockReturned",
    "sale": "StockDecremented",
    "sale_refund": "StockReturned",
    "adjustment": "StockAdjusted",
    "cost_adjustment": "StockCostAdjusted",
}


# A variant at or below this many sellable units is worth reordering. It lives
# here, not inside a page's view function, because two pages ask the question.
LOW_STOCK_THRESHOLD = 2


def sellable_expression():
    """Sellable units per variant as a SQL expression: stock minus reservations.

    The one definition of «how many can be sold». :func:`stock_alerts` counts
    with it and the products list filters and sorts with it — a page that
    re-derives the arithmetic beside this helper is a second definition, and
    the suite fails it.
    """
    return (ProductVariant.stock_quantity
            - func.coalesce(ProductVariant.reserved_quantity, 0))


def stock_alerts(db) -> dict:
    """How much of the catalogue needs reordering, and how much has run out.

    ``low_count`` counts everything at or below the threshold *including* the
    ones already at zero, so the two numbers are not meant to be added together:
    ``out_count`` is the more urgent part of ``low_count``, which is why the
    dashboard shows it as the detail rather than as a second total.

    The products page prints these same two numbers, so it calls this too — a
    dashboard count that disagrees with the page it links to is worse than no
    count at all.
    """
    sellable = sellable_expression()
    scope = (db.query(ProductVariant)
             .join(Product)
             .filter(Product.is_active == True, ProductVariant.is_active == True))  # noqa: E712
    return {
        "low_count": scope.filter(sellable <= LOW_STOCK_THRESHOLD).count(),
        "out_count": scope.filter(sellable <= 0).count(),
        "threshold": LOW_STOCK_THRESHOLD,
    }


def movement_type_label(movement_type: str) -> str:
    return MOVEMENT_LABELS.get(movement_type, movement_type)


def movement_direction(quantity_delta: int) -> str:
    """``in`` / ``out`` / ``zero`` — cost rows move no quantity but still matter."""
    delta = int(quantity_delta or 0)
    if delta > 0:
        return "in"
    if delta < 0:
        return "out"
    return "zero"


def record_stock_movement(
    db,
    variant: ProductVariant,
    quantity_delta: int,
    movement_type: str,
    *,
    unit_cost: int | None = None,
    purchase_id: int | None = None,
    sale_id: int | None = None,
    note: str | None = None,
    actor_user_id: int | None = None,
    request_id: str | None = None,
) -> StockMovement:
    """Append one movement and update the cached variant balance atomically."""
    if movement_type not in MOVEMENT_TYPES:
        raise ValueError(f"Unknown stock movement type: {movement_type}")
    quantity_delta = int(quantity_delta)
    new_balance = (variant.stock_quantity or 0) + quantity_delta
    if new_balance < 0:
        raise ValueError("Stock cannot become negative")

    variant.stock_quantity = new_balance
    movement = StockMovement(
        variant_id=variant.id,
        quantity_delta=quantity_delta,
        movement_type=movement_type,
        unit_cost=unit_cost,
        purchase_id=purchase_id,
        sale_id=sale_id,
        note=(note or "")[:500] or None,
    )
    db.add(movement)
    db.flush()
    from services.events import append_event
    event_type = _MOVEMENT_EVENT_TYPES[movement_type]
    append_event(
        db,
        event_type,
        "variant",
        variant.id,
        idempotency_key=f"stock-movement:{movement.id}",
        actor_user_id=actor_user_id,
        request_id=request_id,
        payload={
            "movement_id": movement.id,
            "quantity_delta": quantity_delta,
            "movement_type": movement_type,
            "purchase_id": purchase_id,
            "sale_id": sale_id,
        },
    )
    return movement


class InsufficientStockError(Exception):
    pass


def atomic_decrement_stock(
    db,
    variant_id: int,
    quantity: int,
    *,
    sale_id: int | None = None,
    note: str | None = None,
    actor_user_id: int | None = None,
    request_id: str | None = None,
):
    """Decrement stock with one conditional SQL update and append its ledger row."""
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    result = db.execute(
        text("UPDATE product_variants SET stock_quantity = stock_quantity - :qty, updated_at = :now "
             "WHERE id = :variant_id AND stock_quantity >= :qty"),
        {"qty": quantity, "variant_id": int(variant_id), "now": datetime.now(timezone.utc)},
    )
    if result.rowcount != 1:
        raise InsufficientStockError("Stock changed; please retry")
    movement = StockMovement(
        variant_id=int(variant_id), quantity_delta=-quantity,
        movement_type="sale", sale_id=sale_id, note=(note or "")[:500] or None,
    )
    db.add(movement)
    db.flush()
    from services.events import append_event
    append_event(
        db,
        "StockDecremented",
        "variant",
        int(variant_id),
        idempotency_key=f"stock-movement:{movement.id}",
        actor_user_id=actor_user_id,
        request_id=request_id,
        payload={
            "movement_id": movement.id,
            "quantity_delta": -quantity,
            "sale_id": sale_id,
        },
    )
    return movement


def record_opening_stock(
    db,
    variant: ProductVariant,
    quantity: int,
    note: str = "",
    *,
    actor_user_id: int | None = None,
    request_id: str | None = None,
):
    """Record initial stock for a newly-created variant."""
    if quantity:
        return record_stock_movement(
            db,
            variant,
            quantity,
            "opening_stock",
            unit_cost=variant.cost_price,
            note=note,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
    return None


def record_cost_adjustment(
    db,
    variant: ProductVariant,
    old_cost: int,
    new_cost: int,
    note: str = "",
    *,
    actor_user_id: int | None = None,
    request_id: str | None = None,
    purchase_id: int | None = None,
):
    """Record a cost-basis edit without changing stock or sale history.

    A purchase uses this too: buying stock updates what the goods cost, while
    where the stock itself is counted is decided on the product screens.
    """
    if int(old_cost or 0) == int(new_cost or 0):
        return None
    movement = StockMovement(
        variant_id=variant.id,
        quantity_delta=0,
        movement_type="cost_adjustment",
        unit_cost=max(0, int(new_cost or 0)),
        purchase_id=purchase_id,
        note=(note or "")[:500] or None,
    )
    db.add(movement)
    db.flush()
    from services.events import append_event
    append_event(
        db,
        "StockCostAdjusted",
        "variant",
        variant.id,
        idempotency_key=f"stock-movement:{movement.id}",
        actor_user_id=actor_user_id,
        request_id=request_id,
        payload={
            "movement_id": movement.id,
            "old_cost": old_cost,
            "new_cost": new_cost,
            "purchase_id": purchase_id,
        },
    )
    return movement


def record_stock_adjustment(
    db,
    variant: ProductVariant,
    new_quantity: int,
    note: str = "",
    *,
    actor_user_id: int | None = None,
    request_id: str | None = None,
):
    """Replace a displayed balance through a signed, auditable adjustment."""
    new_quantity = int(new_quantity)
    if new_quantity < 0:
        raise ValueError("Stock cannot become negative")
    if new_quantity < (variant.reserved_quantity or 0):
        raise ValueError("Stock cannot be lower than reserved quantity")
    delta = new_quantity - (variant.stock_quantity or 0)
    if not delta:
        return None
    return record_stock_movement(
        db,
        variant,
        delta,
        "adjustment",
        note=note,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )


def latest_active_purchase_cost(db, variant_id: int) -> int | None:
    """Return the latest non-reversed purchase cost for a variant.

    Reads the purchase lines (not stock movements), because a purchase records
    money and cost only and no longer writes a stock movement. Drafts have not
    applied a cost yet, so they never count.
    """
    line = (
        db.query(PurchaseItem)
        .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
        .filter(
            PurchaseItem.variant_id == variant_id,
            PurchaseItem.landed_unit_cost.isnot(None),
            Purchase.is_reversed == False,
            Purchase.is_draft == False,
        )
        .order_by(func.coalesce(Purchase.purchase_date, Purchase.created_at).desc(), Purchase.id.desc(), PurchaseItem.id.desc())
        .first()
    )
    return line.landed_unit_cost if line else None


def restore_cost_after_purchase_reversal(db, variant: ProductVariant, fallback: int | None = None):
    """Recompute current cost without changing historical SaleItem costs."""
    cost = latest_active_purchase_cost(db, variant.id)
    if cost is not None:
        variant.cost_price = cost
    elif fallback is not None:
        variant.cost_price = fallback


def ledger_snapshot(db, variant_ids) -> dict:
    """Balances and totals derived from the ledger alone.

    Returns ``{"by_movement": {movement_id: balance_after}, "totals":
    {variant_id: ledger_total}}``. Balances are a running sum of the deltas of
    that variant's movements in id order, so they stay internally consistent
    even when a variant's stock predates the ledger.

    ``stock_quantity`` is deliberately not used as the anchor: the point of this
    view is to expose where the ledger and the cached balance disagree, so
    anchoring it would hide exactly what it exists to show.
    """
    variant_ids = [int(v) for v in dict.fromkeys(variant_ids or [])]
    result = {"by_movement": {}, "totals": {}}
    if not variant_ids:
        return result
    rows = db.query(
        StockMovement.id, StockMovement.variant_id, StockMovement.quantity_delta,
    ).filter(
        StockMovement.variant_id.in_(variant_ids),
    ).order_by(StockMovement.variant_id.asc(), StockMovement.id.asc()).all()

    running = 0
    current_variant = None
    for movement_id, variant_id, delta in rows:
        if variant_id != current_variant:
            current_variant = variant_id
            running = 0
        running += int(delta or 0)
        result["by_movement"][movement_id] = running
        result["totals"][variant_id] = running
    for variant_id in variant_ids:
        result["totals"].setdefault(variant_id, 0)
    return result


def ledger_missing_variants(db) -> list[ProductVariant]:
    """Active variants holding stock that the ledger has never seen.

    These are the rows a shop accumulates before the ledger existed (or before a
    variant's stock was ever edited through a recorded flow).
    """
    return db.query(ProductVariant).filter(
        ProductVariant.is_active == True,
        ProductVariant.stock_quantity > 0,
        ProductVariant.id.notin_(select(StockMovement.variant_id)),
    ).order_by(ProductVariant.product_id.asc(), ProductVariant.id.asc()).all()


def ledger_mismatched_variants(db) -> list[tuple[ProductVariant, int]]:
    """Active variants whose ledger total differs from the cached balance."""
    ledger = db.query(
        StockMovement.variant_id.label("variant_id"),
        func.coalesce(func.sum(StockMovement.quantity_delta), 0).label("ledger_total"),
    ).group_by(StockMovement.variant_id).subquery()
    rows = db.query(ProductVariant, ledger.c.ledger_total).join(
        ledger, ledger.c.variant_id == ProductVariant.id,
    ).filter(
        ProductVariant.is_active == True,
        ProductVariant.stock_quantity != ledger.c.ledger_total,
    ).order_by(ProductVariant.product_id.asc(), ProductVariant.id.asc()).all()
    return [(variant, int(total or 0)) for variant, total in rows]


def record_ledger_opening(
    db,
    variant: ProductVariant,
    *,
    actor_user_id: int | None = None,
    request_id: str | None = None,
) -> StockMovement | None:
    """Explain stock that existed before the ledger, without changing it.

    Used by the opt-in reconciliation on the inventory-ledger page: a variant
    that holds stock but has no history gets one ``opening_stock`` row whose
    delta accounts for the balance the shop already has. ``stock_quantity`` is
    left untouched — going through :func:`record_stock_movement` here would add
    the units a second time, which is the double count this ledger exists to
    expose. Idempotent: a variant with any history is skipped.
    """
    quantity = int(variant.stock_quantity or 0)
    if quantity <= 0:
        return None
    if db.query(StockMovement.id).filter(StockMovement.variant_id == variant.id).first():
        return None

    movement = StockMovement(
        variant_id=variant.id,
        quantity_delta=quantity,
        movement_type="opening_stock",
        unit_cost=variant.cost_price,
        note=LEDGER_OPENING_NOTE,
    )
    db.add(movement)
    db.flush()
    from services.events import append_event
    append_event(
        db,
        "StockReceived",
        "variant",
        variant.id,
        idempotency_key=f"stock-movement:{movement.id}",
        actor_user_id=actor_user_id,
        request_id=request_id,
        payload={
            "movement_id": movement.id,
            "quantity_delta": quantity,
            "movement_type": "opening_stock",
            "purchase_id": None,
            "sale_id": None,
            "ledger_opening": True,
        },
    )
    return movement
