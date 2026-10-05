"""Settings tabs: eight panels, one template, ?tab= deep-linking."""
from urllib.parse import unquote

from models import Settings
from routers.admin import SETTINGS_TABS
from tests.conftest import csrf_token
from tests.test_staff_phase_b import _owner_client

# The order the pill row must wear, read from the router's own table so a
# panel added there cannot slip past this file.
TABS = tuple(tab_id for tab_id, _title, _icon in SETTINGS_TABS)
TAB_FIELD = {
    "shop": 'name="store_name"',
    "sales": 'name="low_stock_threshold"',
    "discounts": 'name="birthday_target"',
    "credit": 'name="credit_terms_days"',
    "devices": 'name="pos_terminal_host"',
    "messaging": 'name="tryon_daily_limit"',
    "advanced": 'name="check_default_reminders"',
    "account": 'name="new_password"',
}


def test_the_pill_row_offers_every_panel_the_router_declares(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-pills")
    page = client.get("/admin/settings").text
    assert TABS == ("shop", "sales", "discounts", "credit", "devices",
                    "messaging", "advanced", "account")
    assert len(TABS) == 8
    for tab_id in TABS:
        assert f'data-settings-tab="{tab_id}"' in page, tab_id
        assert f'href="/admin/settings?tab={tab_id}"' in page, tab_id


def test_default_and_garbage_tabs_open_shop(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-default")
    for url in ("/admin/settings", "/admin/settings?tab=nope"):
        page = client.get(url).text
        assert 'aria-current="page" href="/admin/settings?tab=shop"' in page
        assert TAB_FIELD["shop"] in page
        assert TAB_FIELD["discounts"] not in page  # one panel per render


def test_each_tab_renders_its_own_panel(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-panels")
    for tab_id, field in TAB_FIELD.items():
        page = client.get(f"/admin/settings?tab={tab_id}").text
        assert field in page, tab_id
        others = [f for tab, f in TAB_FIELD.items() if tab != tab_id]
        assert all(f not in page for f in others), tab_id


def test_save_returns_to_its_tab_and_never_saves_tab(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-save")
    res = client.post("/admin/settings", data={
        "csrf_token": csrf_token(client, "/admin/settings"),
        "tab": "credit", "credit_terms_days": "45",
    }, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/admin/settings?tab=credit&msg=")
    db_session.expire_all()
    assert db_session.query(Settings).filter(
        Settings.key == "credit_terms_days").one().value == "45"
    assert db_session.query(Settings).filter(
        Settings.key == "tab").count() == 0


def test_numeric_error_reopens_the_owning_tab(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-err")
    res = client.post("/admin/settings", data={
        "csrf_token": csrf_token(client, "/admin/settings"),
        "tab": "shop", "tier_gold_threshold": "-5",
    }, follow_redirects=False)
    assert res.status_code == 303
    location = unquote(res.headers["location"])
    assert "tab=discounts" in location  # the field's home, not the posted tab
    assert "field=tier_gold_threshold" in location


def test_sms_link_section_is_gone_but_tryon_stays(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-sms")
    assert "متن همه پیامک‌ها" not in client.get("/admin/settings").text
    messaging = client.get("/admin/settings?tab=messaging").text
    assert 'name="tryon_daily_limit"' in messaging


def test_sales_tab_fields_and_guard_coverage(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-sales")
    page = client.get("/admin/settings?tab=sales").text
    for field in ("cash_opening_balance", "receipt_footer_note", "low_stock_threshold"):
        assert f'name="{field}"' in page, field


def test_low_stock_threshold_tunes_alerts(client, db_session):
    from services.inventory import low_stock_threshold, stock_alerts
    _owner_client(client, db_session, name="tabs-owner-stock")
    assert low_stock_threshold(db_session) == 2
    assert low_stock_threshold(None) == 2
    res = client.post("/admin/settings", data={
        "csrf_token": csrf_token(client, "/admin/settings"),
        "tab": "sales", "low_stock_threshold": "5",
    }, follow_redirects=False)
    assert res.status_code == 303
    db_session.expire_all()
    assert low_stock_threshold(db_session) == 5
    assert stock_alerts(db_session)["threshold"] == 5


def test_receipt_footer_note_prints_on_invoice(client, db_session):
    from models import Sale
    _owner_client(client, db_session, name="tabs-owner-receipt")
    client.post("/admin/settings", data={
        "csrf_token": csrf_token(client, "/admin/settings"),
        "tab": "sales", "receipt_footer_note": "ممنون از خرید شما",
    }, follow_redirects=False)
    sale = Sale(total_amount=600_000, discount_amount=0, final_amount=600_000,
                payment_method="card")
    db_session.add(sale)
    db_session.commit()
    page = client.get(f"/sales/invoice/{sale.id}").text
    assert "ممنون از خرید شما" in page


def test_messaging_tab_shows_gateway_status(client, db_session):
    _owner_client(client, db_session, name="tabs-owner-gw")
    page = client.get("/admin/settings?tab=messaging").text
    assert "درگاه" in page and "/admin/sms" in page
