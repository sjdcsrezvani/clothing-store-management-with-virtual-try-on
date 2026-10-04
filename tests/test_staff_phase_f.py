"""Remaining ideas: explicable caps, login attendance, math preview."""
from models import AttendanceRecord, StaffUser
from tests.conftest import csrf_token
from tests.test_roles import _login, _session_as, _staff
from tests.test_staff_phase_b import _owner_client


def test_first_login_marks_present_once_and_spares_manual_leave(client, db_session):
    _owner_client(client, db_session, name="phasef-owner-login")
    worker, password = _staff(db_session, "phasef-login", "cashier")

    _login(client, "phasef-login", password)
    rows = db_session.query(AttendanceRecord).filter(
        AttendanceRecord.staff_user_id == worker.id).all()
    assert len(rows) == 1
    assert rows[0].status == "present"
    assert rows[0].recorded_by_user_id is None

    _login(client, "phasef-login", password)                  # repeat login
    assert db_session.query(AttendanceRecord).filter(
        AttendanceRecord.staff_user_id == worker.id).count() == 1

    # A hand-marked sick day survives the next login untouched.
    record = rows[0]
    record.status = "sick_leave"
    record.recorded_by_user_id = worker.id
    db_session.commit()
    _login(client, "phasef-login", password)
    db_session.refresh(record)
    assert record.status == "sick_leave"


def test_caps_tab_explains_consequences(client, db_session):
    owner, _ = _owner_client(client, db_session, name="phasef-owner-caps")
    worker, _ = _staff(db_session, "phasef-caps", "cashier")
    page = client.get(f"/admin/staff/{worker.id}?tab=permissions").text
    assert "روشن</strong> همان دسترسی نقش است" in page
    assert "پذیرفته نیست" in page
    assert "cap-row" in page


def test_payroll_tab_shows_math_preview_hooks(client, db_session):
    _owner_client(client, db_session, name="phasef-owner-math")
    worker, _ = _staff(db_session, "phasef-math", "cashier")
    worker.salary_amount = 10_000_000
    db_session.commit()
    page = client.get(f"/admin/staff/{worker.id}?tab=payroll").text
    assert 'data-base-amount="10000000"' in page
    assert 'id="salary-math-preview"' in page
    assert "اضافه می‌کند" in page
