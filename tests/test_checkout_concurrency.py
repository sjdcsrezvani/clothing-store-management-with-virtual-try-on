import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base
from models import CheckoutEvent, CheckoutSession, Product, ProductVariant, StockReservation
from services.checkout import (
    InsufficientStockError,
    atomic_decrement_stock,
    complete_checkout,
    create_checkout,
    expire_stale,
    reserve_basket,
    transition_allowed,
)


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def enable_fk(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _variant(db, stock=1):
    product = Product(name="Concurrency test")
    db.add(product)
    db.flush()
    variant = ProductVariant(product_id=product.id, price=100, cost_price=50, stock_quantity=stock, barcode=f"bar-{stock}-{product.id}")
    db.add(variant)
    db.commit()
    return variant


def test_atomic_decrement_stops_at_zero():
    db = _memory_session()
    variant = _variant(db, stock=1)
    atomic_decrement_stock(db, variant.id, 1)
    db.commit()
    with pytest.raises(InsufficientStockError):
        atomic_decrement_stock(db, variant.id, 1)
    db.rollback()
    db.refresh(variant)
    assert variant.stock_quantity == 0
    assert db.query(StockReservation).count() == 0


def test_reservation_blocks_other_checkout_and_can_be_released():
    db = _memory_session()
    variant = _variant(db, stock=1)
    first = create_checkout(db, basket_json=json.dumps([{"variant_id": variant.id, "quantity": 1}]))
    reserve_basket(db, first, [{"variant_id": variant.id, "quantity": 1}])
    second = create_checkout(db, basket_json="[]")
    with pytest.raises(InsufficientStockError):
        reserve_basket(db, second, [{"variant_id": variant.id, "quantity": 1}])
    db.rollback()
    from services.checkout import release_reservations
    assert release_reservations(db, first) == 1
    db.commit()
    reserve_basket(db, second, [{"variant_id": variant.id, "quantity": 1}])
    assert db.query(StockReservation).filter(StockReservation.checkout_session_id == second.id, StockReservation.state == "active").count() == 1


def test_expired_checkout_releases_reservation_and_records_history():
    db = _memory_session()
    variant = _variant(db, stock=1)
    checkout = create_checkout(db, basket_json=json.dumps([{"variant_id": variant.id, "quantity": 1}]))
    reserve_basket(db, checkout, [{"variant_id": variant.id, "quantity": 1}])
    checkout.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert expire_stale(db) == 1
    db.refresh(checkout)
    assert checkout.state == "expired"
    assert db.query(StockReservation).filter(StockReservation.checkout_session_id == checkout.id, StockReservation.state == "released").count() == 1
    assert [event.to_state for event in checkout.history] == ["draft", "reserved", "expired"]


def test_checkout_state_machine_rejects_invalid_terminal_transition():
    assert transition_allowed("completed", "draft") is False
    assert transition_allowed("payment_cancelled", "completed") is False
    assert transition_allowed("uncertain", "completed") is False
    assert transition_allowed("completed", "refunded") is True


def test_completed_checkout_has_traceable_state_history():
    db = _memory_session()
    checkout = create_checkout(db)
    checkout.state = "payment_approved"
    db.commit()
    from models import Sale
    sale = Sale(total_amount=100, final_amount=100, payment_method="cash")
    db.add(sale)
    db.flush()
    complete_checkout(db, checkout, sale.id)
    assert checkout.state == "completed"
    assert checkout.history[-1].to_state == "completed"
