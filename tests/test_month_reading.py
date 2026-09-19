"""The month's reading, reused where the owner already is.

سود و زیان writes one sentence per chart from the figures the chart draws.
Those same sentences are now read in two more places — the dashboard's «روایت
این ماه» strip and the owner's monthly SMS — and both reuse the page's own
builders. Three things can go wrong when one text is said in three places, and
each gets its own guard:

* the three copies drift (a sentence edited only on the page stops being true
  of the phone, and nobody would ever notice);
* the automatic send fires twice, or fires when nobody asked for it;
* the strip silently becomes work every dashboard visitor pays for.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def _opt_in(db_session, phone="09123456789", day=None):
    """Point the digest at a phone — the settings row *is* the opt-in."""
    from models import Settings
    from services.month_reading import SETTING_DIGEST_PHONE, SETTING_DIGEST_DAY

    db_session.add(Settings(key=SETTING_DIGEST_PHONE, value=phone))
    if day is not None:
        db_session.add(Settings(key=SETTING_DIGEST_DAY, value=str(day)))
    db_session.commit()


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


def test_the_dashboard_strip_and_the_digest_read_the_same_sentences(db_session):
    """One reading, two readers — by construction, not by discipline."""
    from services.dashboard import _month_reading
    from services.month_reading import month_reading

    strip = _month_reading(db_session)
    assert strip, "an empty shop still gets sentences — the quiet is the news"

    start = datetime.now(timezone.utc) - timedelta(days=10)
    reading = month_reading(db_session, start=start,
                            end=datetime.now(timezone.utc), span="این ماه")
    # Every sentence the strip carries is verbatim one of the reading's.
    assert set(strip) <= set(reading["notes"].values())


# ── the digest text ──────────────────────────────────────────────────────────

def _reading_with_sales():
    from services.chart_notes import chart_notes

    daily = [{"date": "۰۵/۰۳", "revenue": 2_000_000, "profit": 700_000, "count": 4},
             {"date": "۰۵/۰۹", "revenue": 500_000, "profit": 150_000, "count": 2}]
    return {
        "notes": chart_notes(
            daily=daily,
            categories=[{"category": "پسرانه", "revenue": 2_000_000},
                        {"category": "دخترانه", "revenue": 500_000}],
            tier_revenue=[{"tier": "silver", "label": "نقره‌ای", "revenue": 1_800_000},
                          {"tier": "gold", "label": "طلایی", "revenue": 700_000}],
            span="ماه مرداد",
        ),
        "daily": daily,
    }


def test_the_digest_says_the_month_in_one_text():
    from services.month_reading import digest_text

    body = digest_text(_reading_with_sales(), month="مرداد", year=1405,
                       top_products=[{"name": "تیشرت پسرانه", "qty_sold": 7}])
    # The reading and nothing else: the same sentences the page draws.
    assert "گزارش مرداد 1405:" in body
    assert "فروش 2,500,000 ت در 6 فاکتور، سود 850,000 ت." in body
    assert "بیشترین فروش ۰۵/۰۳ با 2,000,000 ت." in body
    assert "پرفروش‌ترین: تیشرت پسرانه (7 عدد)." in body
    # No placeholder, no raw «این بازه» leaking from the page's vocabulary.
    assert "این بازه" not in body
    assert "{" not in body


def test_the_digest_states_an_empty_month_instead_of_zero_figures():
    from services.month_reading import digest_text
    from services.chart_notes import chart_notes

    body = digest_text({"notes": chart_notes(daily=[], span="ماه مهر"),
                        "daily": []},
                       month="مهر", year=1404)
    # The reading's own empty sentence — one spelling of the fact, not two.
    assert "در ماه مهر فروشی ثبت نشده." in body
    # And no invented zeros beside it.
    assert "فروش 0 ت" not in body


# ── the firing gates ─────────────────────────────────────────────────────────

def test_no_phone_means_no_digest_even_when_due(db_session):
    from services.month_reading import fire_monthly_digest

    summary = asyncio.run(fire_monthly_digest(db_session))
    assert summary == {"sent": 0, "skipped": "no_phone"}


def test_the_digest_waits_for_its_day(db_session):
    from services.month_reading import fire_monthly_digest

    _opt_in(db_session)
    # Shahrivar 1, 1405 — before the default day 3.
    at = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    summary = asyncio.run(fire_monthly_digest(db_session, at=at))
    assert summary == {"sent": 0, "skipped": "not_due"}


def test_the_digest_fires_once_for_the_finished_month(db_session):
    from models import SmsMessage
    from services.month_reading import fire_monthly_digest

    _opt_in(db_session)
    # Shahrivar 3, 1405: the first pass that is due, and مرداد is over.
    at = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    summary = asyncio.run(fire_monthly_digest(db_session, at=at))
    assert summary["sent"] == 1
    row = db_session.query(SmsMessage).order_by(SmsMessage.id.desc()).first()
    assert row.ref == summary["ref"]
    assert row.ref.startswith("digest:")
    assert "مرداد" in row.body
    # A second pass — the scheduler runs every five minutes — stays quiet.
    again = asyncio.run(fire_monthly_digest(db_session, at=at + timedelta(minutes=5)))
    assert again == {"sent": 0, "skipped": "already_sent"}
    assert db_session.query(SmsMessage).filter(SmsMessage.source == "monthly_digest").count() == 1


def test_the_digest_window_opens_on_the_configured_day(db_session):
    from services.month_reading import fire_monthly_digest

    _opt_in(db_session, day=28)
    # Shahrivar 1 is too early for a day-28 window…
    early = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    assert asyncio.run(fire_monthly_digest(db_session, at=early))["sent"] == 0
    # …and Shahrivar 28 is the day it opens, still speaking of مرداد.
    due = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)
    summary = asyncio.run(fire_monthly_digest(db_session, at=due))
    assert summary["sent"] == 1


# ── the owner's settings ─────────────────────────────────────────────────────

def _owner(client, db_session):
    from tests.test_roles import _session_as, _staff
    user, password = _staff(db_session, "digest-owner", "owner")
    _session_as(client, user, password)


def _csrf(client, url="/admin/sms"):
    import re
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(url).text).group(1)


def test_the_settings_form_saves_reads_and_clears_the_digest(client, db_session):
    from models import Settings
    from services._common import get_setting_int
    from services.month_reading import SETTING_DIGEST_PHONE, SETTING_DIGEST_DAY

    _owner(client, db_session)
    token = _csrf(client)
    resp = client.post("/admin/sms/config", data={
        "csrf_token": token,
        "monthly_digest_phone": "۰۹۱۲۳۴۵۶۷۸۹",   # Persian digits, contiguous
        "monthly_digest_day": "۵",
    }, follow_redirects=False)
    assert resp.status_code == 303
    saved = db_session.query(Settings).filter(Settings.key == SETTING_DIGEST_PHONE).one()
    assert saved.value == "09123456789"
    assert get_setting_int(db_session, SETTING_DIGEST_DAY, 3) == 5

    # Clearing the phone is switching the digest off — and it is honoured.
    resp = client.post("/admin/sms/config", data={
        "csrf_token": _csrf(client),
        "monthly_digest_phone": "",
        "monthly_digest_day": "5",
    }, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.query(Settings).filter(Settings.key == SETTING_DIGEST_PHONE).one().value == ""


def test_a_nonsense_phone_is_refused_not_saved(client, db_session):
    from models import Settings
    from services.month_reading import SETTING_DIGEST_PHONE

    _owner(client, db_session)
    resp = client.post("/admin/sms/config", data={
        "csrf_token": _csrf(client),
        "monthly_digest_phone": "سلام",
        "monthly_digest_day": "3",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]
    assert db_session.query(Settings).filter(Settings.key == SETTING_DIGEST_PHONE).count() == 0


def test_a_day_outside_the_month_is_refused(client, db_session):
    _owner(client, db_session)
    resp = client.post("/admin/sms/config", data={
        "csrf_token": _csrf(client),
        "monthly_digest_phone": "09123456789",
        "monthly_digest_day": "31",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]


def test_the_digest_settings_page_shows_the_next_month(client, db_session):
    _owner(client, db_session)
    html = client.get("/admin/sms").text
    assert "monthly_digest_phone" in html
    assert "monthly_digest_day" in html
    assert "خلاصه بعدی درباره" in html


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


# ── the scheduler never trips on it ──────────────────────────────────────────

def test_a_broken_reading_never_breaks_the_sweep(db_session, monkeypatch):
    from services import month_reading as module

    _opt_in(db_session)
    monkeypatch.setattr(module, "month_reading",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    summary = asyncio.run(module.fire_monthly_digest(db_session))
    assert summary == {"sent": 0, "skipped": "error"}


# ── the preview: what opting in puts on the phone ────────────────────────────

def test_the_preview_says_what_the_send_will_send(db_session):
    """The page shows the very text the scheduler will queue — one composer."""
    from models import SmsMessage
    from services.month_reading import (
        _finished_month_window,
        compose_digest,
        digest_preview,
        fire_monthly_digest,
    )

    _opt_in(db_session)
    # Shahrivar 3, 1405: the first pass that is due, and مرداد is over.
    at = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    preview = digest_preview(db_session, at=at)
    assert preview["state"] == "ready"
    assert "گزارش" in preview["body"]

    start, end = _finished_month_window(at)
    direct = compose_digest(db_session, start=start, end=end)
    assert preview["body"] == direct["body"]
    assert preview["ref"] == direct["ref"]

    summary = asyncio.run(fire_monthly_digest(db_session, at=at))
    assert summary["sent"] == 1
    row = db_session.query(SmsMessage).filter(SmsMessage.source == "monthly_digest").first()
    assert row.body == preview["body"]
    assert row.ref == preview["ref"]


def test_the_preview_needs_no_phone_and_sends_nothing(db_session):
    """Deciding happens before opting in; looking must never be a trigger."""
    from models import SmsMessage
    from services.month_reading import digest_preview

    preview = digest_preview(db_session)
    assert preview["state"] == "ready"
    assert db_session.query(SmsMessage).filter(
        SmsMessage.source == "monthly_digest").count() == 0


def test_the_preview_names_the_finished_month_before_the_window_opens(db_session):
    """On the 1st, the send is quiet — but the preview still shows a month."""
    from services.month_reading import digest_preview

    # Shahrivar 1, 1405 — before the default day 3.
    at = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    preview = digest_preview(db_session, at=at)
    assert preview["state"] == "ready"
    # The month that finished is described now, not a complaint about timing.
    assert "مرداد" in preview["body"]


def test_the_settings_page_shows_the_preview_bubble_to_the_owner_only(client, db_session):
    from tests.test_roles import _session_as, _staff

    owner, password = _staff(db_session, "digest-preview-owner", "owner")
    _session_as(client, owner, password)
    html = client.get("/admin/sms").text
    assert "digest-preview" in html
    assert "پیش‌نمایش خلاصه بعدی" in html
    # A real bubble carrying the month's own words, not the fallback note.
    assert "sms-bubble digest-preview-bubble" in html
    assert "گزارش" in html

    manager, password = _staff(db_session, "digest-preview-manager", "manager")
    _session_as(client, manager, password)
    assert "digest-preview" not in client.get("/admin/sms").text


def test_the_preview_is_never_computed_for_a_manager(client, db_session, monkeypatch):
    """Owner-only work: the reading walks the analytics queries, so a manager's
    page must not pay for it — the same laziness the dashboard strip keeps."""
    import routers.sms as sms_router
    from tests.test_roles import _session_as, _staff

    def _refuse(*args, **kwargs):
        raise AssertionError("the digest preview ran for a manager")

    monkeypatch.setattr(sms_router, "digest_preview", _refuse)
    manager, password = _staff(db_session, "digest-preview-lazy", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin/sms").status_code == 200


# ── the history: finding and re-sending a past month ─────────────────────────

def _fire_a_month(db_session, *, at):
    from services.month_reading import fire_monthly_digest
    summary = asyncio.run(fire_monthly_digest(db_session, at=at))
    assert summary["sent"] == 1, summary
    return summary["ref"]


def test_digest_rows_carry_their_month_in_history(client, db_session):
    """«مرداد ۱۴۰۵» on the row — findable among a log of senders and dates."""
    from tests.test_roles import _session_as, _staff

    _opt_in(db_session)
    at = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    _fire_a_month(db_session, at=at)

    owner, password = _staff(db_session, "history-owner", "owner")
    _session_as(client, owner, password)
    filtered = client.get("/admin/sms/history?source=monthly_digest").text
    assert "مرداد ۱۴۰۵" in filtered
    assert "digest-month-chip" in filtered
    # The month is the digests' own view; elsewhere the log keeps its shape.
    unfiltered = client.get("/admin/sms/history").text
    assert "digest-month-chip" not in unfiltered


def test_the_owner_can_resend_a_past_month_by_hand(client, db_session):
    """One form on the digests' own view; the same body, under the same ref."""
    from models import SmsMessage
    from tests.test_roles import _session_as, _staff

    _opt_in(db_session)
    at = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    ref = _fire_a_month(db_session, at=at)
    first_body = db_session.query(SmsMessage).filter(
        SmsMessage.ref == ref).first().body

    owner, password = _staff(db_session, "resend-owner", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin/sms/history?source=monthly_digest").text
    assert "ارسال دوباره خلاصه یک ماه" in page
    assert f'value="{ref}"' in page

    resp = client.post("/admin/sms/history/digest/resend", data={
        "csrf_token": _csrf(client, "/admin/sms/history"),
        "ref": ref,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "msg=" in resp.headers["location"]

    rows = db_session.query(SmsMessage).filter(
        SmsMessage.ref == ref, SmsMessage.source == "monthly_digest").all()
    assert len(rows) == 2
    assert rows[-1].body == first_body


def test_a_manager_neither_sees_the_form_nor_passes_the_route(client, db_session):
    from tests.test_roles import _session_as, _staff

    _opt_in(db_session)
    ref = _fire_a_month(db_session, at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc))

    manager, password = _staff(db_session, "resend-manager", "manager")
    _session_as(client, manager, password)
    assert "ارسال دوباره خلاصه یک ماه" not in client.get(
        "/admin/sms/history?source=monthly_digest").text
    resp = client.post("/admin/sms/history/digest/resend", data={
        "csrf_token": _csrf(client, "/admin/sms/history"),
        "ref": ref,
    }, follow_redirects=False)
    assert resp.status_code == 403


def test_a_resend_of_an_unknown_ref_says_so(client, db_session):
    from models import SmsMessage
    from tests.test_roles import _session_as, _staff

    _owner(client, db_session)
    resp = client.post("/admin/sms/history/digest/resend", data={
        "csrf_token": _csrf(client, "/admin/sms/history"),
        "ref": "digest:1399-01",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]
    assert db_session.query(SmsMessage).filter(
        SmsMessage.ref == "digest:1399-01").count() == 0


def test_the_resend_carries_the_recorded_text_verbatim(client, db_session):
    """A hand send cannot rewrite the month: the row's body is what goes out."""
    from models import SmsMessage
    from tests.test_roles import _session_as, _staff

    _opt_in(db_session)
    ref = _fire_a_month(db_session, at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc))
    row = db_session.query(SmsMessage).filter(SmsMessage.ref == ref).first()
    row.body = "متن ثبت‌شده‌ی همان ماه"
    db_session.commit()

    owner, password = _staff(db_session, "verbatim-owner", "owner")
    _session_as(client, owner, password)
    client.post("/admin/sms/history/digest/resend", data={
        "csrf_token": _csrf(client, "/admin/sms/history"),
        "ref": ref,
    }, follow_redirects=False)
    resent = db_session.query(SmsMessage).filter(
        SmsMessage.ref == ref, SmsMessage.source == "monthly_digest",
    ).order_by(SmsMessage.id.desc()).first()
    assert resent.body == "متن ثبت‌شده‌ی همان ماه"
