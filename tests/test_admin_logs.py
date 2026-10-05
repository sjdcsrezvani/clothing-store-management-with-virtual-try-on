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


def test_day_groups_and_relative_time(client, db_session):
    _owner_client(client, db_session, name="logs-owner-days")
    db_session.query(AdminLog).delete()
    db_session.commit()
    now = datetime.now(timezone.utc)
    _log(db_session, "login", "ردیف امروز", created_at=now - timedelta(minutes=5))
    _log(db_session, "logout", "ردیف دیروز", created_at=now - timedelta(days=1, hours=1))
    page = client.get("/admin/logs").text
    assert "امروز" in page and "دیروز" in page
    assert "دقیقه پیش" in page  # relative tail under the fresh stamp


def test_target_links_ip_truncation_and_diff_dialog(client, db_session):
    owner, _ = _owner_client(client, db_session, name="logs-owner-links")
    row = _log(db_session, "refund", "x" * 120, user=owner,
               target_type="sale", target_id=7,
               before_json='{"a": 1}', after_json='{"a": 2}')
    row.ip_address = "1.2.3.4"
    db_session.commit()
    page = client.get("/admin/logs").text
    assert 'href="/admin/invoice/7"' in page and "فاکتور #7" in page
    assert "1.2.3.4" in page
    assert "<details" in page and "تغییرات" in page  # long detail + diff button
    assert f'id="logdiff-action-{row.id}"' in page and "data-dialog" in page
    assert "&#34;a&#34;: 1" in page  # pretty JSON, still HTML-escaped


def test_unmapped_targets_stay_plain(client, db_session):
    _owner_client(client, db_session, name="logs-owner-plain")
    _log(db_session, "payment_reverse", "برگشت مبهم",
         target_type="payment", target_id=3)  # ambiguous: never linked
    page = client.get("/admin/logs").text
    assert "برگشت مبهم" in page
    assert "/admin/invoice/3" not in page and "payment" not in page.replace(
        "payment_reverse", "")


def test_csv_export_matches_filtered_screen(client, db_session):
    _owner_client(client, db_session, name="logs-owner-csv")
    _log(db_session, "logout", "ردیف فایل")
    _log(db_session, "login", "ردیف بیرون")
    res = client.get("/admin/logs/export?action=logout")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    body = res.content.decode("utf-8-sig")
    assert body.splitlines()[0] == "زمان,عملیات,کننده,پیوند,آی‌پی,جزئیات"
    assert "ردیف فایل" in body and "خروج" in body
    assert "ردیف بیرون" not in body
    page = client.get("/admin/logs?action=logout").text
    assert "export%3Faction%3Dlogout" not in page  # sanity: link is plain
    assert "/admin/logs/export?action=logout" in page


def test_source_tabs_switch_streams(client, db_session):
    from services.events import append_event
    owner, _ = _owner_client(client, db_session, name="logs-owner-tabs")
    _log(db_session, "logout", "ردیف عملیات")
    append_event(db_session, "SaleCompleted", "sale", aggregate_id=9,
                 actor_user_id=owner.id, payload={"total": 5})
    db_session.commit()

    actions = client.get("/admin/logs").text
    assert "عملیات کاربران" in actions and "رویدادهای فروشگاه" in actions
    assert "ردیف عملیات" in actions and "ثبت فروش" not in actions

    events = client.get("/admin/logs?source=events").text
    assert "ثبت فروش" in events and "SaleCompleted" in events
    assert f"/admin/staff/{owner.id}" in events  # event actor links too
    assert 'href="/admin/invoice/9"' in events  # aggregate reuses target map
    assert "ردیف عملیات" not in events

    merged = client.get("/admin/logs?source=all").text
    assert "ردیف عملیات" in merged and "ثبت فروش" in merged
    assert "۱۰۰ ردیف آخر" in merged  # merged depth is capped, honestly said
    assert "نمایش ۱۰۰ ردیف بعدی" not in merged


def test_latest_endpoint_reports_per_stream_max(client, db_session):
    from services.events import append_event
    _owner_client(client, db_session, name="logs-owner-latest")
    row = _log(db_session, "logout", "ردیف سقف")
    db_session.commit()
    data = client.get("/admin/logs/latest").json()
    assert data["actions_max"] == row.id and data["events_max"] == 0


def test_archive_files_then_deletes_old_rows(client, db_session):
    from pathlib import Path
    from tests.conftest import csrf_token
    _owner_client(client, db_session, name="logs-owner-archive")
    old = _log(db_session, "logout", "ردیف کهنه",
               created_at=datetime.now(timezone.utc) - timedelta(days=400))
    fresh = _log(db_session, "logout", "ردیف تازه")
    db_session.commit()
    old_id, fresh_id = old.id, fresh.id

    assert "بایگانی و حذف" in client.get("/admin/logs").text
    res = client.post("/admin/logs/archive",
                      data={"csrf_token": csrf_token(client, "/admin/logs")},
                      follow_redirects=False)
    assert res.status_code == 303
    files = sorted(Path("backups").glob("logs_archive_*.csv"))
    assert files, "archive file must land before rows go"
    try:
        body = files[-1].read_text(encoding="utf-8-sig")
        assert "ردیف کهنه" in body
        assert db_session.query(AdminLog).filter(
            AdminLog.id == old_id).count() == 0
        assert db_session.query(AdminLog).filter(
            AdminLog.id == fresh_id).count() == 1
        assert db_session.query(AdminLog).filter(
            AdminLog.action == "logs_archive").count() == 1
    finally:
        for stale in files:
            stale.unlink()
