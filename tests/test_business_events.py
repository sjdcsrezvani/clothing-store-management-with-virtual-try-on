import json

import pytest
from sqlalchemy.exc import StatementError

from models import BusinessEvent, StaffUser
from services.events import append_event


def test_event_append_is_idempotent_and_redacts_sensitive_payload(db_session):
    first = append_event(
        db_session,
        "PaymentRequested",
        "checkout",
        1,
        idempotency_key="event-test-payment-request",
        payload={
            "amount": 1200,
            "api_token": "do-not-store",
            "nested": {"password": "also-do-not-store"},
        },
    )
    second = append_event(
        db_session,
        "PaymentRequested",
        "checkout",
        1,
        idempotency_key="event-test-payment-request",
        payload={"amount": 9999},
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(BusinessEvent).filter_by(idempotency_key="event-test-payment-request").count() == 1
    stored = json.loads(first.payload)
    assert stored["amount"] == 1200
    assert stored["api_token"] == "[redacted]"
    assert stored["nested"]["password"] == "[redacted]"
    assert "do-not-store" not in first.payload


def test_event_is_rolled_back_with_business_transaction(db_session):
    append_event(
        db_session,
        "SaleCompleted",
        "sale",
        1,
        idempotency_key="event-test-rollback",
        payload={"final_amount": 100},
    )
    db_session.rollback()

    assert db_session.query(BusinessEvent).filter_by(idempotency_key="event-test-rollback").count() == 0


def test_owner_can_view_business_event_history(client, db_session, authed):
    owner = db_session.query(StaffUser).filter(StaffUser.username == "owner").one()
    append_event(
        db_session,
        "CashSessionClosed",
        "cash_session",
        1,
        idempotency_key="event-test-owner-history",
        actor_user_id=owner.id,
        payload={"variance": 0},
    )
    db_session.commit()

    response = client.get("/admin/events")

    assert response.status_code == 200
    assert "CashSessionClosed" in response.text


def test_business_event_cannot_be_updated(db_session):
    event = append_event(
        db_session,
        "CheckoutCreated",
        "checkout",
        1,
        idempotency_key="event-test-immutable",
    )
    db_session.commit()
    event.payload = json.dumps({"changed": True})

    with pytest.raises((ValueError, StatementError)):
        db_session.commit()
    db_session.rollback()
