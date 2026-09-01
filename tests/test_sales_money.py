"""Money logic: server-side price recomputation, stock clamping, discount caps,
and complete refund reversal (referred discount, referral void, tier recompute)."""
import json

from models import Customer, Product, ProductVariant, Referral, Sale, SaleItem, StockMovement
from tests.conftest import csrf_token


def _make_variant(db_session, price=600_000, cost=300_000, stock=5, name="تیشرت تست"):
    product = Product(name=name)
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(
        product_id=product.id,
        price=price,
        cost_price=cost,
        stock_quantity=stock,
        barcode=f"{name[-2:]}{price}",
    )
    db_session.add(variant)
    db_session.commit()
    db_session.refresh(variant)
    return product, variant


def _make_customer(db_session, phone="09120000001", **kwargs):
    customer = Customer(phone=phone, referral_code=phone[-6:], **kwargs)
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _confirm_sale(client, basket, customer_id=0, extra=None, referrer_code="", url="/sales/new"):
    token = csrf_token(client, url)
    data = {
        "customer_id": str(customer_id),
        "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "card",
        "referrer_code": referrer_code,
        "referrer_phone": "",
        "use_referrer_discount": "1",
        "custom_discount_amount": "",
        "custom_discount_percent": "",
        "csrf_token": token,
    }
    if extra:
        data.update(extra)
    return client.post("/sales/confirm-sale", data=data)


def test_confirm_sale_rejects_unknown_payment_method(client, db_session):
    """Only the supported payment methods may create a sale."""
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]

    response = _confirm_sale(client, basket, extra={"payment_method": "wire_transfer"})

    assert response.status_code == 400
    assert "روش پرداخت نامعتبر" in response.json()["detail"]
    assert db_session.query(Sale).count() == 0
    db_session.refresh(variant)
    assert variant.stock_quantity == 5


def test_confirm_sale_recomputes_prices_from_db(client, db_session):
    """Client-tampered unit_price/total_price must be ignored — DB wins."""
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    tampered = [{
        "variant_id": variant.id, "product_id": variant.product_id,
        "unit_price": 1, "quantity": 3, "total_price": 3,
    }]
    resp = _confirm_sale(client, tampered)
    assert resp.status_code == 200
    assert "فاکتور" in resp.text

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.total_amount == 300_000          # 3 × 100_000, not 3
    assert sale.final_amount == 300_000
    item = db_session.query(SaleItem).filter(SaleItem.sale_id == sale.id).first()
    assert item.unit_price == 100_000
    assert item.total_price == 300_000
    # Stock decremented by the recomputed quantity.
    db_session.refresh(variant)
    assert variant.stock_quantity == 2


def test_confirm_sale_clamps_quantity_to_stock(client, db_session):
    _, variant = _make_variant(db_session, price=50_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 50_000, "quantity": 99, "total_price": 99 * 50_000}]
    resp = _confirm_sale(client, basket)
    assert resp.status_code == 200
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.total_amount == 5 * 50_000  # clamped to available stock
    db_session.refresh(variant)
    assert variant.stock_quantity == 0


def test_refund_restores_stock_and_reverses_customer(client, db_session):
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 2, "total_price": 200_000}]
    _confirm_sale(client, basket, customer_id=customer.id)

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    token = csrf_token(client, f"/sales/invoice/{sale.id}")
    resp = client.post(f"/sales/{sale.id}/refund",
                       data={"refund_reason": "تست", "csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 303

    db_session.refresh(sale)
    assert sale.is_refunded is True
    db_session.refresh(variant)
    assert variant.stock_quantity == 5          # restored
    db_session.refresh(customer)
    assert customer.total_spent == 0
    assert customer.total_purchases == 0
    movement_types = [m.movement_type for m in db_session.query(StockMovement)
                      .filter(StockMovement.variant_id == variant.id)
                      .order_by(StockMovement.id.asc()).all()]
    assert movement_types == ["sale", "sale_refund"]


def test_refund_of_referred_sale_voids_referral(client, db_session):
    referrer = _make_customer(db_session, phone="09120000002")
    referred = _make_customer(db_session, phone="09120000003",
                              referred_discount=50_000)
    _, variant = _make_variant(db_session, price=600_000, stock=5)  # ≥ min purchase
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]

    _confirm_sale(client, basket, customer_id=referred.id, referrer_code=referrer.referral_code)

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    # `_grant_referred_discount` uses the settings default (30k), not the 50k
    # we pre-seeded on the customer row.
    assert sale.discount_amount == 30_000  # referred discount applied
    db_session.refresh(referred)
    assert referred.has_used_referred_discount is True
    assert referred.referred_by == referrer.id
    db_session.refresh(referrer)
    assert referrer.active_referral_count == 1
    assert referrer.referrer_discount == 50_000  # referrer reward (settings default)
    assert db_session.query(Referral).filter(Referral.referred_id == referred.id).count() == 1

    # Refund the sale → referral must be voided.
    token = csrf_token(client, f"/sales/invoice/{sale.id}")
    resp = client.post(f"/sales/{sale.id}/refund",
                       data={"refund_reason": "تست", "csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 303

    db_session.refresh(referred)
    assert referred.has_used_referred_discount is False   # usable again
    assert referred.referred_by is None                    # unlinked
    db_session.refresh(referrer)
    assert referrer.active_referral_count == 0
    # Already-granted referrer credit is deliberately NOT clawed back.
    assert referrer.referrer_discount == 50_000
    assert db_session.query(Referral).filter(Referral.referred_id == referred.id).count() == 0


def test_refund_recomputes_tier_down(client, db_session):
    customer = _make_customer(db_session, total_points=1_990, tier="silver")
    _, variant = _make_variant(db_session, price=600_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]
    _confirm_sale(client, basket, customer_id=customer.id)

    db_session.refresh(customer)
    assert customer.tier == "gold"   # 1990 + 60 points ≥ 2000 threshold

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    token = csrf_token(client, f"/sales/invoice/{sale.id}")
    client.post(f"/sales/{sale.id}/refund",
                data={"refund_reason": "تست", "csrf_token": token},
                follow_redirects=False)

    db_session.refresh(customer)
    assert customer.total_points == 1_990
    assert customer.tier == "silver"  # points reversal dropped the tier back


def test_custom_discount_percent_capped_at_100(client, db_session):
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket, extra={"custom_discount_percent": "500"})
    assert resp.status_code == 200
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.discount_amount == 100_000  # 100%, not 500%
    assert sale.final_amount == 0
