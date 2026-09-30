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


def test_only_owner_can_change_check_settings_and_values_are_validated(client, db_session):
    """Reminder defaults live in /admin/settings now: validated, normalised."""
    from urllib.parse import unquote_plus
    from tests.test_roles import _session_as, _staff

    manager, password = _staff(db_session, "checks-manager", "manager")
    _session_as(client, manager, password)
    token = csrf_token(client, "/admin/checks")
    response = client.post(
        "/admin/settings",
        data={"csrf_token": token, "check_default_reminders": "30, 14, 7"},
        follow_redirects=False,
    )
    assert response.status_code == 403

    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client, "/admin/settings")
    bad = client.post(
        "/admin/settings",
        data={"csrf_token": token, "check_default_reminders": "banana"},
        follow_redirects=False,
    )
    assert bad.status_code == 303
    assert "روزهای هشدار چک" in unquote_plus(bad.headers["location"])

    good = client.post(
        "/admin/settings",
        data={"csrf_token": token, "check_default_reminders": "30,14,7",
              "check_upcoming_days": "30", "check_reminders_enabled": "1"},
        follow_redirects=False,
    )
    assert good.status_code == 303
    from models import Settings
    stored = {row.key: row.value for row in db_session.query(Settings).all()}
    assert stored["check_default_reminders"] == "30, 14, 7"
    assert stored["check_upcoming_days"] == "30"

    from services.checks import get_default_reminder_days, get_upcoming_days
    assert get_default_reminder_days(db_session) == [30, 14, 7]
    assert get_upcoming_days(db_session) == 30
    assert "30 روز آینده" in client.get("/admin/checks").text


def test_a_cheque_without_its_number_is_refused(client, db_session, authed):
    """The leaf it was torn from is the only identity a cheque has at the bank."""
    from urllib.parse import unquote_plus
    token = csrf_token(client, "/admin/checks")
    response = client.post(
        "/admin/checks/add",
        data={
            "csrf_token": token,
            "provider_name": "بدون شماره",
            "check_number": "",
            "amount_rials": "50000000",
            "due_date": "2027/03/01",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "شماره چک الزامی است" in unquote_plus(response.headers["location"])
    assert db_session.query(CheckRecord).count() == 0


def test_the_add_form_confirms_and_suggests_without_mixing(client, db_session, authed):
    """Modal, script, and datalists drawn from the shop's own words only."""
    import re
    token = csrf_token(client, "/admin/checks")
    client.post(
        "/admin/checks/add",
        data={
            "csrf_token": token,
            "provider_name": "پرداخت‌کننده پیشین",
            "check_number": "777001",
            "amount_rials": "200000000",
            "due_date": "2027/04/01",
            "bank_name": "بانک پیشین",
        },
        follow_redirects=False,
    )
    page = client.get("/admin/checks")
    assert page.status_code == 200
    assert 'id="check-add-form"' in page.text
    assert 'id="check-add-confirm"' in page.text
    assert "/static/js/checks.js" in page.text
    assert '<option value="پرداخت‌کننده پیشین">' in page.text
    assert '<option value="بانک پیشین">' in page.text
    # Same-named fields elsewhere must never leak into these suggestions.
    for field in ("provider_name", "bank_name"):
        tag = re.search(rf'<input[^>]*name="{field}"[^>]*>', page.text)
        assert tag and 'autocomplete="off"' in tag.group(0), field


def _add_check(client, number, amount=None, due="2027/05/01", **extra):
    # The figure varies per number: identical provider + figure + due within
    # two minutes is the double-click guard's definition, not two cheques.
    token = csrf_token(client, "/admin/checks")
    data = {
        "csrf_token": token,
        "provider_name": "گیرنده آزمون",
        "check_number": number,
        "amount_rials": amount or str(300_000_000 + int(number) % 1_000_000),
        "due_date": due,
    }
    data.update(extra)
    return client.post("/admin/checks/add", data=data, follow_redirects=False)


def test_issued_words_edit_but_money_and_time_do_not(client, db_session, authed):
    """Paperwork is correctable while open; the promise itself is not."""
    assert _add_check(client, "111001").status_code == 303
    check = db_session.query(CheckRecord).one()
    token = csrf_token(client, "/admin/checks")
    response = client.post(
        f"/admin/checks/{check.id}/edit",
        data={"csrf_token": token, "provider_name": "گیرنده اصلاح‌شده",
              "check_number": "111002", "bank_name": "بانک نو",
              "account_reference": "", "note": "اصلاح"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(check)
    assert check.provider_name == "گیرنده اصلاح‌شده"
    assert check.check_number == "111002"
    assert check.bank_name == "بانک نو"
    assert check.amount_rials == 300_000_000 + 111001

    # Empty number and non-issued rows are refused, not half-saved.
    refused = client.post(
        f"/admin/checks/{check.id}/edit",
        data={"csrf_token": token, "provider_name": "x", "check_number": ""},
        follow_redirects=False,
    )
    assert refused.status_code == 303
    db_session.refresh(check)
    assert check.provider_name == "گیرنده اصلاح‌شده"

    _set_status(client, check.id, "paid")
    locked = client.post(
        f"/admin/checks/{check.id}/edit",
        data={"csrf_token": token, "provider_name": "y", "check_number": "111003"},
        follow_redirects=False,
    )
    assert locked.status_code == 303
    db_session.refresh(check)
    assert check.provider_name == "گیرنده اصلاح‌شده"


def _set_status(client, check_id, status):
    token = csrf_token(client, "/admin/checks")
    return client.post(
        f"/admin/checks/{check_id}/status",
        data={"csrf_token": token, "status": status},
        follow_redirects=False,
    )


def test_delete_only_removes_a_cheque_that_never_lived(client, db_session, authed):
    """No fired reminder, no journal beyond the recording — else cancel."""
    assert _add_check(client, "222001").status_code == 303
    fresh = db_session.query(CheckRecord).filter(CheckRecord.check_number == "222001").one()
    token = csrf_token(client, "/admin/checks")
    assert client.post(f"/admin/checks/{fresh.id}/delete",
                       data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert db_session.query(CheckRecord).filter(CheckRecord.id == fresh.id).count() == 0

    assert _add_check(client, "222002").status_code == 303
    lived = db_session.query(CheckRecord).filter(CheckRecord.check_number == "222002").one()
    _set_status(client, lived.id, "paid")
    refused = client.post(f"/admin/checks/{lived.id}/delete",
                          data={"csrf_token": token}, follow_redirects=False)
    assert refused.status_code == 303
    assert db_session.query(CheckRecord).filter(CheckRecord.id == lived.id).count() == 1

    assert _add_check(client, "222003").status_code == 303
    reminded = db_session.query(CheckRecord).filter(CheckRecord.check_number == "222003").one()
    reminder = db_session.query(CheckReminder).filter(CheckReminder.check_id == reminded.id).first()
    reminder.status = "triggered"
    db_session.commit()
    refused = client.post(f"/admin/checks/{reminded.id}/delete",
                          data={"csrf_token": token}, follow_redirects=False)
    assert refused.status_code == 303
    assert db_session.query(CheckRecord).filter(CheckRecord.id == reminded.id).count() == 1


def test_bounced_stays_flagged_until_resolved_or_reissued(client, db_session, authed):
    """برگشتی is terminal as a status and open as a task, until signed off."""
    from urllib.parse import unquote_plus
    assert _add_check(client, "333001").status_code == 303
    check = db_session.query(CheckRecord).filter(CheckRecord.check_number == "333001").one()
    assert _set_status(client, check.id, "bounced").status_code == 303
    db_session.refresh(check)
    assert check.needs_followup is True

    page = client.get("/admin/checks")
    assert "نیازمند پیگیری" in page.text
    flagged = client.get("/admin/checks?status=needs_action")
    assert f"?reissue={check.id}" in flagged.text
    calm = client.get("/admin/checks?status=paid")
    assert f"?reissue={check.id}" not in calm.text

    token = csrf_token(client, "/admin/checks")
    assert client.post(f"/admin/checks/{check.id}/resolve",
                       data={"csrf_token": token}, follow_redirects=False).status_code == 303
    db_session.refresh(check)
    assert check.needs_followup is False
    assert '<span class="badge badge-used">نیازمند پیگیری</span>' not in client.get("/admin/checks").text


def test_reissue_prefills_and_closes_the_loop(client, db_session, authed):
    """The replacement is one confirmation, and the old row signs off."""
    assert _add_check(client, "444001", bank_name="بانک کهنه").status_code == 303
    old = db_session.query(CheckRecord).filter(CheckRecord.check_number == "444001").one()
    assert _set_status(client, old.id, "bounced").status_code == 303

    form = client.get(f"/admin/checks?reissue={old.id}")
    assert "صدور مجدد چک" in form.text
    assert f'name="reissue_id" value="{old.id}"' in form.text
    assert "بانک کهنه" in form.text

    token = csrf_token(client, "/admin/checks")
    response = client.post(
        "/admin/checks/add",
        data={"csrf_token": token, "provider_name": "گیرنده آزمون",
              "check_number": "444002", "amount_rials": "300000000",
              "due_date": "2027/06/01", "reissue_id": str(old.id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    from urllib.parse import unquote_plus
    assert "پیگیری چک قبلی بسته شد" in unquote_plus(response.headers["location"])
    db_session.refresh(old)
    assert old.needs_followup is False
    assert db_session.query(CheckRecord).filter(CheckRecord.check_number == "444002").count() == 1


def test_paid_rows_show_their_date_and_actions_confirm(client, db_session, authed):
    """paid_at was stored and hidden; row actions share one modal surface."""
    from services._common import jalali_str
    assert _add_check(client, "555001").status_code == 303
    assert _add_check(client, "555002").status_code == 303
    check = db_session.query(CheckRecord).filter(CheckRecord.check_number == "555001").one()
    assert _set_status(client, check.id, "paid").status_code == 303
    db_session.refresh(check)
    page = client.get("/admin/checks")
    assert "پرداخت‌شده" in page.text
    assert jalali_str(check.paid_at, False) in page.text
    assert 'id="check-action-confirm"' in page.text
    assert page.text.count("data-check-action") >= 3


def _seed_checks(db_session, count, **overrides):
    from datetime import datetime, timezone, timedelta
    operator = db_session.query(StaffUser).filter(StaffUser.username == "owner").one()
    now = datetime.now(timezone.utc)
    rows = []
    for index in range(count):
        check = CheckRecord(
            provider_name=f"گیرنده {index}",
            check_number=f"900{100 + index}",
            amount_rials=50_000_000 + index,
            issue_at=now - timedelta(days=60),
            due_at=now + timedelta(days=30),
            operator_user_id=operator.id,
        )
        for key, value in overrides.items():
            setattr(check, key, value)
        db_session.add(check)
        rows.append(check)
    db_session.commit()
    return rows


def test_cheque_list_pages_with_numbers_and_searches_figures(client, db_session, authed):
    """Twenty-five rows is three pages of ten; a figure finds its cheque."""
    _seed_checks(db_session, 25)
    first = client.get("/admin/checks?per_page=10")
    assert 'aria-label="صفحه 3"' in first.text
    third = client.get("/admin/checks?per_page=10&page=3")
    assert 'aria-current="page">3<' in third.text
    assert 'aria-current="page">3<' in client.get("/admin/checks?per_page=10&page=9").text

    by_amount = client.get("/admin/checks?q=50000000")
    assert "گیرنده 0" in by_amount.text
    assert by_amount.text.count('data-label="دریافت‌کننده"') == 1
    by_text = client.get("/admin/checks?q=گیرنده 1")
    assert "<mark>" in by_text.text


def test_bounced_card_counts_and_export_files_the_view(client, db_session, authed):
    """The fifth card watches برگشتی; the file answers the viewed rows."""
    _seed_checks(db_session, 2)
    doomed = db_session.query(CheckRecord).order_by(CheckRecord.id.asc()).first()
    token = csrf_token(client, "/admin/checks")
    client.post(f"/admin/checks/{doomed.id}/status",
                data={"csrf_token": token, "status": "bounced"}, follow_redirects=False)

    page = client.get("/admin/checks")
    assert 'href="/admin/checks?status=bounced"' in page.text

    whole = client.get("/admin/checks/export")
    assert whole.status_code == 200
    assert "text/csv" in whole.headers["content-type"]
    assert whole.text.startswith("\ufeff")
    assert "50000000" in whole.text
    assert "50,000,000" not in whole.text

    only_bounced = client.get("/admin/checks/export?status=bounced")
    assert f"\n{doomed.id}," in only_bounced.text
    assert only_bounced.text.count("\n") == 2  # header + the one row


def test_reminder_figure_is_plain_text_and_alerts_lead(client, db_session, authed):
    """No Python repr leaks into the field; triggered alerts sit above the list."""
    from datetime import datetime, timezone, timedelta
    _seed_checks(db_session, 1)
    check = db_session.query(CheckRecord).one()
    add_reminders(db_session, check, [14, 7, 3])
    db_session.commit()
    body = client.get("/admin/checks").text
    assert 'value="14، 7، 3"' in body
    assert "[14" not in body

    reminder = db_session.query(CheckReminder).filter(CheckReminder.check_id == check.id).first()
    reminder.remind_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    triggered = client.get("/admin/checks").text
    assert 'id="check-alerts"' in triggered
    assert 'role="alert"' not in triggered
    assert triggered.index('id="check-alerts"') < triggered.index('id="checks-list"')
