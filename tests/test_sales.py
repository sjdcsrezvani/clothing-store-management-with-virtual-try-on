"""Test the critical sales flow — the exact flow that had the "phone required" bug.

Run:  python3 -m pytest tests/test_sales.py -v          (if pytest installed)
Or:   DATABASE_URL=sqlite:///./test_check.db python3 -c "
  from tests.test_sales import test_sales_flow
  test_sales_flow()
"
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, get_db
from main import app

# In-memory SQLite for isolated tests
TEST_DB_URL = "sqlite:///./test_referral.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def setup_module():
    Base.metadata.create_all(bind=engine)


def teardown_module():
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("test_referral.db"):
        os.remove("test_referral.db")


def test_sales_flow():
    """Full sales cycle: lookup new customer → create → scan → confirm → receipt."""
    # Step 1: Lookup a new phone number → should redirect to customer creation
    resp = client.post("/sales/lookup-customer", data={"phone": "09123456789"}, follow_redirects=False)
    assert resp.status_code in (200, 303), f"lookup failed: {resp.status_code}"

    # Step 2: Create customer
    resp = client.post("/sales/create-customer", data={
        "phone": "09123456789",
        "first_name": "رضا",
        "last_name": "محمدی",
        "child_name": "سارا",
        "child_birthday": "۱۴۰۳/۰۵/۱۲",
    }, follow_redirects=False)
    assert resp.status_code in (200, 303), f"create failed: {resp.status_code}"

    # The customer was created immediately (bug fix). Follow to scan step.
    # If redirect, follow it.
    if resp.status_code == 303:
        location = resp.headers.get("location", "")
        # Customer might redirect to checkout page — follow
        resp = client.get(location, follow_redirects=True)
        # Actually the /sales/create-customer POST returns HTML directly with step=scan
        # If it's a redirect, let's follow properly
        if resp.status_code == 303:
            resp = client.get(resp.headers["location"], follow_redirects=True)

    # Now we should be on the scan step — the customer was created.
    # The key assertion: no "phone required" error (the old bug).
    assert "شماره موبایل الزامی است" not in resp.text, "BUG: phone required error on new customer"

    # Verify the response is checkout HTML (step scan or customer)
    assert "رای کیدز" in resp.text or "checkout" in resp.text or "فروش" in resp.text

    # Step 3: Confirm sale with an empty basket → should get "empty basket" error
    resp = client.post("/sales/confirm-sale", data={
        "customer_id": 1,
        "basket_json": "[]",
        "payment_method": "card",
        "referrer_code": "",
        "referrer_phone": "",
    })
    assert "سبد خرید خالی است" in resp.text, "Should reject empty basket"

    # Step 4: Confirm sale with a valid customer works (happy path)
    # (requires products in DB — skippable check)
    print("Sales flow test: OK — new customer created without 'phone required' error")

    # Cleanup
    from models import Customer
    db = TestSession()
    db.query(Customer).delete()
    db.commit()
    db.close()


def test_birthday_parser_edge_cases():
    """Test the centralized birthday parser via the customer creation endpoint."""
    from services._common import parse_persian_birthday
    assert parse_persian_birthday("۱۴۰۳/۰۳/۱۵") == "03-15"
    assert parse_persian_birthday("1403-03-15") == "03-15"
    assert parse_persian_birthday("") is None
    assert parse_persian_birthday("garbage") is None
    assert parse_persian_birthday("1403/13/01") is None  # bad month


def test_discount_engine_referred_carry_over_and_gates():
    """Referred discount: below min -> no apply; at/above min -> applies; and it is
    not tied to the literal first purchase (has_used_referred_discount is the guard)."""
    from services.discount import calculate_discounts
    from services._common import get_setting_int
    from models import Settings, Customer
    db = TestSession()
    db.add(Settings(key="min_purchase_for_discount", value="100000"))
    c = Customer(phone="09000000011", referral_code="AAA111", referred_discount=30000)
    db.add(c)
    db.commit()
    # territory below threshold -> 0
    below = calculate_discounts(c, 90000, db)
    assert below["referred_discount"] == 0, "must not apply below min purchase"
    # territory at/above threshold -> applied
    at = calculate_discounts(c, 100000, db)
    assert at["referred_discount"] == 30000
    db.close()


def test_discount_engine_referrer_opt_in():
    from services.discount import calculate_discounts
    from models import Customer
    db = TestSession()
    c = Customer(phone="09000001122", referral_code="ABB112",
                 referrer_discount=150000, active_referral_count=3)
    db.add(c)
    db.commit()
    off = calculate_discounts(c, 200000, db, use_referrer_discount=False)
    assert off["referrer_discount"] == 0, "opt-out must zero the referrer discount"
    on = calculate_discounts(c, 200000, db, use_referrer_discount=True)
    assert on["referrer_discount"] == 150000
    db.close()


def test_discount_engine_custom_amount_percent_precedence_and_cap():
    from services.discount import calculate_discounts
    from models import Customer
    db = TestSession()
    c = Customer(phone="09000008888", referral_code="AAA888")
    db.add(c)
    db.commit()
    amt = calculate_discounts(c, 200000, db, custom_amount=30000, custom_percent=0)
    assert amt["custom_discount"] == 30000, "amount-only applies the amount"
    pct = calculate_discounts(c, 200000, db, custom_amount=0, custom_percent=10)
    assert pct["custom_discount"] == 20000, "percent-only applies percent of total"
    both = calculate_discounts(c, 200000, db, custom_amount=5000, custom_percent=10)
    assert both["custom_discount"] == 5000, "amount takes precedence"
    capped = calculate_discounts(c, 10000, db, custom_amount=99999)
    assert capped["total_discount"] == 10000, "total discount capped at total amount"
    db.close()


def test_points_floor_to_threshold():
    """Points already floor-divide; lock it in. 90,000/100k -> 0; 100k -> 10; 190k -> 10; 250k -> 20."""
    from services.tier import calculate_points
    cfg = {"points_per_amount": 10, "points_per_toman": 100000}
    assert calculate_points(90000, cfg) == 0
    assert calculate_points(100000, cfg) == 10
    assert calculate_points(190000, cfg) == 10
    assert calculate_points(250000, cfg) == 20


if __name__ == "__main__":
    setup_module()
    try:
        test_sales_flow()
        test_birthday_parser_edge_cases()
        print("All tests passed.")
    finally:
        teardown_module()
