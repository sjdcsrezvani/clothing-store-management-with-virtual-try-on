"""Phase 11 owner extras + poll: split attention, merged cash, failed-SMS
alert, manager-visible money cards, greeting for all, silent poll."""
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def _page_as(client, db_session, role, username):
    user, password = _staff(db_session, username, role)
    _session_as(client, user, password)
    return client.get("/admin").text


def test_attention_splits_money_first(client, db_session):
    page = _page_as(client, db_session, "owner", "phase11-owner")
    assert "اقدام فوری" in page  # money row leads
    assert "پیامک ناموفق" not in page  # empty failure queue: no card
    assert "اختلاف صندوق" not in page and "صندوق باز مانده" not in page


def test_pos_card_is_owner_only(client, db_session):
    manager_page = _page_as(client, db_session, "manager", "phase11-mgr")
    assert "تراکنش کارتخوان" not in manager_page
    owner_page = _page_as(client, db_session, "owner", "phase11-own2")
    assert "تراکنش کارتخوان" in owner_page


def test_manager_sees_money_cards_without_margins(client, db_session):
    page = _page_as(client, db_session, "manager", "phase11-mgr2")
    assert "هزینه‌ها" in page and "ارزش موجودی انبار" in page
    assert "سود ناخالص" not in page and "سود خالص" not in page


def test_debt_splits_overdue_and_club_shrinks(client, db_session):
    page = _page_as(client, db_session, "manager", "phase11-mgr3")
    assert "نسیه در گردش" in page
    assert "مشتریان بدهکار" not in page  # balances card owns it now
    assert 'data-poll-value="club_new"' not in page  # folded into members sub


def test_greeting_names_every_role(client, db_session):
    for role, username in (("manager", "phase11-gmgr"),
                           ("owner", "phase11-gown")):
        page = _page_as(client, db_session, role, username)
        assert f"سلام {username}" in page


def test_reading_is_one_line(client, db_session):
    page = _page_as(client, db_session, "owner", "phase11-read")
    assert "روایت این ماه" in page


def test_poll_returns_keyed_values_per_role(client, db_session):
    # Each board names its own day's total: the manager's shop card is
    # today_sales, the cashier's board carries shop_today. The poll must hand
    # back whatever that role's page draws — same builder, flattened.
    for role, username, day_key in (("manager", "phase11-pmgr", "today_sales"),
                                    ("cashier", "phase11-pcash", "shop_today")):
        user, password = _staff(db_session, username, role)
        _session_as(client, user, password)
        resp = client.get("/admin/dashboard-poll",
                          headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert "cards" in data and day_key in data["cards"]
    client.cookies.clear()
    assert client.get("/admin/dashboard-poll",
                      follow_redirects=False).status_code in (401, 403, 303)


def test_settings_sales_tab_carries_weekday_targets(client, db_session):
    owner, password = _staff(db_session, "phase11-set", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin/settings?tab=sales").text
    assert "sales_target_0" in page and "شنبه" in page
