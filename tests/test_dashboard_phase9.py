"""Phase 9 cashier dashboard: my day only, never cost/profit/colleagues,
plus the navigation that lets a cashier reach it."""
import json

from models import Settings
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _make_variant


def _sell_as(client, db_session, user, password, price=100_000):
    """One cash sale rung by this staffer, through the real confirm route."""
    _session_as(client, user, password)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=price, stock=5,
                               name="کالای داشبورد")
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": price, "quantity": 1, "total_price": price}]
    return client.post("/sales/confirm-sale", data={
        "customer_id": "0", "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "cash", "use_referrer_discount": "0",
        "custom_discount_amount": "", "custom_discount_percent": "",
        "csrf_token": csrf_token(client, "/sales/new"),
    })


def test_cashier_opens_dashboard_and_sees_only_their_day(client, db_session):
    cashier, password = _staff(db_session, "phase9-cash", "cashier")
    _sell_as(client, db_session, cashier, password)
    _session_as(client, cashier, password)
    page = client.get("/admin").text
    assert "فروش امروز من" in page  # my-today card
    assert "سلام phase9-cash" in page  # greeting names the viewer
    assert "تخفیف‌های من" in page and "برگشتی‌های من" in page
    assert "مشتری‌های جدید من" in page
    assert "فروش امروز فروشگاه" in page  # shop total, no context beyond it
    for secret in ("سود", "حاشیه", "هزینه", "موجودی انبار"):
        assert secret not in page  # never computed, never printed


def test_cashier_history_strip_and_day_param(client, db_session):
    cashier, password = _staff(db_session, "phase9-hist", "cashier")
    _sell_as(client, db_session, cashier, password)
    _session_as(client, cashier, password)
    page = client.get("/admin").text
    assert "دیروز" in page and "/admin?day=1" in page  # 7-day strip
    yesterday = client.get("/admin?day=1").text
    assert "دیروز من" in yesterday  # full cards, other day


def test_target_card_appears_only_when_set(client, db_session):
    import datetime
    cashier, password = _staff(db_session, "phase9-target", "cashier")
    _sell_as(client, db_session, cashier, password)
    _session_as(client, cashier, password)
    assert "هدف امروز فروشگاه" not in client.get("/admin").text
    weekday = datetime.datetime.now().weekday()
    db_session.add(Settings(key=f"sales_target_{weekday}", value="1000000"))
    db_session.commit()
    page = client.get("/admin").text
    assert "هدف امروز فروشگاه" in page and "٪10" in page


def test_cashier_nav_and_home(client, db_session):
    from services.navigation import home_for, navigation_for, topbar_for
    assert home_for("cashier") == "/admin"  # dashboard first, not the till
    topbar = [a["key"] for a in topbar_for("cashier")]
    assert "sales" in topbar and "dashboard" in topbar  # both links
    sidebar = [i["key"] for s in navigation_for("cashier") for i in s["items"]]
    for key in ("sales-history", "cashbox", "customers", "tryon"):
        assert key in sidebar, key  # dashboard itself lives in the topbar
    assert "credit" not in sidebar and "settings" not in sidebar


def test_cashier_refusal_keeps_403_with_dashboard_way_home(client, db_session):
    cashier, password = _staff(db_session, "phase9-refused", "cashier")
    _session_as(client, cashier, password)
    resp = client.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 403  # contract kept; the page points home
    assert "بازگشت به داشبورد" in resp.text


def test_manager_and_owner_views_unchanged(client, db_session):
    manager, password = _staff(db_session, "phase9-mgr", "manager")
    _session_as(client, manager, password)
    page = client.get("/admin").text
    assert "نیازمند توجه" in page and "dash-greeting" not in page
    assert "سود ناخالص" not in page and "سود خالص" not in page  # money-in only
