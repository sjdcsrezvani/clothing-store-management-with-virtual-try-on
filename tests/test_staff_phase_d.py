"""Phase D: people intelligence — terms, attendance, performance, timeline."""
from urllib.parse import unquote

from models import AdminLog, AttendanceRecord, StaffUser
from migrations import MIGRATION_VERSION
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_staff_phase_b import _owner_client


def _hire(client, db_session, username, **fields):
    data = {"csrf_token": csrf_token(client, "/admin/staff/new"),
            "username": username, "password": "hire-pass", "role": "cashier"}
    data.update(fields)
    return client.post("/admin/staff", data=data, follow_redirects=False)


def test_contract_term_derives_its_end_date(client, db_session):
    _owner_client(client, db_session, name="phased-owner-term")
    response = _hire(client, db_session, "phased-term",
                     hire_date="1405/01/01", contract_term_months="6")
    assert response.status_code == 303
    user = db_session.query(StaffUser).filter(
        StaffUser.username == "phased-term").one()
    assert user.contract_term_months == 6
    # 1405/01/01 + 6 Jalali months = 1405/07/01.
    assert user.contract_end_date is not None
    assert (user.contract_end_date.year, user.contract_end_date.month,
            user.contract_end_date.day) == (2026, 9, 23)

    termless = _hire(client, db_session, "phased-noterm",
                     contract_term_months="3")
    assert "تاریخ شروع الزامی" in unquote(termless.headers["location"])

    bad = _hire(client, db_session, "phased-badterm",
                hire_date="1405/01/01", contract_term_months="9")
    assert "مدت قرارداد نامعتبر" in unquote(bad.headers["location"])


def test_expiring_contracts_flag_in_list_and_profile(client, db_session):
    from datetime import datetime, timedelta, timezone
    _owner_client(client, db_session, name="phased-owner-flag")
    soon, _ = _staff(db_session, "phased-soon", "cashier")
    soon.contract_end_date = datetime.now(timezone.utc) + timedelta(days=3)
    past, _ = _staff(db_session, "phased-past", "cashier")
    past.contract_end_date = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    page = client.get("/admin/staff").text
    assert "قرارداد: رو به پایان" in page
    assert "قرارداد: پایان‌یافته" in page

    profile = client.get(f"/admin/staff/{soon.id}").text
    assert "قرارداد: رو به پایان" in profile
    assert "روز مانده" in profile


def test_attendance_marks_rewrite_and_feed_leave_balances(client, db_session):
    _owner_client(client, db_session, name="phased-owner-att")
    worker, _ = _staff(db_session, "phased-att", "cashier")

    def mark(day, status):
        return client.post(f"/admin/staff/{worker.id}/attendance", data={
            "csrf_token": csrf_token(client, f"/admin/staff/{worker.id}?tab=attendance"),
            "day": day, "status": status, "note": ""}, follow_redirects=False)

    assert mark("1405/06/10", "present").status_code == 303
    assert mark("1405/06/10", "annual_leave").status_code == 303  # rewrite
    assert mark("1405/06/11", "sick_leave").status_code == 303
    assert db_session.query(AttendanceRecord).filter(
        AttendanceRecord.staff_user_id == worker.id).count() == 2

    bad = mark("1405/06/12", "vacation")
    assert "وضعیت حضور نامعتبر" in unquote(bad.headers["location"])

    page = client.get(f"/admin/staff/{worker.id}?tab=attendance&month=1405-06").text
    assert "مرخصی استحقاقی" in page
    assert "استحقاقی مانده: 25 روز" in page
    assert "استعلاجی امسال: 1 روز" in page


def test_overview_shows_performance_and_history_shows_trail(client, db_session):
    _owner_client(client, db_session, name="phased-owner-perf")
    _hire(client, db_session, "phased-perf", full_name="فروشنده نمونه")

    worker = db_session.query(StaffUser).filter(
        StaffUser.username == "phased-perf").one()
    overview = client.get(f"/admin/staff/{worker.id}").text
    assert "عملکرد ماه" in overview
    assert "0 فاکتور" in overview

    history = client.get(f"/admin/staff/{worker.id}?tab=history").text
    assert "استخدام" in history
    assert "phased-perf" in history


def test_structured_emergency_contact_saved(client, db_session):
    _owner_client(client, db_session, name="phased-owner-emg")
    _hire(client, db_session, "phased-emg", emergency_name="مادر",
          emergency_relation="مادر", emergency_phone="09120000000")
    user = db_session.query(StaffUser).filter(
        StaffUser.username == "phased-emg").one()
    assert (user.emergency_name, user.emergency_relation,
            user.emergency_phone) == ("مادر", "مادر", "09120000000")


def test_revision_30_adds_terms_emergency_and_attendance(tmp_path):
    """Old shops gain term/emergency columns (NULL = legacy) and the
    person-day table with its uniqueness."""
    from sqlalchemy import create_engine, text
    from migrations import upgrade
    engine = create_engine(f"sqlite:///{tmp_path / 'ppl30.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 29)"))
        conn.execute(text("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username VARCHAR(100))"))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(staff_users)")).all()}
        assert {"contract_term_months", "emergency_name", "emergency_relation",
                "emergency_phone"} <= cols
        assert conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attendance_records'"
        )).scalar() == "attendance_records"
