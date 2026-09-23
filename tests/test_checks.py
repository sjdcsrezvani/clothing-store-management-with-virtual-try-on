from datetime import datetime, timedelta, timezone

from models import BusinessEvent, CheckRecord, CheckReminder, StaffUser
from services.checks import add_reminders, trigger_due_reminders
from tests.conftest import csrf_token


def test_manager_can_create_and_view_issued_check(client, db_session, authed):
    token = csrf_token(client, "/admin/checks")
    response = client.post(
        "/admin/checks/add",
        data={
            "csrf_token": token,
            "provider_name": "تأمین‌کننده نمونه",
            "supplier_id": "",
            "check_number": "123456",
            "amount_rials": "1000000000",
            "issue_date": "2026/01/01",
            "due_date": "2026/02/01",
            "bank_name": "بانک نمونه",
            "account_reference": "IR0001",
            "reminder_days": "۱۴، ۷، ۳",
            "note": "پرداخت فاکتور خرید",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    check = db_session.query(CheckRecord).one()
    assert check.provider_name == "تأمین‌کننده نمونه"
    assert check.amount_rials == 1_000_000_000
    assert [reminder.days_before for reminder in check.reminders] == [14, 7, 3]
    assert db_session.query(BusinessEvent).filter(
        BusinessEvent.event_type == "CheckIssued",
        BusinessEvent.aggregate_id == check.id,
    ).count() == 1

    page = client.get("/admin/checks")
    assert page.status_code == 200
    assert "تأمین‌کننده نمونه" in page.text
    assert "1,000,000,000" in page.text
    assert "پرداخت فاکتور خرید" in page.text or "تأمین‌کننده نمونه" in page.text


def test_due_check_reminder_is_triggered_once_and_journaled(db_session, client, authed):
    operator = db_session.query(StaffUser).filter(StaffUser.username == "owner").one()
    now = datetime.now(timezone.utc)
    check = CheckRecord(
        provider_name="ارائه‌دهنده هشدار",
        amount_rials=250_000,
        issue_at=now - timedelta(days=30),
        due_at=now + timedelta(days=3),
        operator_user_id=operator.id,
    )
    db_session.add(check)
    db_session.flush()
    add_reminders(db_session, check, [3])
    db_session.commit()

    reminder = db_session.query(CheckReminder).filter(CheckReminder.check_id == check.id).one()
    reminder.remind_at = now - timedelta(minutes=1)
    db_session.commit()

    assert trigger_due_reminders(db_session, now) == 1
    db_session.commit()
    db_session.refresh(reminder)
    assert reminder.status == "triggered"
    assert db_session.query(BusinessEvent).filter(
        BusinessEvent.event_type == "CheckReminderTriggered",
        BusinessEvent.aggregate_id == check.id,
    ).count() == 1

    assert trigger_due_reminders(db_session, now + timedelta(minutes=5)) == 0


def test_rescheduling_a_check_dismisses_old_active_reminders(db_session, client, authed):
    operator = db_session.query(StaffUser).filter(StaffUser.username == "owner").one()
    now = datetime.now(timezone.utc)
    check = CheckRecord(
        provider_name="ارائه‌دهنده زمان‌بندی",
        amount_rials=300_000,
        issue_at=now - timedelta(days=1),
        due_at=now + timedelta(days=30),
        operator_user_id=operator.id,
    )
    db_session.add(check)
    db_session.flush()
    add_reminders(db_session, check, [14, 7, 3])
    db_session.commit()

    old_triggered = db_session.query(CheckReminder).filter(
        CheckReminder.check_id == check.id,
        CheckReminder.days_before == 14,
    ).one()
    old_triggered.status = "triggered"
    db_session.commit()

    add_reminders(db_session, check, [7, 3])
    db_session.commit()

    db_session.expire_all()
    reminders = db_session.query(CheckReminder).filter(CheckReminder.check_id == check.id).all()
    assert old_triggered.status == "dismissed"
    assert {reminder.days_before for reminder in reminders if reminder.status == "pending"} == {7, 3}
    assert all(reminder.status != "triggered" for reminder in reminders)


def test_only_owner_can_change_default_check_reminders(client, db_session):
    from tests.test_roles import _session_as, _staff

    manager, password = _staff(db_session, "checks-manager", "manager")
    _session_as(client, manager, password)
    token = csrf_token(client, "/admin/checks")
    response = client.post(
        "/admin/checks/settings",
        data={"csrf_token": token, "reminder_days": "30, 14, 7", "enabled": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 403
