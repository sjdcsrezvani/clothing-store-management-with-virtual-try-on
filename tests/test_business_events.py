import json

import pytest
from sqlalchemy.exc import StatementError

from models import BusinessEvent, Customer, Product, ProductVariant, StaffUser
from services.checkout import create_checkout, reserve_basket
from services.events import append_event
from tests.conftest import csrf_token


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


def test_database_reset_clears_checkout_data_and_keeps_reset_event(client, db_session, authed):
    customer = Customer(phone="09121111111", referral_code="RESET1")
    product = Product(name="Reset test product")
    db_session.add_all([customer, product])
    db_session.flush()
    customer.referred_by = customer.id
    variant = ProductVariant(
        product_id=product.id,
        price=100,
        cost_price=50,
        stock_quantity=2,
        barcode="reset-event-1",
    )
    db_session.add(variant)
    db_session.commit()

    owner = db_session.query(StaffUser).filter(StaffUser.username == "owner").one()
    checkout = create_checkout(
        db_session,
        customer_id=customer.id,
        staff_user_id=owner.id,
        basket_json=json.dumps([{"variant_id": variant.id, "quantity": 1}]),
        checkout_nonce="reset-event-checkout",
    )
    reserve_basket(db_session, checkout, [{"variant_id": variant.id, "quantity": 1}])
    assert db_session.query(ProductVariant).filter_by(id=variant.id).one().reserved_quantity == 1

    response = client.post(
        "/admin/reset-database",
        headers={"X-Request-ID": "reset-event-request"},
        data={"csrf_token": csrf_token(client, "/admin")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.query(Customer).count() == 0
    assert db_session.query(ProductVariant).filter_by(id=variant.id).one().reserved_quantity == 0
    reset_event = db_session.query(BusinessEvent).filter_by(event_type="DatabaseReset").one()
    assert reset_event.actor_user_id == owner.id
    assert reset_event.request_id == "reset-event-request"


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
