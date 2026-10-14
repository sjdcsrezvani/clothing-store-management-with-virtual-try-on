"""Phase 5 profit meter: manager/owner only, live per render, with a
distinct margin percent — and nothing at all for cashiers, not even HTML."""
import json

from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _make_variant


def _scan_as(client, db_session, role, username, product_name="تیشرت تست"):
    user, password = _staff(db_session, username, role)
    _session_as(client, user, password)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5,
                               name=product_name)
    return client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text


def test_cashier_page_carries_no_margin_at_all(client, db_session):
    page = _scan_as(client, db_session, "cashier", "phase5-cashier")
    assert "profit-meter" not in page
    assert "profit-pct" not in page


def test_manager_and_owner_see_amount_plus_distinct_percent(client, db_session):
    for role, username, product in (("manager", "phase5-manager", "تیشرت مدیر"),
                                    ("owner", "phase5-owner", "تیشرت مالک")):
        page = _scan_as(client, db_session, role, username, product)
        assert "profit-meter" in page
        assert "+50,000" in page  # exact toman: 100,000 − 50,000
        assert '<span class="profit-pct tnum">50٪</span>' in page


def test_meter_hides_on_an_empty_basket(client, db_session):
    user, password = _staff(db_session, "phase5-owner-empty", "owner")
    _session_as(client, user, password)
    page = client.post("/sales/skip-customer",
                       data={"csrf_token": csrf_token(client, "/sales/new")}).text
    assert "profit-meter" not in page
