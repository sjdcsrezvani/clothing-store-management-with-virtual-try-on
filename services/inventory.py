"""Inventory ledger helpers.

ProductVariant.stock_quantity is kept as a cached balance for existing reports
and fast checkout reads. Every mutation must also append a StockMovement row;
there are deliberately no helpers that delete or edit old movements.
"""
from __future__ import annotations

from models import ProductVariant, Purchase, StockMovement


MOVEMENT_TYPES = {
    "opening_stock",
    "purchase",
    "purchase_reversal",
    "sale",
    "sale_refund",
    "adjustment",
    "cost_adjustment",
}


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
    return movement


def record_opening_stock(db, variant: ProductVariant, quantity: int, note: str = ""):
    """Record initial stock for a newly-created variant."""
    if quantity:
        return record_stock_movement(
            db, variant, quantity, "opening_stock", unit_cost=variant.cost_price, note=note,
        )
    return None


def record_cost_adjustment(db, variant: ProductVariant, old_cost: int, new_cost: int, note: str = ""):
    """Record a cost-basis edit without changing stock or sale history."""
    if int(old_cost or 0) == int(new_cost or 0):
        return None
    movement = StockMovement(
        variant_id=variant.id,
        quantity_delta=0,
        movement_type="cost_adjustment",
        unit_cost=max(0, int(new_cost or 0)),
        note=(note or "")[:500] or None,
    )
    db.add(movement)
    return movement


def record_stock_adjustment(db, variant: ProductVariant, new_quantity: int, note: str = ""):
    """Replace a displayed balance through a signed, auditable adjustment."""
    new_quantity = max(0, int(new_quantity))
    delta = new_quantity - (variant.stock_quantity or 0)
    if not delta:
        return None
    return record_stock_movement(db, variant, delta, "adjustment", note=note)


def latest_active_purchase_cost(db, variant_id: int) -> int | None:
    """Return the latest non-reversed purchase cost for a variant."""
    movement = (
        db.query(StockMovement)
        .join(Purchase, StockMovement.purchase_id == Purchase.id)
        .filter(
            StockMovement.variant_id == variant_id,
            StockMovement.movement_type == "purchase",
            StockMovement.quantity_delta > 0,
            StockMovement.unit_cost.isnot(None),
            Purchase.is_reversed == False,
        )
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .first()
    )
    return movement.unit_cost if movement else None


def restore_cost_after_purchase_reversal(db, variant: ProductVariant, fallback: int | None = None):
    """Recompute current cost without changing historical SaleItem costs."""
    cost = latest_active_purchase_cost(db, variant.id)
    if cost is not None:
        variant.cost_price = cost
    elif fallback is not None:
        variant.cost_price = fallback
