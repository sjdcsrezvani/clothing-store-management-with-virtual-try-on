"""The month's reading on the dashboard, and the monthly SMS it outlived.

سود و زیان writes one sentence per chart from the figures the chart draws; the
dashboard's «روایت این ماه» strip reads those same sentences through
``month_reading``, so the page and the strip cannot disagree about the month.

The monthly SMS digest that used to share the reading is gone. These tests keep
the strip's guarantees and guard the removal: no settings on the SMS page, no
send route, no scheduler pass — while historical digest rows stay legible in
the log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── the sentences stay the page's sentences ──────────────────────────────────

def test_a_named_span_says_the_month_where_the_page_says_the_range():
    """The only word that may differ between the page and a named month."""
    from services.chart_notes import chart_notes

    daily = [{"date": "۰۵/۰۱", "revenue": 1000, "profit": 400},
             {"date": "۰۵/۰۲", "revenue": 3000, "profit": 900}]
    categories = [{"category": "پسرانه", "revenue": 3000},
                  {"category": "دخترانه", "revenue": 1000}]
    page = chart_notes(daily=daily, categories=categories)
    month = chart_notes(daily=daily, categories=categories, span="این ماه")

    assert page["dailyChart"].replace("این بازه", "این ماه") == month["dailyChart"]
    assert page["categoryChart"].replace("این بازه", "این ماه") == month["categoryChart"]
    # A span that changes nothing would make this test pointless.
    assert month["dailyChart"] != page["dailyChart"]


def test_an_empty_period_names_its_month_not_the_page_range():
    """A quiet month is stated, and stated about *that* month."""
    from services.chart_notes import chart_notes

    notes = chart_notes(daily=[], span="ماه مرداد")
    assert notes["dailyChart"] == "در ماه مرداد فروشی ثبت نشده."
    # The page's own empty sentence is untouched.
    assert chart_notes(daily=[])["dailyChart"] == "در این بازه فروشی ثبت نشده."


def test_the_dashboard_strip_reads_the_month_reading(db_session):
    """One reading, one reader — by construction, not by discipline."""
    from services.dashboard import _month_reading
    from services.month_reading import month_reading

    strip = _month_reading(db_session)
    assert strip, "an empty shop still gets sentences — the quiet is the news"

    start = datetime.now(timezone.utc) - timedelta(days=10)
    reading = month_reading(db_session, start=start,
                            end=datetime.now(timezone.utc), span="این ماه")
    # Every sentence the strip carries is verbatim one of the reading's.
    assert set(strip) <= set(reading["notes"].values())


# ── the dashboard strip ──────────────────────────────────────────────────────

def test_the_owner_sees_the_reading_and_the_manager_never_does(client, db_session):
    from tests.test_roles import _session_as, _staff

    owner, password = _staff(db_session, "reading-owner", "owner")
    _session_as(client, owner, password)
    html = client.get("/admin").text
    assert "روایت این ماه" in html
    assert "reading-list" in html

    manager, password = _staff(db_session, "reading-manager", "manager")
    _session_as(client, manager, password)
    html = client.get("/admin").text
    assert "روایت این ماه" not in html


def test_the_strip_is_never_computed_for_a_manager(client, db_session, monkeypatch):
    """Laziness, the same discipline the other owner-only cards keep."""
    import services.dashboard as dashboard
    from tests.test_roles import _session_as, _staff

    def _refuse(*args, **kwargs):
        raise AssertionError("the month reading ran for a manager")

    monkeypatch.setattr(dashboard, "_month_reading", _refuse)
    manager, password = _staff(db_session, "reading-lazy", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin").status_code == 200


# ── the monthly SMS is gone ──────────────────────────────────────────────────

def test_the_sms_page_offers_no_monthly_digest(client, db_session):
    from tests.test_roles import _session_as, _staff

    owner, password = _staff(db_session, "digest-gone-page", "owner")
    _session_as(client, owner, password)
    html = client.get("/admin/sms").text
    assert "monthly_digest_phone" not in html
    assert "monthly_digest_day" not in html
    assert "پیش‌نمایش خلاصه" not in html
    assert 'action="/admin/sms/digest/send-now"' not in html


def test_the_monthly_send_routes_are_gone(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _session_as, _staff

    owner, password = _staff(db_session, "digest-gone-routes", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/admin/sms")
    send = client.post("/admin/sms/digest/send-now",
                       data={"csrf_token": token}, follow_redirects=False)
    resend = client.post("/admin/sms/history/digest/resend",
                         data={"csrf_token": token, "ref": "digest:1405-05"},
                         follow_redirects=False)
    assert send.status_code == 404
    assert resend.status_code == 404


def test_the_scheduler_has_no_monthly_digest_pass():
    scheduler = (ROOT / "services/scheduler.py").read_text()
    assert "fire_monthly_digest" not in scheduler
    import services.month_reading as module
    assert not hasattr(module, "fire_monthly_digest")
    assert not hasattr(module, "send_digest_now")


def test_a_historical_digest_row_keeps_its_month_in_history(client, db_session):
    """Old rows stay readable; removing the send must not erase the log."""
    from models import SmsMessage
    from tests.test_roles import _session_as, _staff

    db_session.add(SmsMessage(
        phone="09123456789",
        body="گزارش مرداد ۱۴۰۵: فروش این ماه ثبت شد.",
        status="sent",
        source="monthly_digest",
        ref="digest:1405-05",
    ))
    db_session.commit()

    owner, password = _staff(db_session, "digest-history-owner", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin/sms/history?source=monthly_digest").text
    assert "digest-month-chip" in page
    assert "مرداد ۱۴۰۵" in page
    # The resend form is gone with the send it fed.
    assert "ارسال دوباره خلاصه یک ماه" not in page
