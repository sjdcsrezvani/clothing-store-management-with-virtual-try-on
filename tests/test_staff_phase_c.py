"""Phase C: payroll power — items, void-with-audit, bulk payday, exports."""
from urllib.parse import unquote

from models import AdminLog, Expense, SalaryPayment, SalaryPaymentItem
from migrations import MIGRATION_VERSION
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_staff_phase_b import _owner_client


def _pay(client, db_session, user, period, **fields):
    from services.payroll import current_period_key  # noqa: F401
    data = {"csrf_token": csrf_token(client, f"/admin/staff/{user.id}?tab=payroll"),
            "period_key": period, "payment_method": "cash", "note": ""}
    data.update(fields)
    return client.post(f"/admin/staff/{user.id}/salary", data=data, follow_redirects=False)


def test_itemized_lines_drive_the_math(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-items")
    worker, _ = _staff(db_session, "phasec-items", "cashier")
    worker.salary_amount = 10_000_000
    db_session.commit()

    response = _pay(client, db_session, worker, "1405-06",
                    item_kind_0="overtime", item_label_0="شیفت اضافه",
                    item_amount_0="2000000",
                    item_kind_1="deduction", item_label_1="تأخیر",
                    item_amount_1="500000")
    assert response.status_code == 303
    payment = db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == worker.id).one()
    assert (payment.gross_amount, payment.deductions, payment.net_amount) == (
        12_000_000, 500_000, 11_500_000)
    kinds = sorted(item.kind for item in payment.items)
    assert kinds == ["base", "deduction", "overtime"]
    assert db_session.query(Expense).filter(
        Expense.id == payment.expense_id).one().amount == 11_500_000

    # A deduction without a reason refuses honestly.
    bad = _pay(client, db_session, worker, "1405-07",
               item_kind_0="deduction", item_label_0="", item_amount_0="100000")
    assert bad.status_code == 303
    assert "دلیل الزامی" in unquote(bad.headers["location"])


def test_void_reverses_money_and_frees_the_month(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-void")
    worker, _ = _staff(db_session, "phasec-void", "cashier")
    worker.salary_amount = 10_000_000
    db_session.commit()
    _pay(client, db_session, worker, "1405-06")
    payment = db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == worker.id).one()

    # Reason is required.
    reasonless = client.post(f"/admin/payroll/{payment.id}/void",
                             data={"csrf_token": csrf_token(
                                 client, f"/admin/staff/{worker.id}?tab=payroll"),
                                 "reason": ""}, follow_redirects=False)
    assert reasonless.status_code == 303
    assert "دلیل ابطال الزامی" in unquote(reasonless.headers["location"])

    response = client.post(f"/admin/payroll/{payment.id}/void",
                           data={"csrf_token": csrf_token(
                               client, f"/admin/staff/{worker.id}?tab=payroll"),
                               "reason": "اشتباه ماه"}, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(payment)
    assert payment.is_voided is True
    assert payment.void_reason == "اشتباه ماه"
    expense = db_session.query(Expense).filter(
        Expense.id == payment.expense_id).one()
    assert expense.reversed_at is not None                     # money reversed
    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "salary_void", AdminLog.target_id == payment.id).one()
    assert "اشتباه ماه" in (audit.after_json or "")

    # The freed month re-pays fresh.
    again = _pay(client, db_session, worker, "1405-06")
    assert again.status_code == 303
    assert "/admin/payroll/" in again.headers["location"]
    assert "receipt" in again.headers["location"]
    live = db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == worker.id,
        SalaryPayment.is_voided == False).all()  # noqa: E712
    assert len(live) == 1


def test_bulk_payday_pays_skips_and_repeats_cleanly(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-bulk")
    first, _ = _staff(db_session, "phasec-bulk1", "cashier")
    first.salary_amount = 8_000_000
    second, _ = _staff(db_session, "phasec-bulk2", "cashier")
    second.salary_amount = 9_000_000
    db_session.commit()
    _pay(client, db_session, first, "1405-06")

    token = csrf_token(client, "/admin/staff")
    response = client.post("/admin/payroll/bulk", data={
        "csrf_token": token, "period_key": "1405-06",
        "payment_method": "cash"}, follow_redirects=False)
    assert response.status_code == 303
    location = unquote(response.headers["location"])
    assert "1 نفر ثبت شد" in location
    assert "قبلاً پرداخت شده" in location
    assert db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == second.id).count() == 1

    repeat = client.post("/admin/payroll/bulk", data={
        "csrf_token": token, "period_key": "1405-06",
        "payment_method": "cash"}, follow_redirects=False)
    assert "0 نفر ثبت شد" in unquote(repeat.headers["location"])  # idempotent


def test_nudges_name_the_unpaid(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-nudge")
    worker, _ = _staff(db_session, "phasec-nudge", "cashier")
    worker.full_name = "منتظر حقوق"
    worker.salary_amount = 7_000_000
    db_session.commit()

    page = client.get("/admin/staff").text
    assert "هنوز حقوق نگرفته‌اند" in page
    assert "منتظر حقوق" in page
    assert "پرداخت گروهی حقوق" in page


def test_yearly_totals_and_enriched_receipt(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-ytd")
    worker, _ = _staff(db_session, "phasec-ytd", "cashier")
    worker.salary_amount = 10_000_000
    db_session.commit()
    _pay(client, db_session, worker, "1405-05",
         item_kind_0="bonus", item_label_0="عیدی", item_amount_0="1000000")
    _pay(client, db_session, worker, "1405-06")

    profile = client.get(f"/admin/staff/{worker.id}?tab=payroll").text
    assert "جمع سالانه" in profile
    assert profile.count("21,000,000") >= 2                          # gross + net YTD

    payment = db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == worker.id,
        SalaryPayment.period_key == "1405-05").one()
    receipt = client.get(f"/admin/payroll/{payment.id}/receipt").text
    assert "عیدی" in receipt
    assert "جمع سال 1405" in receipt


def test_staff_and_payroll_exports_are_filtered_files(client, db_session):
    _owner_client(client, db_session, name="phasec-owner-exp")
    worker, _ = _staff(db_session, "phasec-exp", "cashier")
    worker.full_name = "خروجی‌گیر"
    worker.salary_amount = 10_000_000
    db_session.commit()
    _pay(client, db_session, worker, "1405-06")

    directory = client.get("/admin/staff/export")
    assert directory.status_code == 200
    assert "text/csv" in directory.headers["content-type"]
    assert directory.text.startswith("\ufeff")
    assert "خروجی‌گیر" in directory.text

    payments = client.get("/admin/payroll/export")
    assert payments.status_code == 200
    assert "1405-06" in payments.text

    year = client.get("/admin/payroll/export?year=1404")
    assert "1405-06" not in year.text

    cashier, password = _staff(db_session, "phasec-peek", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/staff/export", follow_redirects=False).status_code == 403
    assert client.get("/admin/payroll/export", follow_redirects=False).status_code == 403


def test_revision_29_rebuilds_payments_with_items_and_live_unique(tmp_path):
    """Old shops gain void flags plus a base line per historical payment, and
    the staff+month unique stops covering voided rows."""
    from sqlalchemy import create_engine, text
    from migrations import upgrade
    engine = create_engine(f"sqlite:///{tmp_path / 'pay29.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 28)"))
        conn.execute(text("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username VARCHAR(100))"))
        conn.execute(text("CREATE TABLE expenses (id INTEGER PRIMARY KEY, amount INTEGER)"))
        conn.execute(text("""
            CREATE TABLE salary_payments (
                id INTEGER PRIMARY KEY, staff_user_id INTEGER NOT NULL,
                period_key VARCHAR(20) NOT NULL, gross_amount INTEGER NOT NULL,
                deductions INTEGER NOT NULL, net_amount INTEGER NOT NULL,
                payment_method VARCHAR(20) NOT NULL, paid_at DATETIME NOT NULL,
                operator_user_id INTEGER NOT NULL, expense_id INTEGER NOT NULL UNIQUE,
                cash_session_id INTEGER, note TEXT, created_at DATETIME NOT NULL,
                CONSTRAINT uq_salary_payments_staff_period UNIQUE (staff_user_id, period_key))"""))
        conn.execute(text("INSERT INTO staff_users (username) VALUES ('vet')"))
        conn.execute(text("INSERT INTO expenses (amount) VALUES (9000000)"))
        conn.execute(text("""
            INSERT INTO salary_payments (staff_user_id, period_key, gross_amount,
                deductions, net_amount, payment_method, paid_at, operator_user_id,
                expense_id, note, created_at)
            VALUES (1, '1405-06', 10000000, 1000000, 9000000, 'cash',
                '2026-09-01 00:00:00', 1, 1, NULL, '2026-09-01 00:00:00')"""))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(salary_payments)")).all()}
        assert {"is_voided", "void_reason", "voided_at", "voided_by_user_id"} <= cols
        assert conn.execute(text(
            "SELECT kind, amount FROM salary_payment_items")).all() == [("base", 10000000)]
        assert conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_salary_payments_staff_period_live'"
        )).scalar() is not None


def test_brand_new_hire_is_not_dunned_or_bulk_paid(client, db_session):
    from datetime import datetime, timezone
    from services.payroll import current_period_key, is_payroll_due
    _owner_client(client, db_session, name="phasec-owner-due")
    rookie, _ = _staff(db_session, "phasec-rookie", "cashier")
    rookie.full_name = "تازه‌وارد"
    rookie.salary_amount = 10_000_000
    rookie.hire_date = datetime.now(timezone.utc)
    veteran, _ = _staff(db_session, "phasec-vet", "cashier")
    veteran.full_name = "باسابقه"
    veteran.salary_amount = 10_000_000
    db_session.commit()

    assert is_payroll_due(None, "1405-07") is True
    assert is_payroll_due(rookie.hire_date, current_period_key()) is False

    page = client.get("/admin/staff").text
    assert "هنوز حقوق نگرفته‌اند" in page
    import re
    banner_names = re.search(
        r"هنوز حقوق نگرفته‌اند</h3></div></div>\s*<p class=\"muted\">(.*?)</p>",
        page, re.S)
    assert banner_names is not None
    assert "باسابقه" in banner_names.group(1)
    assert "تازه‌وارد" not in banner_names.group(1)

    token = csrf_token(client, "/admin/staff")
    response = client.post("/admin/payroll/bulk", data={
        "csrf_token": token, "period_key": current_period_key(),
        "payment_method": "cash"}, follow_redirects=False)
    location = unquote(response.headers["location"])
    assert "ماه اولشان تمام نشده" in location
    assert db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == rookie.id).count() == 0
    assert db_session.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == veteran.id).count() == 1


def test_payroll_tab_lists_pickable_closed_months(client, db_session):
    from services.payroll import current_period_key
    _owner_client(client, db_session, name="phasec-owner-months")
    worker, _ = _staff(db_session, "phasec-months", "cashier")
    worker.salary_amount = 10_000_000
    db_session.commit()
    page = client.get(f"/admin/staff/{worker.id}?tab=payroll").text
    assert 'list="salary-months"' in page
    assert 'maxlength="7"' in page
    current = current_period_key()
    assert f'value="{current}"' not in page.split('name="period_key"')[1][:400]
