"""Server-owned checkout lifecycle: state machine, basket reservations, and
atomic stock decrements.

The browser sends only a checkout nonce (idempotency key) and a list of variant
IDs. Prices, discounts, credit surcharge, and the final terminal amount are all
computed server-side here so a client can never send one amount to the terminal
and later confirm a different basket.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from models import (
    CheckoutEvent,
    CheckoutSession,
    CHECKOUT_STATES,
    CHECKOUT_TRANSITIONS,
    Customer,
    ProductVariant,
    StockReservation,
    StockMovement,
)

RESERVATION_TIMEOUT_MINUTES = 15
CHECKOUT_TIMEOUT_MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _checkout_expiry() -> datetime:
    return _now() + timedelta(minutes=CHECKOUT_TIMEOUT_MINUTES)


def _reservation_expiry() -> datetime:
    return _now() + timedelta(minutes=RESERVATION_TIMEOUT_MINUTES)


def transition_allowed(from_state: str | None, to_state: str) -> bool:
    """True if moving from ``from_state`` to ``to_state`` is a valid transition."""
    if to_state not in CHECKOUT_STATES:
        return False
    if from_state is None:
        return to_state == "draft"
    return to_state in CHECKOUT_TRANSITIONS.get(from_state, set())


def _record_event(db, checkout: CheckoutSession, to_state: str, detail: str = "") -> None:
    db.add(CheckoutEvent(
        checkout_session_id=checkout.id,
        from_state=checkout.state,
        to_state=to_state,
        detail=(detail or "")[:500] or None,
    ))


def _set_state(db, checkout: CheckoutSession, to_state: str, detail: str = "") -> None:
    if not transition_allowed(checkout.state, to_state):
        raise ValueError(f"Invalid checkout transition: {checkout.state} → {to_state}")
    _record_event(db, checkout, to_state, detail)
    checkout.state = to_state


def create_checkout(
    db: Session,
    *,
    customer_id: int | None = None,
    staff_user_id: int | None = None,
    basket_json: str = "[]",
    payment_method: str = "card",
) -> CheckoutSession:
    """Create a fresh draft checkout with a server-generated nonce."""
    checkout = CheckoutSession(
        checkout_nonce=secrets.token_urlsafe(18),
        customer_id=customer_id or None,
        staff_user_id=staff_user_id,
        basket_json=basket_json,
        payment_method=payment_method,
        state="draft",
        expires_at=_checkout_expiry(),
    )
    db.add(checkout)
    db.flush()
    _record_event(db, checkout, "draft", "checkout created")
    db.commit()
    db.refresh(checkout)
    return checkout


def get_checkout(db: Session, checkout_nonce: str) -> CheckoutSession | None:
    return db.query(CheckoutSession).filter(
        CheckoutSession.checkout_nonce == checkout_nonce,
    ).first()


def update_checkout_options(
    db: Session,
    checkout: CheckoutSession,
    *,
    customer_id: int | None = None,
    basket_json: str = "[]",
    payment_method: str = "card",
    use_referrer_discount: bool = True,
    custom_discount_amount: int = 0,
    custom_discount_percent: int = 0,
    referrer_code: str = "",
    referrer_phone: str = "",
) -> CheckoutSession:
    """Persist the current checkout inputs before calculating its amount."""
    checkout.customer_id = customer_id or None
    checkout.basket_json = basket_json
    checkout.payment_method = payment_method
    checkout.use_referrer_discount = bool(use_referrer_discount)
    checkout.custom_discount_amount = max(0, int(custom_discount_amount or 0))
    checkout.custom_discount_percent = min(100, max(0, int(custom_discount_percent or 0)))
    checkout.referrer_code = (referrer_code or "")[:50] or None
    checkout.referrer_phone = (referrer_phone or "")[:20] or None
    db.flush()
    return checkout


def is_expired(checkout: CheckoutSession) -> bool:
    return checkout.expires_at < _now()


def expire_stale(db: Session) -> int:
    """Expire checkouts past their timeout and release their reservations.

    Called by the scheduler and before every checkout read so abandoned
    baskets don't hold stock forever."""
    now = _now()
    stale = db.query(CheckoutSession).filter(
        CheckoutSession.expires_at < now,
        CheckoutSession.state.in_(("draft", "reserved", "payment_pending",
                                    "payment_approved", "payment_uncertain")),
    ).all()
    count = 0
    for checkout in stale:
        release_reservations(db, checkout)
        if transition_allowed(checkout.state, "expired"):
            _set_state(db, checkout, "expired", "timeout")
            count += 1
    if count:
        db.commit()
    return count


def reserve_basket(db: Session, checkout: CheckoutSession, items: list[dict]) -> list[StockReservation]:
    """Create or refresh stock reservations for the basket items.

    Each item is ``{"variant_id": int, "quantity": int}``. Reservations are
    exclusive to this checkout session; the same variant in another active
    checkout cannot double-reserve the same units."""
    if checkout.state not in {"draft", "reserved", "payment_pending", "payment_approved"}:
        raise ValueError(f"Cannot reserve from state {checkout.state}")

    # Release any prior reservations for this checkout first (re-scan).
    release_reservations(db, checkout)
    db.flush()

    now = _now()
    exp = _reservation_expiry()
    reservations: list[StockReservation] = []
    for item in items:
        variant_id = int(item.get("variant_id") or 0)
        qty = max(1, int(item.get("quantity") or 1))
        if not variant_id:
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue

        already_reserved = sum(
            r.quantity for r in db.query(StockReservation).filter(
                StockReservation.variant_id == variant_id,
                StockReservation.state == "active",
                StockReservation.expires_at > now,
                StockReservation.checkout_session_id != checkout.id,
            ).all()
        )
        available = (variant.stock_quantity or 0) - already_reserved
        if available < qty:
            raise InsufficientStockError(
                f"موجودی {variant.display_name} کافی نیست (دسترس: {available})."
            )

        variant.reserved_quantity = (variant.reserved_quantity or 0) + qty
        reservation = StockReservation(
            checkout_session_id=checkout.id,
            variant_id=variant_id,
            quantity=qty,
            session_id=checkout.checkout_nonce,
            state="active",
            expires_at=exp,
        )
        db.add(reservation)
        reservations.append(reservation)

    if checkout.state == "draft":
        _set_state(db, checkout, "reserved", "basket reserved")
    db.commit()
    db.refresh(checkout)
    return reservations


def release_reservations(db: Session, checkout: CheckoutSession) -> int:
    """Mark all active reservations for this checkout as released."""
    now = _now()
    released = db.query(StockReservation).filter(
        StockReservation.checkout_session_id == checkout.id,
        StockReservation.state == "active",
    ).all()
    for r in released:
        variant = db.query(ProductVariant).filter(ProductVariant.id == r.variant_id).first()
        if variant:
            variant.reserved_quantity = max(0, (variant.reserved_quantity or 0) - r.quantity)
        r.state = "released"
        r.released_at = now
    return len(released)


def consume_reservations(db: Session, checkout: CheckoutSession) -> None:
    """Mark reservations as consumed after a completed sale."""
    for r in db.query(StockReservation).filter(
        StockReservation.checkout_session_id == checkout.id,
        StockReservation.state == "active",
    ).all():
        variant = db.query(ProductVariant).filter(ProductVariant.id == r.variant_id).first()
        if variant:
            variant.reserved_quantity = max(0, (variant.reserved_quantity or 0) - r.quantity)
        r.state = "consumed"


def finalize_basket(db: Session, checkout: CheckoutSession) -> dict:
    """Recompute the basket from the database and store the server-owned
    total, discounts, and final amount on the checkout row.

    Returns a dict with the verified basket and computed totals. The browser-
    supplied basket is treated as an untrusted list of variant IDs and
    quantities; price is always read from the variant."""
    from services.discount import calculate_discounts
    from services.accounting import apply_credit_surcharge, credit_sale_allowed
    raw_basket = json.loads(checkout.basket_json or "[]")
    verified: list[dict] = []
    for item in raw_basket:
        try:
            variant_id = int(item.get("variant_id"))
            requested_qty = max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue
        # Clamp to current stock minus other active reservations.
        now = _now()
        reserved_elsewhere = sum(
            r.quantity for r in db.query(StockReservation).filter(
                StockReservation.variant_id == variant.id,
                StockReservation.state == "active",
                StockReservation.expires_at > now,
                StockReservation.checkout_session_id != checkout.id,
            ).all()
        )
        available = max(0, (variant.stock_quantity or 0) - reserved_elsewhere)
        qty = min(requested_qty, available)
        if qty <= 0:
            continue
        unit_price = variant.price
        verified.append({
            "variant_id": variant.id,
            "product_id": variant.product_id,
            "name": variant.display_name,
            "unit_price": unit_price,
            "unit_cost": variant.cost_price,
            "quantity": qty,
            "total_price": unit_price * qty,
        })

    if not verified:
        raise InsufficientStockError("هیچ محصول معتبری در سبد نیست.")

    total_amount = sum(it["total_price"] for it in verified)
    customer = None
    if checkout.customer_id:
        customer = db.query(Customer).filter(Customer.id == checkout.customer_id).first()

    referrer = None
    if customer:
        if checkout.referrer_code or checkout.referrer_phone:
            from routers.sales import _resolve_referrer, _grant_referred_discount
            referrer = _resolve_referrer(checkout.referrer_code or "", checkout.referrer_phone or "", db)
            _grant_referred_discount(referrer, customer, db)
    discounts = calculate_discounts(
        customer,
        total_amount,
        db,
        use_referrer_discount=checkout.use_referrer_discount,
        custom_amount=checkout.custom_discount_amount,
        custom_percent=checkout.custom_discount_percent,
    )

    credit_surcharge = 0
    if checkout.payment_method == "credit":
        for k in ("referred_discount", "referrer_discount", "tier_discount",
                  "birthday_discount", "custom_discount"):
            discounts[k] = 0
        discounts["details"] = ["فروش نسیه: تخفیف اعمال نمی‌شود"]
        discounts["total_discount"] = 0
        credit_surcharge, final_amount = apply_credit_surcharge(db, total_amount, 0)
        discounts["details"].append(f"افزایش نسیه: {credit_surcharge:,} تومان")
    else:
        final_amount = total_amount - discounts["total_discount"]

    checkout.total_amount = total_amount
    checkout.discount_amount = discounts["total_discount"]
    checkout.credit_surcharge = credit_surcharge
    checkout.final_amount = final_amount
    checkout.basket_json = json.dumps(verified, ensure_ascii=False)
    db.flush()
    return {
        "basket": verified,
        "total_amount": total_amount,
        "discounts": discounts,
        "credit_surcharge": credit_surcharge,
        "final_amount": final_amount,
    }


def atomic_decrement_stock(
    db: Session,
    variant_id: int,
    quantity: int,
    *,
    sale_id: int | None = None,
    checkout: CheckoutSession | None = None,
    note: str | None = None,
) -> StockMovement:
    """Decrement variant stock atomically.

    Issues a single ``UPDATE ... WHERE stock_quantity >= quantity`` and raises
    ``InsufficientStockError`` when zero rows are affected — meaning another
    concurrent checkout already took the stock. No partial sale or movement is
    created when this fails."""
    held = []
    if checkout is not None:
        held = db.query(StockReservation).filter(
            StockReservation.checkout_session_id == checkout.id,
            StockReservation.variant_id == int(variant_id),
            StockReservation.state == "active",
        ).all()
        held_quantity = sum(r.quantity for r in held)
        if held_quantity < int(quantity):
            db.rollback()
            raise InsufficientStockError("Checkout reservation is no longer valid; please retry")
        for reservation in held:
            variant = db.query(ProductVariant).filter(ProductVariant.id == reservation.variant_id).first()
            if variant:
                variant.reserved_quantity = max(0, (variant.reserved_quantity or 0) - reservation.quantity)
            reservation.state = "consumed"
        db.flush()

    result = db.execute(
        text(
            "UPDATE product_variants "
            "SET stock_quantity = stock_quantity - :qty, "
            "updated_at = :now "
            "WHERE id = :vid AND stock_quantity >= :qty"
        ),
        {"qty": int(quantity), "vid": int(variant_id), "now": _now()},
    )
    if result.rowcount != 1:
        db.rollback()
        raise InsufficientStockError(
            f"Stock changed for variant {variant_id}; please retry."
        )

    movement = StockMovement(
        variant_id=int(variant_id),
        quantity_delta=-int(quantity),
        movement_type="sale",
        sale_id=sale_id,
        note=(note or "")[:500] or None,
    )
    db.add(movement)
    db.flush()
    # Refresh the variant so subsequent reads see the committed balance.
    db.expire(db.query(ProductVariant).filter(ProductVariant.id == variant_id).first())
    return movement


def set_payment_pending(db: Session, checkout: CheckoutSession) -> None:
    if checkout.state == "payment_pending":
        return
    if checkout.state == "draft":
        _set_state(db, checkout, "reserved", "basket reserved before payment")
    _set_state(db, checkout, "payment_pending", "pos payment initiated")
    db.commit()


def set_payment_outcome(db: Session, checkout: CheckoutSession, outcome: str, detail: str = "") -> None:
    """Record the terminal response. ``outcome`` is one of: approved,
    cancelled, declined, uncertain."""
    state_map = {
        "approved": "payment_approved",
        "cancelled": "payment_cancelled",
        "declined": "payment_declined",
        "uncertain": "payment_uncertain",
    }
    to_state = state_map.get(outcome)
    if not to_state:
        raise ValueError(f"Unknown payment outcome: {outcome}")
    _set_state(db, checkout, to_state, detail)
    db.commit()


def complete_checkout(db: Session, checkout: CheckoutSession, sale_id: int) -> None:
    """Mark the checkout as completed and consume its reservations."""
    if checkout.state == "draft":
        _set_state(db, checkout, "reserved", "basket reserved before completion")
    if checkout.state == "reserved":
        _set_state(db, checkout, "completed", f"sale #{sale_id}")
    else:
        _set_state(db, checkout, "completed", f"sale #{sale_id}")
    checkout.sale_id = sale_id
    consume_reservations(db, checkout)
    db.commit()


def refund_checkout(db: Session, checkout: CheckoutSession) -> None:
    """Move a completed checkout to refunded."""
    _set_state(db, checkout, "refunded", "sale refunded")
    db.commit()


class InsufficientStockError(Exception):
    """Raised when stock is insufficient for an atomic decrement or reservation."""
    pass
