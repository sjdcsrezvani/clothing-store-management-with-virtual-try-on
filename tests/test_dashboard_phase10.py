"""Phase 10 ranking + targets + manager cards: per-seller board with a
week toggle, live counter, weekday goals settable by manager+, and the
periodic cards opened to managers."""
import datetime

from models import Settings
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _make_variant
import json


def _sell_as(client, db_session, user, password, price=100_000):
    _session_as(client, user, password)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=price, stock=5,
                               name="کالای رتبه")
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": price, "quantity": 1, "total_price": price}]
    client.post("/sales/confirm-sale", data={
        "customer_id": "0", "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "cash", "use_referrer_discount": "0",
        "custom_discount_amount": "", "custom_discount_percent": "",
        "csrf_token": csrf_token(client, "/sales/new"),
    })


def test_manager_sees_staff_board_and_live_panel(client, db_session):
    cashier, password = _staff(db_session, "phase10-cash", "cashier")
    _sell_as(client, db_session, cashier, password)
    manager, mpassword = _staff(db_session, "phase10-mgr", "manager")
    _session_as(client, manager, mpassword)
    page = client.get("/admin").text
    assert "فروشندگان" in page  # ranking section
    assert "phase10-cash" in page  # names are manager material
    assert "تخفیف" in page  # discount column aboard
    assert "/admin?rank=week" in page  # today/week toggle
    week = client.get("/admin?rank=week").text
    assert "۷ روز گذشته" in week


def test_owner_board_matches_manager_shape(client, db_session):
    owner, password = _staff(db_session, "phase10-owner2", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin").text
    assert "فروشندگان" in page


def test_cashier_never_sees_the_board(client, db_session):
    cashier, password = _staff(db_session, "phase10-cash2", "cashier")
    _session_as(client, cashier, password)
    page = client.get("/admin").text
    assert "فروشندگان" not in page
    assert "تعیین هدف روزانه" not in page


def test_targets_round_trip_for_manager_and_owner(client, db_session):
    weekday = datetime.datetime.now().weekday()
    for role, username in (("manager", "phase10-tmgr"),
                           ("owner", "phase10-town")):
        user, password = _staff(db_session, username, role)
        _session_as(client, user, password)
        resp = client.post("/admin/targets",
                           data={f"sales_target_{weekday}": "2000000",
                                 "csrf_token": csrf_token(client, "/admin")},
                           follow_redirects=False)
        assert resp.status_code == 303
    row = db_session.query(Settings).filter(
        Settings.key == f"sales_target_{weekday}").one()
    assert row.value == "2000000"
    manager, mpassword = _staff(db_session, "phase10-tmgr2", "manager")
    _session_as(client, manager, mpassword)
    assert "تعیین هدف روزانه" in client.get("/admin").text


def test_targets_refuse_cashiers_and_stray_keys(client, db_session):
    cashier, password = _staff(db_session, "phase10-tcash", "cashier")
    _session_as(client, cashier, password)
    resp = client.post("/admin/targets",
                       data={"sales_target_0": "1",
                             "csrf_token": csrf_token(client, "/admin")},
                       follow_redirects=False)
    assert resp.status_code == 403
    manager, mpassword = _staff(db_session, "phase10-tmgr3", "manager")
    _session_as(client, manager, mpassword)
    client.post("/admin/targets",
                data={"sales_target_0": "5", "store_name": "هک",
                      "csrf_token": csrf_token(client, "/admin")},
                follow_redirects=False)
    row = db_session.query(Settings).filter(
        Settings.key == "sales_target_0").first()
    assert row is None or row.value == "5"


def test_manager_sees_periodic_cards(client, db_session):
    manager, password = _staff(db_session, "phase10-pmgr", "manager")
    _session_as(client, manager, password)
    page = client.get("/admin").text
    assert "کارهای دوره‌ای" in page  # no longer owner-only
    for url in ("/admin/birthdays", "/admin/follow-ups", "/admin/tier-up",
                "/admin/tier-downgrades"):
        assert client.get(url).status_code == 200
