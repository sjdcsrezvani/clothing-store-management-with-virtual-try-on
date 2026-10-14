"""Phase 6 referral choice: default grow, explicit use-now/save pair shown
only when a balance exists, record required, accrual math untouched."""
import json

from models import Customer
from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_customer, _make_variant


def _login(client):
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)


def _scan_page(client, db_session, customer):
    _login(client)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=600_000, stock=5,
                               name="کالای معرف")
    page = client.post("/sales/add-to-basket", data={
        "customer_id": str(customer.id), "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    return page, variant


def test_default_is_grow_with_explicit_pair(client, db_session):
    customer = _make_customer(db_session, phone="09120000031")
    customer.referrer_discount = 50_000
    customer.active_referral_count = 2
    db_session.commit()
    page, _ = _scan_page(client, db_session, customer)
    assert "همین فاکتور استفاده شود" in page
    assert "ذخیره برای بعد" in page
    assert 'value="0" checked' in page  # grow is the default
    assert 'value="1" checked' not in page


def test_no_balance_no_choice(client, db_session):
    customer = _make_customer(db_session, phone="09120000032")
    customer.referrer_discount = 0
    customer.active_referral_count = 0
    db_session.commit()
    page, _ = _scan_page(client, db_session, customer)
    assert "همین فاکتور استفاده شود" not in page


def test_use_now_applies_grow_skips(client, db_session):
    customer = _make_customer(db_session, phone="09120000033")
    customer.referrer_discount = 50_000
    customer.active_referral_count = 1
    db_session.commit()
    _, variant = _make_variant(db_session, price=600_000, stock=5,
                               name="کالای سوئیچ")
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]
    _confirm_sale(client, basket, customer_id=customer.id, extra={
        "payment_method": "cash", "use_referrer_discount": "1"})
    from models import Sale
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.discount_amount >= 50_000
    db_session.refresh(customer)
    assert customer.referrer_discount == 0  # all-or-nothing, spent

    customer2 = _make_customer(db_session, phone="09120000034")
    customer2.referrer_discount = 50_000
    customer2.active_referral_count = 1
    db_session.commit()
    _, variant2 = _make_variant(db_session, price=600_000, stock=5,
                                name="کالای رشد")
    basket2 = [{"variant_id": variant2.id, "product_id": variant2.product_id,
                "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]
    _confirm_sale(client, basket2, customer_id=customer2.id, extra={
        "payment_method": "cash", "use_referrer_discount": "0"})
    sale2 = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale2.discount_amount == 0
    db_session.refresh(customer2)
    assert customer2.referrer_discount == 50_000  # saved, untouched


def test_anonymous_referrer_code_grants_nothing(client, db_session):
    referrer = _make_customer(db_session, phone="09120000035")
    _, variant = _make_variant(db_session, price=600_000, stock=5,
                               name="کالای ناشناس")
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]
    _confirm_sale(client, basket, extra={
        "payment_method": "cash", "referrer_code": referrer.referral_code,
        "use_referrer_discount": "1"})
    from models import Sale
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.discount_amount == 0  # record required
