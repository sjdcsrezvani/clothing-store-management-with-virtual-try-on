import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from database import Base
from models import Customer, Product, ProductVariant, Sale, SaleItem, POSTransaction, StockMovement


def _engine():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def enable_fk(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine


def test_sqlite_connections_enable_foreign_keys(db_session):
    assert db_session.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys")).scalar() == 1


def test_invalid_foreign_key_is_rejected(db_session):
    db_session.add(Sale(total_amount=100, final_amount=100, customer_id=999999, payment_method="cash"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_sale_item_requires_existing_sale(db_session):
    db_session.add(SaleItem(sale_id=999999, product_id=999999, quantity=1, unit_price=1, unit_cost=0, total_price=1))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_constraints_reject_invalid_business_values(db_session):
    with pytest.raises(IntegrityError):
        db_session.add(Sale(total_amount=1, final_amount=1, payment_method="wire"))
        db_session.commit()


def test_unique_barcode_is_enforced(db_session):
    product = Product(name="T", is_active=True)
    db_session.add(product)
    db_session.flush()
    db_session.add_all([
        ProductVariant(product_id=product.id, price=1, barcode="same"),
        ProductVariant(product_id=product.id, price=1, barcode="same"),
    ])
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_valid_relationships_round_trip(db_session):
    customer = Customer(phone="09120000000", referral_code="ABC123")
    product = Product(name="T")
    db_session.add_all([customer, product])
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, barcode="rel-1")
    sale = Sale(customer_id=customer.id, total_amount=100, final_amount=100, payment_method="cash")
    db_session.add_all([variant, sale])
    db_session.flush()
    item = SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id, quantity=1, unit_price=100, unit_cost=10, total_price=100)
    movement = StockMovement(variant_id=variant.id, sale_id=sale.id, quantity_delta=-1, movement_type="sale")
    pos = POSTransaction(checkout_nonce="relationship", amount=100, host="127.0.0.1", port=3000, status="created", customer_id=customer.id, sale=sale)
    db_session.add_all([item, movement, pos])
    db_session.commit()
    assert sale.items[0].variant.id == variant.id
    assert pos.sale.id == sale.id
