import json
from datetime import timedelta

import pytest

from models import Product, ProductVariant
from services.checkout import (
    InsufficientStockError,
    create_checkout,
    reserve_basket,
    complete_checkout,
    transition_allowed,
    _now,
)


def test_reservation_uses_atomic_available_balance(db_session):
    product = Product(name="Atomic reservation")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, cost_price=50,
                             stock_quantity=1, barcode="atomic-reservation-1")
    db_session.add(variant)
    db_session.commit()
    first = create_checkout(db_session, basket_json=json.dumps([]))
    second = create_checkout(db_session, basket_json=json.dumps([]))
    reserve_basket(db_session, first, [{"variant_id": variant.id, "quantity": 1}])
    with pytest.raises(InsufficientStockError):
        reserve_basket(db_session, second, [{"variant_id": variant.id, "quantity": 1}])


def test_uncertain_payment_cannot_be_completed(db_session):
    checkout = create_checkout(db_session)
    checkout.state = "payment_uncertain"
    db_session.commit()
    with pytest.raises(ValueError):
        complete_checkout(db_session, checkout, 1)


def test_payment_uncertain_has_no_normal_completion_transition():
    assert transition_allowed("payment_uncertain", "completed") is False
