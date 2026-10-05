"""Admin audit trail phase 1: combined filters, load-more, shared Fa labels."""
from datetime import datetime, timedelta, timezone

import jdatetime

from models import AdminLog
from tests.test_roles import _staff
from tests.test_staff_phase_b import _owner_client


def _log(db_session, action, detail, user=None, created_at=None, **extra):
    row = AdminLog(
        action=action, detail=detail,
        staff_user_id=user.id if user else None,
        created_at=created_at or datetime.now(timezone.utc),
    )
    for key, value in extra.items():
        setattr(row, key, value)
    db_session.add(row)
    db_session.commit()
    return row


def _jalali_day(dt):
    return jdatetime.datetime.fromtimestamp(dt.timestamp()).strftime("%Y/%m/%d")


def test_combined_filters_narrow_to_one_row(client, db_session):
    _owner_client(client, db_session, name="logs-owner-filters")
    cashier, _ = _staff(db_session, "logs-cashier", "cashier")
    manager, _ = _staff(db_session, "logs-manager", "manager")
    day_a = datetime.now(timezone.utc) - timedelta(days=9)
    day_b = datetime.now(timezone.utc) - timedelta(days=2)
    _log(db_session, "salary_void", "ردیف هدف", user=cashier, created_at=day_a)
    _log(db_session, "salary_void", "همان عمل، کننده دیگر", user=manager, created_at=day_a)
    _log(db_session, "staff_update", "عمل دیگر", user=cashier, created_at=day_b)

    page = client.get(
        f"/admin/logs?action=salary_void&actor={cashier.id}"
        f"&from={_jalali_day(day_a)}&to={_jalali_day(day_a)}").text
    assert "ردیف هدف" in page
    assert "کننده دیگر" not in page
    assert "عمل دیگر" not in page
    # Fa badge + Latin code side by side, danger tone for the void.
    assert "ابطال حقوق" in page and "salary_void" in page
    assert "badge-danger" in page
    # Actor renders with a profile link; the form echoes the filters.
    assert f"/admin/staff/{cashier.id}" in page
    # The form echoes the filters back as selected options.
    assert f'<option value="salary_void" selected>' in page
    assert f'<option value="{cashier.id}" selected>' in page


def test_garbage_filter_values_degrade_to_unfiltered(client, db_session):
    _owner_client(client, db_session, name="logs-owner-garbage")
    _log(db_session, "logout", "ردیف ماندگار")
    page = client.get("/admin/logs?action=nope&actor=999999&from=not-a-date").text
    assert "ردیف ماندگار" in page
    assert 'value="nope"' not in page  # unknown values echo back cleared


def test_load_more_paginates_without_filters(client, db_session):
    _owner_client(client, db_session, name="logs-owner-more")
    base = datetime.now(timezone.utc) - timedelta(days=30)
    for i in range(105):
        _log(db_session, "logout", f"ردیف انبوه {i}",
             created_at=base + timedelta(minutes=i))
    first = client.get("/admin/logs").text
    assert "نمایش ۱۰۰ ردیف بعدی" in first
    assert "offset=100" in first
    second = client.get("/admin/logs?offset=100").text
    assert "نمایش ۱۰۰ ردیف بعدی" not in second


def test_empty_states_stay_smart(client, db_session):
    _owner_client(client, db_session, name="logs-owner-empty")
    db_session.query(AdminLog).delete()  # the login itself is a row
    db_session.commit()
    assert "هنوز عملیاتی ثبت نشده است" in client.get("/admin/logs").text
    _log(db_session, "logout", "ردیف بیرون از بازه")
    future = _jalali_day(datetime.now(timezone.utc) + timedelta(days=30))
    filtered = client.get(f"/admin/logs?action=logout&from={future}").text
    assert "ردیف بیرون از بازه" not in filtered
    assert "پاک کردن فیلترها" in filtered


def test_staff_timeline_uses_shared_labels(client, db_session):
    owner, _ = _owner_client(client, db_session, name="logs-owner-shared")
    _log(db_session, "staff_update", "ویرایش آزمایشی",
         target_type="staff_user", target_id=owner.id)
    page = client.get(f"/admin/staff/{owner.id}?tab=history").text
    assert "ویرایش پرونده" in page
