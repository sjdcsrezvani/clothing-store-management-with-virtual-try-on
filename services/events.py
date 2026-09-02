from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from models import (
    BUSINESS_EVENT_TYPES,
    BusinessEvent,
    CashSession,
    CheckoutSession,
    Expense,
    Payment,
    PaymentReversal,
    POSTransaction,
    Purchase,
    Refund,
    Sale,
    StockMovement,
    StockReservation,
    SupplierPayment,
)

_MAX_PAYLOAD_LENGTH = 12000
_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "card_number",
    "pan",
    "cvv",
    "response_text",
    "raw_response",
    "response_body",
    "approval_token",
    "session_token",
    "nonce",
    "cookie",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _is_sensitive_key(str(key)) else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def normalize_payload(payload: dict[str, Any] | None) -> str:
    safe = _safe_value(payload or {})
    encoded = json.dumps(safe, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(encoded) <= _MAX_PAYLOAD_LENGTH:
        return encoded
    return json.dumps({
        "truncated": True,
        "original_size": len(encoded),
    }, separators=(",", ":"))


def append_event(
    db: Session,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int | None = None,
    *,
    idempotency_key: str | None = None,
    actor_user_id: int | None = None,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> BusinessEvent:
    """Append one event to the current transaction.

    A repeated idempotency key returns the original row. The unique database
    constraint also protects the journal when two writers race.
    """
    if event_type not in BUSINESS_EVENT_TYPES:
        raise ValueError(f"Unknown business event type: {event_type}")
    if not aggregate_type or len(aggregate_type) > 50:
        raise ValueError("aggregate_type is required")
    if aggregate_id is not None and int(aggregate_id) <= 0:
        raise ValueError("aggregate_id must be positive")

    raw_key = idempotency_key or f"{event_type}:{aggregate_type}:{aggregate_id or 'none'}"
    if len(raw_key) > 200:
        key = raw_key[:160] + ":" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:39]
    else:
        key = raw_key
    existing = db.query(BusinessEvent).filter(BusinessEvent.idempotency_key == key).first()
    if existing:
        return existing

    values = {
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": int(aggregate_id) if aggregate_id is not None else None,
        "idempotency_key": key,
        "actor_user_id": actor_user_id,
        "request_id": (request_id or "")[:100] or None,
        "payload": normalize_payload(payload),
        "schema_version": 1,
        "occurred_at": occurred_at or _now(),
    }
    db.execute(insert(BusinessEvent).prefix_with("OR IGNORE").values(**values))
    return db.query(BusinessEvent).filter(BusinessEvent.idempotency_key == key).one()


def event_payload(event: BusinessEvent) -> dict[str, Any]:
    try:
        value = json.loads(event.payload or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def event_history(
    db: Session,
    aggregate_type: str | None = None,
    aggregate_id: int | None = None,
    *,
    event_type: str | None = None,
    limit: int = 200,
) -> list[BusinessEvent]:
    query = db.query(BusinessEvent)
    if aggregate_type:
        query = query.filter(BusinessEvent.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        query = query.filter(BusinessEvent.aggregate_id == int(aggregate_id))
    if event_type:
        query = query.filter(BusinessEvent.event_type == event_type)
    bounded_limit = max(1, min(int(limit or 200), 1000))
    return query.order_by(BusinessEvent.occurred_at.asc(), BusinessEvent.id.asc()).limit(bounded_limit).all()


def event_counts(
    db: Session,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, int]:
    query = db.query(BusinessEvent.event_type)
    if start is not None:
        query = query.filter(BusinessEvent.occurred_at >= start)
    if end is not None:
        query = query.filter(BusinessEvent.occurred_at <= end)
    return dict(Counter(row[0] for row in query.all()))


def _event_exists(db: Session, key: str) -> bool:
    return db.query(BusinessEvent.id).filter(BusinessEvent.idempotency_key == key).first() is not None


def _append_if_missing(
    db: Session,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int | None,
    key: str,
    *,
    actor_user_id: int | None = None,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> int:
    if _event_exists(db, key):
        return 0
    append_event(
        db,
        event_type,
        aggregate_type,
        aggregate_id,
        idempotency_key=key,
        actor_user_id=actor_user_id,
        request_id=request_id,
        payload=payload,
        occurred_at=occurred_at,
    )
    return 1


def _stock_event_type(movement_type: str) -> str | None:
    return {
        "opening_stock": "StockReceived",
        "purchase": "StockReceived",
        "purchase_reversal": "StockReturned",
        "sale": "StockDecremented",
        "sale_refund": "StockReturned",
        "adjustment": "StockAdjusted",
        "cost_adjustment": "StockCostAdjusted",
    }.get(movement_type)


def backfill_legacy_events(db: Session) -> int:
    """Create stable event history for records created before the journal."""
    created = 0

    for movement in db.query(StockMovement).order_by(StockMovement.id.asc()).all():
        event_type = _stock_event_type(movement.movement_type)
        if event_type:
            created += _append_if_missing(
                db,
                event_type,
                "variant",
                movement.variant_id,
                f"stock-movement:{movement.id}",
                payload={
                    "movement_id": movement.id,
                    "quantity_delta": movement.quantity_delta,
                    "movement_type": movement.movement_type,
                    "sale_id": movement.sale_id,
                    "purchase_id": movement.purchase_id,
                },
                occurred_at=movement.created_at,
            )

    for sale in db.query(Sale).order_by(Sale.id.asc()).all():
        if not sale.payment_confirmed:
            continue
        created += _append_if_missing(
            db,
            "SaleCompleted",
            "sale",
            sale.id,
            f"sale:{sale.id}:completed",
            payload={
                "payment_method": sale.payment_method,
                "final_amount": sale.final_amount,
                "customer_id": sale.customer_id,
            },
            occurred_at=sale.created_at,
        )
        if sale.payment_method == "credit":
            created += _append_if_missing(
                db,
                "CreditSaleIssued",
                "sale",
                sale.id,
                f"sale:{sale.id}:credit-issued",
                payload={"customer_id": sale.customer_id, "amount": sale.final_amount},
                occurred_at=sale.created_at,
            )
        if sale.points_earned and sale.customer_id:
            created += _append_if_missing(
                db,
                "LoyaltyUpdated",
                "customer",
                sale.customer_id,
                f"sale:{sale.id}:loyalty-updated",
                payload={"sale_id": sale.id, "points_earned": sale.points_earned},
                occurred_at=sale.created_at,
            )

    for refund in db.query(Refund).order_by(Refund.id.asc()).all():
        created += _append_if_missing(
            db,
            "RefundIssued",
            "refund",
            refund.id,
            f"refund:{refund.id}:issued",
            actor_user_id=refund.operator_user_id,
            payload={"sale_id": refund.sale_id, "amount": refund.total_amount},
            occurred_at=refund.created_at,
        )

    for payment in db.query(Payment).order_by(Payment.id.asc()).all():
        created += _append_if_missing(
            db,
            "CreditPaymentRecorded",
            "payment",
            payment.id,
            f"payment:{payment.id}:recorded",
            payload={
                "customer_id": payment.customer_id,
                "sale_id": payment.sale_id,
                "amount": payment.amount,
                "method": payment.method,
            },
            occurred_at=payment.created_at,
        )

    for reversal in db.query(PaymentReversal).order_by(PaymentReversal.id.asc()).all():
        created += _append_if_missing(
            db,
            "PaymentReversed",
            "payment_reversal",
            reversal.id,
            f"payment-reversal:{reversal.id}:recorded",
            actor_user_id=reversal.operator_user_id,
            payload={"payment_id": reversal.payment_id, "amount": reversal.amount},
            occurred_at=reversal.created_at,
        )

    for transaction in db.query(POSTransaction).order_by(POSTransaction.id.asc()).all():
        created += _append_if_missing(
            db,
            "PaymentRequested",
            "pos_transaction",
            transaction.id,
            f"pos:{transaction.id}:requested",
            payload={"amount": transaction.amount, "status": transaction.status},
            occurred_at=transaction.request_started_at or transaction.created_at,
        )
        outcome_type = {
            "approved": "PaymentApproved",
            "cancelled": "PaymentCancelled",
            "declined": "PaymentDeclined",
            "uncertain": "PaymentUncertain",
            "linked_to_sale": "PaymentApproved",
        }.get(transaction.status)
        if outcome_type:
            created += _append_if_missing(
                db,
                outcome_type,
                "pos_transaction",
                transaction.id,
                f"pos:{transaction.id}:{outcome_type}",
                actor_user_id=transaction.operator_user_id,
                payload={
                    "amount": transaction.amount,
                    "response_code": transaction.response_code,
                    "sale_id": transaction.sale_id,
                },
                occurred_at=transaction.request_finished_at or transaction.updated_at or transaction.created_at,
            )
        if transaction.reconciled and transaction.resolution_type:
            created += _append_if_missing(
                db,
                "POSReconciled",
                "pos_transaction",
                transaction.id,
                f"pos:{transaction.id}:reconciled:{transaction.resolution_type}",
                actor_user_id=transaction.operator_user_id,
                payload={
                    "resolution_type": transaction.resolution_type,
                    "provider_reference": transaction.provider_reference,
                    "sale_id": transaction.sale_id,
                    "evidence_recorded": bool(transaction.resolution_evidence),
                },
                occurred_at=transaction.reconciled_at or transaction.updated_at or transaction.created_at,
            )

    for purchase in db.query(Purchase).order_by(Purchase.id.asc()).all():
        created += _append_if_missing(
            db,
            "PurchaseRecorded",
            "purchase",
            purchase.id,
            f"purchase:{purchase.id}:recorded",
            payload={"total_cost": purchase.total_cost, "supplier_id": purchase.supplier_id},
            occurred_at=purchase.created_at,
        )
        if purchase.is_reversed:
            created += _append_if_missing(
                db,
                "PurchaseReversed",
                "purchase",
                purchase.id,
                f"purchase:{purchase.id}:reversed",
                payload={"total_cost": purchase.total_cost},
                occurred_at=purchase.reversed_at or purchase.created_at,
            )

    for expense in db.query(Expense).order_by(Expense.id.asc()).all():
        created += _append_if_missing(
            db,
            "ExpenseRecorded",
            "expense",
            expense.id,
            f"expense:{expense.id}:recorded",
            payload={"amount": expense.amount, "category": expense.category, "payment_method": expense.payment_method},
            occurred_at=expense.created_at,
        )
        if expense.reversed_at:
            created += _append_if_missing(
                db,
                "ExpenseReversed",
                "expense",
                expense.id,
                f"expense:{expense.id}:reversed",
                payload={"amount": expense.amount},
                occurred_at=expense.reversed_at,
            )

    for supplier_payment in db.query(SupplierPayment).order_by(SupplierPayment.id.asc()).all():
        created += _append_if_missing(
            db,
            "SupplierPaymentRecorded",
            "supplier_payment",
            supplier_payment.id,
            f"supplier-payment:{supplier_payment.id}:recorded",
            actor_user_id=supplier_payment.operator_user_id,
            payload={"supplier_id": supplier_payment.supplier_id, "amount": supplier_payment.amount, "method": supplier_payment.method},
            occurred_at=supplier_payment.created_at,
        )

    for cash_session in db.query(CashSession).order_by(CashSession.id.asc()).all():
        created += _append_if_missing(
            db,
            "CashSessionOpened",
            "cash_session",
            cash_session.id,
            f"cash-session:{cash_session.id}:opened",
            actor_user_id=cash_session.cashier_user_id,
            payload={"opening_balance": cash_session.opening_balance},
            occurred_at=cash_session.opened_at,
        )
        if cash_session.closed_at:
            created += _append_if_missing(
                db,
                "CashSessionClosed",
                "cash_session",
                cash_session.id,
                f"cash-session:{cash_session.id}:closed",
                actor_user_id=cash_session.manager_user_id,
                payload={
                    "expected_closing_balance": cash_session.expected_closing_balance,
                    "counted_closing_balance": cash_session.counted_closing_balance,
                    "variance": cash_session.variance,
                },
                occurred_at=cash_session.closed_at,
            )

    for reservation in db.query(StockReservation).order_by(StockReservation.id.asc()).all():
        created += _append_if_missing(
            db,
            "StockReserved",
            "reservation",
            reservation.id,
            f"reservation:{reservation.id}:reserved",
            payload={
                "checkout_session_id": reservation.checkout_session_id,
                "variant_id": reservation.variant_id,
                "quantity": reservation.quantity,
            },
            occurred_at=reservation.created_at,
        )
        if reservation.state == "released":
            created += _append_if_missing(
                db,
                "StockReleased",
                "reservation",
                reservation.id,
                f"reservation:{reservation.id}:released",
                payload={"variant_id": reservation.variant_id, "quantity": reservation.quantity},
                occurred_at=reservation.released_at or reservation.created_at,
            )
        elif reservation.state == "consumed":
            created += _append_if_missing(
                db,
                "StockReservationConsumed",
                "reservation",
                reservation.id,
                f"reservation:{reservation.id}:consumed",
                payload={"variant_id": reservation.variant_id, "quantity": reservation.quantity},
                occurred_at=reservation.released_at or reservation.created_at,
            )

    for checkout in db.query(CheckoutSession).order_by(CheckoutSession.id.asc()).all():
        created += _append_if_missing(
            db,
            "CheckoutCreated",
            "checkout",
            checkout.id,
            f"checkout:{checkout.id}:created",
            actor_user_id=checkout.staff_user_id,
            payload={"customer_id": checkout.customer_id},
            occurred_at=checkout.created_at,
        )
        if checkout.state == "refunded" and checkout.sale_id:
            created += _append_if_missing(
                db,
                "CheckoutRefunded",
                "checkout",
                checkout.id,
                f"checkout:{checkout.id}:refunded",
                payload={"sale_id": checkout.sale_id},
                occurred_at=checkout.updated_at or checkout.created_at,
            )

    return created
