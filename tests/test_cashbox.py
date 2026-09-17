"""The cash drawer: one shift, one count, one number that tells the truth.

The page is the only place in the app that can catch a till that does not match,
so most of these tests are about what must *not* happen — a second drawer cannot
be opened, a cashier cannot close somebody else's shift, a card-paid expense never
reaches the till, money taken out mid-shift is accounted for, and a count cannot
be copied off a figure the register already printed. The other half is the point
of the feature: a difference recorded on close has to be visible somewhere
afterwards — on the page, on the shift's own statement and on the dashboard.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import pytest
from sqlalchemy.exc import IntegrityError

from models import CashSession, CashSessionEntry, Expense, Sale, Settings
from services.accounting import get_cashbox, last_counted_balance, open_cash_session
from tests.conftest import csrf_token
from tests.test_roles import _login, _session_as, _staff
from tests.test_sales_money import _make_customer, _make_variant

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F]")


# ── helpers ──────────────────────────────────────────────────────────────────

def _open(client, amount):
    return client.post("/admin/cashbox/open",
                       data={"opening": str(amount), "csrf_token": csrf_token(client, "/admin/cashbox")},
                       follow_redirects=False)


def _close(client, counted):
    return client.post("/admin/cashbox/close",
                       data={"counted": str(counted), "csrf_token": csrf_token(client, "/admin/cashbox")},
                       follow_redirects=False)


def _withdraw(client, amount, reason):
    return client.post("/admin/cashbox/withdraw",
                       data={"amount": str(amount), "reason": reason,
                             "csrf_token": csrf_token(client, "/admin/cashbox")},
                       follow_redirects=False)


def _reverse(client, entry, session):
    return client.post(f"/admin/cashbox/withdrawals/{entry.id}/reverse",
                       data={"csrf_token": csrf_token(client, f"/admin/cashbox/sessions/{session.id}")},
                       follow_redirects=False)


def _sell(client, variant, *, payment_method="cash", quantity=1):
    """Confirm a sale as whoever is signed in, not as the owner."""
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": variant.price, "quantity": quantity,
               "total_price": variant.price * quantity}]
    return client.post("/sales/confirm-sale", data={
        "customer_id": "0",
        "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": payment_method,
        "referrer_code": "", "referrer_phone": "", "use_referrer_discount": "1",
        "custom_discount_amount": "", "custom_discount_percent": "",
        "csrf_token": csrf_token(client, "/sales/new"),
    }, follow_redirects=False)


# The shell's `<main>`, matched as a pattern: the tag carries `id` and `tabindex`
# for the skip link, and this cut must survive the shell learning attributes.
MAIN_OPEN = re.compile(r'<main class="app-main"[^>]*>')


def _content(html: str) -> str:
    """The page's own markup — the shell keeps its own icons and is not the subject."""
    start = MAIN_OPEN.search(html).end()
    return html[start:html.index("</main>", start)]


def _cashier(db_session, username):
    return _staff(db_session, username, "cashier")


def _live_shift(db_session) -> CashSession:
    return open_cash_session(db_session)


def _latest_shift(db_session) -> CashSession:
    return db_session.query(CashSession).order_by(CashSession.id.desc()).first()


# ── opening and closing ──────────────────────────────────────────────────────

def test_a_cashier_opens_and_closes_their_own_drawer(client, db_session):
    """The person who counts the money is the person the shift belongs to."""
    cashier, password = _cashier(db_session, "till-cashier")
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    _session_as(client, cashier, password)

    assert _open(client, 500_000).status_code == 303
    session = _live_shift(db_session)
    assert session.cashier_user_id == cashier.id
    assert session.opening_balance == 500_000

    assert _sell(client, variant).status_code == 200

    assert _close(client, 600_000).status_code == 303
    db_session.refresh(session)
    assert session.status == "closed"
    assert session.expected_closing_balance == 600_000
    assert session.counted_closing_balance == 600_000
    assert session.variance == 0
    # The closer is recorded whoever they were: the column is named «manager»,
    # the label says «بستننده», and a cashier closing their own shift is normal.
    assert session.manager_user_id == cashier.id


def test_the_drawer_refuses_a_second_opening_and_names_who_holds_it(client, db_session):
    cashier, password = _cashier(db_session, "till-holder")
    manager, manager_password = _staff(db_session, "till-manager", "manager")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303

    _session_as(client, manager, manager_password)
    again = _open(client, 250_000)
    assert again.status_code == 303
    assert "till-holder" in again.headers["location"]          # named, not numbered
    assert db_session.query(CashSession).filter(CashSession.status == "open").count() == 1
    assert _live_shift(db_session).opening_balance == 100_000


def test_the_database_refuses_a_second_open_shift(client, db_session):
    """The index, not the route guard, is what makes two drawers impossible."""
    cashier, password = _cashier(db_session, "till-index")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303

    db_session.add(CashSession(cashier_user_id=cashier.id, opening_balance=1, status="open"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_cashier_cannot_close_somebody_elses_drawer(client, db_session):
    first, first_password = _cashier(db_session, "till-first")
    second, second_password = _cashier(db_session, "till-second")
    _session_as(client, first, first_password)
    assert _open(client, 100_000).status_code == 303

    _session_as(client, second, second_password)
    assert _close(client, 100_000).status_code == 403
    assert _live_shift(db_session).status == "open"


def test_a_manager_closes_and_verifies_someone_elses_drawer(client, db_session):
    cashier, cashier_password = _cashier(db_session, "till-open")
    manager, manager_password = _staff(db_session, "till-verify", "manager")
    _, variant = _make_variant(db_session, price=50_000, stock=5)

    _session_as(client, cashier, cashier_password)
    assert _open(client, 100_000).status_code == 303
    assert _sell(client, variant).status_code == 200

    _session_as(client, manager, manager_password)
    closed = _close(client, 100_000)
    assert closed.status_code == 303
    assert "tone=warning" in closed.headers["location"]
    session = _latest_shift(db_session)
    assert session.expected_closing_balance == 150_000
    assert session.counted_closing_balance == 100_000
    assert session.variance == -50_000
    assert session.manager_user_id == manager.id


def test_the_blind_count_hides_the_expected_figure_until_the_count_is_in(client, db_session):
    """A count typed off a figure the register printed cannot disagree with it."""
    cashier, cashier_password = _cashier(db_session, "till-blind")
    manager, manager_password = _staff(db_session, "till-blind-manager", "manager")
    _, variant = _make_variant(db_session, price=250_000, stock=5)

    _session_as(client, cashier, cashier_password)
    assert _open(client, 100_000).status_code == 303
    assert _sell(client, variant).status_code == 200
    session = _live_shift(db_session)

    page = client.get("/admin/cashbox")
    assert "350,000" not in _content(page.text)                      # the figure that would be copied
    assert "350,000" not in _content(client.get(f"/admin/cashbox/sessions/{session.id}").text)
    assert "پولی که شمرده" in page.text                              # …while the form itself is there

    _session_as(client, manager, manager_password)
    assert "350,000" in client.get("/admin/cashbox").text            # the verifier sees it

    _session_as(client, cashier, cashier_password)
    told = _close(client, 345_000)
    assert told.status_code == 303
    message = unquote_plus(told.headers["location"])
    assert "350,000" in message                                      # spoken once the count is in
    assert "tone=warning" in told.headers["location"]


def test_the_next_drawer_suggests_the_last_count(client, db_session):
    cashier, password = _cashier(db_session, "till-suggest")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303
    assert _close(client, 95_000).status_code == 303

    assert last_counted_balance(db_session) == 95_000
    page = client.get("/admin/cashbox")
    assert 'value="95000"' in page.text
    assert "پیشنهاد از آخرین شمارش" in page.text


# ── what the drawer counts ───────────────────────────────────────────────────

def test_a_card_paid_expense_never_comes_out_of_the_drawer(client, db_session):
    """A salary paid by card left the shop's account, not the cash box.

    The register counted every expense however it was paid while it filtered
    supplier payments by method, so a card-paid wage quietly emptied a drawer
    nothing had been taken from — and the page's two blocks disagreed about the
    same money.
    """
    cashier, password = _cashier(db_session, "till-card")
    _session_as(client, cashier, password)
    assert _open(client, 1_000_000).status_code == 303

    shift = _live_shift(db_session)
    # Exactly what the app writes: a cash expense belongs to the shift that paid
    # it, and a card one gets no shift at all because no drawer was opened for it.
    db_session.add(Expense(amount=300_000, category="حقوق کارکنان", payment_method="card"))
    db_session.add(Expense(amount=50_000, category="اجاره", payment_method="cash",
                           cash_session_id=shift.id))
    db_session.commit()

    session = _live_shift(db_session)
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    register = get_cashbox(db_session, session.opened_at, end, session.opening_balance, session.id)
    assert register["expenses"] == 50_000                 # the cash one only
    assert register["closing"] == 950_000
    # …and the period view tells the same story as the shift view.
    assert get_cashbox(db_session, session.opened_at, end, 0)["expenses"] == 50_000


def test_the_drawer_ignores_money_that_moved_outside_its_window(client, db_session):
    cashier, password = _cashier(db_session, "till-window")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303

    db_session.add(Sale(total_amount=70_000, final_amount=70_000, payment_method="cash",
                        payment_confirmed=True,
                        created_at=datetime.now(timezone.utc) - timedelta(hours=2)))
    db_session.commit()

    session = _live_shift(db_session)
    register = get_cashbox(db_session, session.opened_at, datetime.now(timezone.utc),
                           session.opening_balance, session.id)
    assert register["cash_sales"] == 0
    assert register["closing"] == 100_000


def test_a_cash_sale_carries_its_shift_and_a_card_sale_does_not(client, db_session):
    """The column used to be written only when a sale was voided."""
    cashier, password = _cashier(db_session, "till-attrib")
    _, variant = _make_variant(db_session, price=80_000, stock=6)
    _session_as(client, cashier, password)
    assert _open(client, 200_000).status_code == 303
    assert _sell(client, variant, payment_method="cash").status_code == 200
    assert _sell(client, variant, payment_method="card").status_code == 200

    cash_sale = db_session.query(Sale).filter(Sale.payment_method == "cash").order_by(Sale.id.desc()).first()
    card_sale = db_session.query(Sale).filter(Sale.payment_method == "card").order_by(Sale.id.desc()).first()
    assert cash_sale.cash_session_id == _live_shift(db_session).id
    assert card_sale.cash_session_id is None


# ── money taken out mid-shift ────────────────────────────────────────────────

def test_a_withdrawal_lowers_what_should_be_in_the_drawer(client, db_session):
    cashier, password = _cashier(db_session, "till-withdraw")
    _session_as(client, cashier, password)
    assert _open(client, 1_000_000).status_code == 303
    assert _withdraw(client, 300_000, "واریز به بانک").status_code == 303

    entry = db_session.query(CashSessionEntry).one()
    assert (entry.amount, entry.reason) == (300_000, "واریز به بانک")

    session = _live_shift(db_session)
    register = get_cashbox(db_session, session.opened_at, datetime.now(timezone.utc),
                           session.opening_balance, session.id)
    assert register["withdrawals"] == 300_000
    assert register["closing"] == 700_000

    manager, manager_password = _staff(db_session, "till-withdraw-manager", "manager")
    _session_as(client, manager, manager_password)
    assert _reverse(client, entry, session).status_code == 303
    db_session.refresh(entry)
    assert entry.reversed_at is not None
    register = get_cashbox(db_session, session.opened_at, datetime.now(timezone.utc),
                           session.opening_balance, session.id)
    assert register["closing"] == 1_000_000


def test_a_withdrawal_needs_an_amount_and_a_reason(client, db_session):
    cashier, password = _cashier(db_session, "till-guard")
    _session_as(client, cashier, password)
    assert _open(client, 500_000).status_code == 303

    assert "err=" in _withdraw(client, 100_000, "   ").headers["location"]
    assert "err=" in _withdraw(client, 0, "خرید نقدی").headers["location"]
    assert "err=" in _withdraw(client, 600_000, "خرید نقدی").headers["location"]   # more than the drawer
    assert db_session.query(CashSessionEntry).count() == 0

    assert "msg=" in _withdraw(client, 100_000, "خرید نقدی").headers["location"]
    assert db_session.query(CashSessionEntry).count() == 1


def test_a_withdrawal_cannot_be_reversed_once_the_shift_is_closed(client, db_session):
    """A closed shift's expected and counted figures are a record somebody signed."""
    cashier, password = _cashier(db_session, "till-frozen")
    manager, manager_password = _staff(db_session, "till-frozen-manager", "manager")
    _session_as(client, cashier, password)
    assert _open(client, 400_000).status_code == 303
    assert _withdraw(client, 100_000, "واریز به بانک").status_code == 303
    assert _close(client, 300_000).status_code == 303

    session = _latest_shift(db_session)
    entry = db_session.query(CashSessionEntry).one()
    assert session.variance == 0

    _session_as(client, manager, manager_password)
    refused = _reverse(client, entry, session)
    assert refused.status_code == 303
    assert "err=" in refused.headers["location"]
    db_session.refresh(entry)
    assert entry.reversed_at is None


# ── the page, per role ───────────────────────────────────────────────────────

def test_a_cashier_sees_their_own_shifts_and_no_period_report(client, db_session):
    cashier, password = _cashier(db_session, "till-own")
    other, other_password = _cashier(db_session, "till-other")
    _session_as(client, other, other_password)
    assert _open(client, 55_000).status_code == 303
    assert _close(client, 55_000).status_code == 303
    other_shift = _latest_shift(db_session)

    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303
    page = client.get("/admin/cashbox")
    assert page.status_code == 200
    assert "شیفت‌های خودتان" in page.text
    assert f"/admin/cashbox/sessions/{other_shift.id}" not in _content(page.text)
    # The period register, the default float and the shop's other doors are a
    # manager's business — a cashier's page offers none of them.
    assert "حرکت نقدی این بازه" not in page.text
    assert "موجودی پیش‌فرض شروع روز" not in page.text
    assert "/admin/expenses" not in page.text
    assert "تا این لحظه باید در کشو باشد" not in page.text

    manager, manager_password = _staff(db_session, "till-report", "manager")
    _session_as(client, manager, manager_password)
    manager_page = client.get("/admin/cashbox")
    assert "حرکت نقدی این بازه" in manager_page.text
    assert "موجودی پیش‌فرض شروع روز" in manager_page.text
    assert f"/admin/cashbox/sessions/{other_shift.id}" in manager_page.text
    assert "تا این لحظه باید در کشو باشد" in manager_page.text


def test_the_statement_belongs_to_its_opener_and_to_a_manager(client, db_session):
    cashier, password = _cashier(db_session, "till-statement")
    other, other_password = _cashier(db_session, "till-statement-other")
    manager, manager_password = _staff(db_session, "till-statement-manager", "manager")

    _session_as(client, cashier, password)
    assert _open(client, 120_000).status_code == 303
    session = _live_shift(db_session)

    # Open shift: the statement is exactly the arithmetic the counter must not
    # read before counting, so only a manager is given it.
    assert client.get(f"/admin/cashbox/sessions/{session.id}").status_code == 403
    _session_as(client, manager, manager_password)
    assert client.get(f"/admin/cashbox/sessions/{session.id}").status_code == 200

    _session_as(client, other, other_password)
    assert client.get(f"/admin/cashbox/sessions/{session.id}").status_code == 403

    # Closed shift: nothing left to bias, and the opener may read their own.
    _session_as(client, manager, manager_password)
    assert _close(client, 120_000).status_code == 303
    _session_as(client, cashier, password)
    assert client.get(f"/admin/cashbox/sessions/{session.id}").status_code == 200
    _session_as(client, other, other_password)
    assert client.get(f"/admin/cashbox/sessions/{session.id}").status_code == 403


def test_the_statement_shows_what_makes_up_the_expected_figure(client, db_session):
    cashier, password = _cashier(db_session, "till-trace")
    manager, manager_password = _staff(db_session, "till-trace-manager", "manager")
    _, variant = _make_variant(db_session, price=90_000, stock=5)

    _session_as(client, cashier, password)
    assert _open(client, 200_000).status_code == 303
    assert _sell(client, variant).status_code == 200
    assert _withdraw(client, 30_000, "خرید نقدی").status_code == 303
    db_session.add(Expense(amount=20_000, category="حمل و نقل", payment_method="cash",
                           cash_session_id=_live_shift(db_session).id))
    db_session.commit()
    session = _live_shift(db_session)

    _session_as(client, manager, manager_password)
    page = client.get(f"/admin/cashbox/sessions/{session.id}")
    assert page.status_code == 200
    assert f"شیفت صندوق #{session.id}" in page.text
    assert f"+90,000 ت" in page.text                       # the sale
    assert "-30,000 ت" in page.text                 # the withdrawal
    assert "-20,000 ت" in page.text                 # the expense
    assert "240,000" in page.text                          # 200,000 + 90,000 − 30,000 − 20,000
    assert "/sales/invoice/" in page.text                  # every row reaches its record
    assert "برگشت برداشت" in page.text                      # and a mistake is fixable


def test_the_shift_history_shows_the_difference_a_count_found(client, db_session):
    """The variance used to be written to the database and shown nowhere at all."""
    cashier, password = _cashier(db_session, "till-history")
    manager, manager_password = _staff(db_session, "till-history-manager", "manager")
    _session_as(client, cashier, password)
    assert _open(client, 500_000).status_code == 303
    assert _close(client, 450_000).status_code == 303
    session = _latest_shift(db_session)

    _session_as(client, manager, manager_password)
    content = _content(client.get("/admin/cashbox").text)
    assert "50,000 ت کسری" in content
    assert "450,000" in content                      # what was counted
    assert f"/admin/cashbox/sessions/{session.id}" in content   # and where to read why


def test_a_shift_the_upgrade_closed_says_so_instead_of_pretending(client, db_session, authed):
    """A duplicate the migration had to close carries no count, and says why."""
    db_session.add(CashSession(cashier_user_id=1, opening_balance=250_000, status="abandoned",
                               opened_at=datetime.now(timezone.utc) - timedelta(days=1),
                               closed_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db_session.commit()
    session = _latest_shift(db_session)

    statement = client.get(f"/admin/cashbox/sessions/{session.id}")
    assert statement.status_code == 200
    assert "این شیفت بدون شمارش بسته شده است" in statement.text
    assert "بسته‌شده بدون شمارش" in _content(client.get("/admin/cashbox").text)


def test_the_shift_list_is_paged_and_can_be_opened_whole(client, db_session, authed):
    """Twenty shifts is a page, not a wall — and «نمایش همه» really shows all."""
    for index in range(25):
        db_session.add(CashSession(cashier_user_id=1, opening_balance=1_000,
                                   status="closed", variance=0,
                                   expected_closing_balance=1_000,
                                   counted_closing_balance=1_000,
                                   opened_at=datetime.now(timezone.utc) - timedelta(days=index),
                                   closed_at=datetime.now(timezone.utc) - timedelta(days=index)))
    db_session.commit()

    page = client.get("/admin/cashbox")
    assert "20 از 25" in page.text
    assert "نمایش همهٔ شیفت‌ها (25)" in page.text

    whole = client.get("/admin/cashbox?history=all")
    assert "25 از 25" in whole.text
    assert whole.text.count("/admin/cashbox/sessions/") > 20


def test_the_period_report_explains_the_number_it_prints(client, db_session, authed):
    page = client.get("/admin/cashbox")
    assert "موجودی پایان بازه" in page.text
    assert "پولی که همین حالا در کشو است را فقط بستن شیفت نشان می‌دهد" in page.text

    all_time = client.get("/admin/cashbox?period=all")
    assert "خالص حرکت بازه" in all_time.text
    assert "موجودی پایان بازه" not in all_time.text


def test_the_default_float_refuses_a_negative_and_is_a_managers_form(client, db_session):
    cashier, password = _cashier(db_session, "till-default")
    _session_as(client, cashier, password)
    refused = client.post("/admin/cashbox/opening",
                          data={"opening": "500", "csrf_token": csrf_token(client, "/admin/cashbox")},
                          follow_redirects=False)
    assert refused.status_code == 403
    assert db_session.query(Settings).filter(Settings.key == "cash_opening_balance").first() is None

    manager, manager_password = _staff(db_session, "till-default-manager", "manager")
    _session_as(client, manager, manager_password)
    bad = client.post("/admin/cashbox/opening",
                      data={"opening": "-5", "csrf_token": csrf_token(client, "/admin/cashbox")},
                      follow_redirects=False)
    assert "err=" in bad.headers["location"]
    stored = db_session.query(Settings).filter(Settings.key == "cash_opening_balance").first()
    assert stored is None or stored.value == "0"

    good = client.post("/admin/cashbox/opening",
                       data={"opening": "۳۰۰۰۰۰", "csrf_token": csrf_token(client, "/admin/cashbox")},
                       follow_redirects=False)
    assert "msg=" in good.headers["location"]
    assert db_session.query(Settings).filter(Settings.key == "cash_opening_balance").first().value == "300000"


def test_the_cashbox_page_is_persian_and_names_people(client, db_session):
    """The shell's no-emoji rule, extended to this page's controls and tables."""
    cashier, password = _cashier(db_session, "till-names")
    _session_as(client, cashier, password)
    assert _open(client, 10_000).status_code == 303
    assert _close(client, 10_000).status_code == 303

    content = _content(client.get("/admin/cashbox").text)
    assert not EMOJI.search(content), EMOJI.findall(content)[:5]
    assert "کاربر #" not in content
    assert "till-names" in content                       # the opener is named


# ── the dashboard ────────────────────────────────────────────────────────────

def test_the_dashboard_reports_the_drawer_instead_of_guessing_it(client, db_session):
    """With nothing counted, no float is known — so no balance is claimed."""
    _login(client, "owner", "test-admin-pass")
    assert "باز نشده" in client.get("/admin").text

    cashier, password = _cashier(db_session, "till-dash")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303

    _login(client, "owner", "test-admin-pass")
    assert "صندوق باز از" in client.get("/admin").text


def test_the_dashboard_names_a_shortage_and_hides_when_there_is_none(client, db_session):
    _login(client, "owner", "test-admin-pass")
    assert "اختلاف صندوق" not in client.get("/admin").text

    cashier, password = _cashier(db_session, "till-variance")
    _session_as(client, cashier, password)
    assert _open(client, 500_000).status_code == 303
    assert _close(client, 470_000).status_code == 303

    _login(client, "owner", "test-admin-pass")
    page = client.get("/admin")
    assert "اختلاف صندوق" in page.text
    assert "30,000" in page.text
    assert "کسری" in page.text

    # A drawer that balanced asks for nothing.
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303
    assert _close(client, 100_000).status_code == 303
    _login(client, "owner", "test-admin-pass")
    assert "اختلاف صندوق" not in client.get("/admin").text


def test_the_dashboard_and_the_page_name_a_drawer_left_open(client, db_session):
    cashier, password = _cashier(db_session, "till-stale")
    _session_as(client, cashier, password)
    assert _open(client, 100_000).status_code == 303
    session = _live_shift(db_session)
    session.opened_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    _login(client, "owner", "test-admin-pass")
    assert "صندوق باز مانده" in client.get("/admin").text
    assert "باز مانده است" in client.get("/admin/cashbox").text
