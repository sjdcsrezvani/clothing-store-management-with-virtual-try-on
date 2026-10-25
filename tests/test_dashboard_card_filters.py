"""Each cashier dashboard card opens the list it counts."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from models import CheckoutSession, Customer, Sale
from tests.test_roles import _session_as, _staff


def _sale(db, staff, *, discount=0, refunded=False, created_at=None):
    sale = Sale(
        total_amount=100_000,
        discount_amount=discount,
        final_amount=100_000 - discount,
        payment_method="cash",
        payment_confirmed=True,
        is_refunded=refunded,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(sale)
    db.flush()
    db.add(CheckoutSession(
        checkout_nonce=uuid4().hex,
        staff_user_id=staff.id,
        sale_id=sale.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()
    return sale


def _customer(db, staff, phone, name, *, created_at=None):
    customer = Customer(
        phone=phone,
        first_name=name,
        referral_code=f"REF{uuid4().hex[:8]}",
        created_by_user_id=staff.id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(customer)
    db.commit()
    return customer


def test_cards_link_to_their_own_filtered_list(client, db_session):
    cashier, password = _staff(db_session, "cards-cash", "cashier")
    other, _ = _staff(db_session, "cards-other", "cashier")
    mine = _sale(db_session, cashier)
    other_sale = _sale(db_session, other)
    _customer(db_session, cashier, "09120000001", "خریدار من")
    _customer(db_session, other, "09120000002", "خریدار دیگر")

    _session_as(client, cashier, password)
    dashboard = client.get("/admin").text
    assert "/sales/?mine=1&amp;day=0" in dashboard
    assert "/sales/?mine=1&amp;day=0&amp;discounted=1" in dashboard
    assert "/sales/?mine=1&amp;day=0&amp;refunded=yes" in dashboard
    assert "/admin/customers?mine=1&amp;day=0" in dashboard
    yesterday = client.get("/admin?day=1").text
    assert "/sales/?mine=1&amp;day=1" in yesterday
    assert "/admin/customers?mine=1&amp;day=1" in yesterday

    sales = client.get("/sales?mine=1&day=0").text
    assert f"#{mine.id}</strong>" in sales
    assert f"#{other_sale.id}</strong>" not in sales

    customers = client.get("/admin/customers?mine=1&day=0").text
    assert "خریدار من" in customers
    assert "خریدار دیگر" not in customers


def test_discount_and_refund_filters_narrow_the_same_day(client, db_session):
    cashier, password = _staff(db_session, "cards-kinds", "cashier")
    plain = _sale(db_session, cashier)
    discounted = _sale(db_session, cashier, discount=10_000)
    refunded = _sale(db_session, cashier, refunded=True)

    _session_as(client, cashier, password)
    discounts = client.get("/sales?mine=1&day=0&discounted=1").text
    assert f"#{discounted.id}</strong>" in discounts
    assert f"#{plain.id}</strong>" not in discounts

    refunds = client.get("/sales?mine=1&day=0&refunded=yes").text
    assert f"#{refunded.id}</strong>" in refunds
    assert f"#{plain.id}</strong>" not in refunds


def test_customer_card_drops_customers_from_other_days(client, db_session):
    cashier, password = _staff(db_session, "cards-day", "cashier")
    _customer(db_session, cashier, "09120000003", "امروزی")
    _customer(db_session, cashier, "09120000004", "دیروزی",
              created_at=datetime.now(timezone.utc) - timedelta(days=1))

    _session_as(client, cashier, password)
    page = client.get("/admin/customers?mine=1&day=0").text
    assert "امروزی" in page
    assert "دیروزی" not in page


def test_mine_filter_follows_the_real_checkout_and_signup_owner(client, db_session):
    """The filter reads the same owner columns the real routes write."""
    from tests.conftest import csrf_token
    from tests.test_dashboard_phase9 import _sell_as

    cashier, password = _staff(db_session, "real-mine", "cashier")
    other, other_password = _staff(db_session, "real-other", "cashier")

    _sell_as(client, db_session, cashier, password, price=100_000)
    mine_sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    _sell_as(client, db_session, other, other_password, price=200_000)
    other_sale = db_session.query(Sale).order_by(Sale.id.desc()).first()

    _session_as(client, cashier, password)
    sales = client.get("/sales?mine=1&day=0").text
    assert f"#{mine_sale.id}</strong>" in sales
    assert f"#{other_sale.id}</strong>" not in sales

    _session_as(client, other, other_password)
    other_sales = client.get("/sales?mine=1&day=0").text
    assert f"#{other_sale.id}</strong>" in other_sales
    assert f"#{mine_sale.id}</strong>" not in other_sales

    _session_as(client, cashier, password)
    client.post("/sales/create-customer", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "phone": "09120000011",
        "first_name": "ثبت واقعی من",
    })
    _session_as(client, other, other_password)
    client.post("/sales/create-customer", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "phone": "09120000012",
        "first_name": "ثبت واقعی دیگر",
    })

    _session_as(client, cashier, password)
    customers = client.get("/admin/customers?mine=1&day=0").text
    assert "ثبت واقعی من" in customers
    assert "ثبت واقعی دیگر" not in customers

    _session_as(client, other, other_password)
    other_customers = client.get("/admin/customers?mine=1&day=0").text
    assert "ثبت واقعی دیگر" in other_customers
    assert "ثبت واقعی من" not in other_customers


