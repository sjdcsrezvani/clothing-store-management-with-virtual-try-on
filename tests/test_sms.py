"""پیامک: the templates, the mirror, the audience, and the log.

The patterns used to live in the settings table with no record of what was sent.
These tests pin the four things that changed: seeding never invents text, a
template switched off still stops the send (through the same legacy keys), a
blast reaches exactly the people it promised, and every message that leaves the
shop is written down with the text it actually carried.
"""
import asyncio
import itertools
import re
from pathlib import Path

import pytest

from models import (
    BackgroundJob,
    Campaign,
    Customer,
    Settings,
    SmsMessage,
    SmsTemplate,
)
from services.jobs import MAX_RETRIES, claim_next, complete, fail, process_one
from services.sms import get_sms_config, queue_sms, queue_welcome_sms
from services.sms_send import (
    message_filtered,
    message_overview,
    normalise_phone,
    parse_phone_list,
    plan_from_form,
    resolve_recipients,
    send_bulk,
    template_card,
)
from services.sms_templates import (
    BUILTIN_TEMPLATES,
    active_pattern,
    create_custom,
    delete_blocked_reason,
    ensure_seeded,
    get_template,
    render_template,
    render_text,
    sms_metrics,
    sync_to_settings,
    template_variables,
    validate,
)
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()
SMS_HTML = (ROOT / "templates" / "admin" / "sms.html").read_text()
HISTORY_HTML = (ROOT / "templates" / "admin" / "sms_history.html").read_text()
SETTINGS_HTML = (ROOT / "templates" / "admin" / "settings.html").read_text()
BASE_HTML = (ROOT / "templates" / "base.html").read_text()

_counter = itertools.count(1)


# ── helpers ──────────────────────────────────────────────────────────────────

def set_setting(db, key, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(Settings(key=key, value=str(value)))
    db.commit()


def make_customer(db, **kwargs) -> Customer:
    index = next(_counter)
    customer = Customer(
        phone=kwargs.pop("phone", f"0912{index:07d}"),
        first_name=kwargs.pop("first_name", f"مشتری{index}"),
        last_name=kwargs.pop("last_name", "تستی"),
        referral_code=f"S{index:05d}",
        **kwargs,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_campaign(db, **kwargs) -> Campaign:
    index = next(_counter)
    campaign = Campaign(
        name=kwargs.pop("name", f"کمپین{index}"),
        code=kwargs.pop("code", f"SMS{index}"),
        discount_percent=kwargs.pop("discount_percent", 10),
        min_purchase=kwargs.pop("min_purchase", 0),
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


# ── seeding and the legacy mirror ────────────────────────────────────────────

def test_seeding_mirrors_the_settings_row_and_never_invents_text(db_session):
    set_setting(db_session, "sms_pattern_welcome", "سلام %var1% عزیز")

    created = ensure_seeded(db_session)

    assert created == len(BUILTIN_TEMPLATES)
    welcome = get_template(db_session, "welcome")
    assert welcome.body == "سلام %var1% عزیز"
    assert welcome.is_active is True

    birthday = get_template(db_session, "birthday")
    assert birthday.body == ""
    assert birthday.is_active is False
    # Seeding must not write a pattern the shop never wrote, or an upgrade would
    # start sending birthday wishes nobody asked for.
    assert db_session.query(Settings).filter(Settings.key == "sms_pattern_birthday").first() is None


def test_second_seed_is_a_no_op(db_session):
    ensure_seeded(db_session)
    assert ensure_seeded(db_session) == 0
    assert db_session.query(SmsTemplate).count() == len(BUILTIN_TEMPLATES)


def test_deactivating_writes_an_empty_pattern_so_every_sender_stops(db_session):
    set_setting(db_session, "sms_pattern_welcome", "سلام %var1%")
    ensure_seeded(db_session)
    welcome = get_template(db_session, "welcome")

    welcome.is_active = False
    sync_to_settings(db_session, welcome)
    db_session.commit()

    assert get_sms_config(db_session)["welcome_pattern"] == ""
    assert db_session.query(Settings).filter(Settings.key == "sms_pattern_welcome").first().value == ""
    assert active_pattern(db_session, "welcome") == ""


def test_editing_a_template_updates_the_settings_row_the_senders_read(db_session):
    ensure_seeded(db_session)
    welcome = get_template(db_session, "welcome")
    welcome.body = "خوش آمدی %var1%"
    welcome.is_active = True
    sync_to_settings(db_session, welcome)
    db_session.commit()

    assert get_sms_config(db_session)["welcome_pattern"] == "خوش آمدی %var1%"


# ── rendering and measuring ──────────────────────────────────────────────────

def test_render_text_accepts_both_placeholder_spellings():
    assert render_text("%var1% و {var2}!", {"var1": "سارا", "var2": "نیلو"}) == "سارا و نیلو!"


def test_render_template_blanks_a_declared_token_nobody_filled(db_session):
    ensure_seeded(db_session)
    campaign = get_template(db_session, "campaign")

    rendered = render_template(campaign, {"var1": "سارا"})

    # A literal %var3% on a customer's phone is worse than a missing code.
    assert "%" not in rendered
    assert "{" not in rendered


def test_sms_metrics_counts_segments_per_encoding():
    assert sms_metrics("")["segments"] == 0
    assert sms_metrics("a" * 160)["segments"] == 1
    assert sms_metrics("a" * 161)["segments"] == 2
    assert sms_metrics("س" * 70)["segments"] == 1
    assert sms_metrics("س" * 71)["segments"] == 2
    assert sms_metrics("س")["encoding"].startswith("یونیکد")


def test_validate_refuses_an_empty_or_oversized_body():
    assert validate("", "متن") != ""
    assert validate("نام", "   ") != ""
    assert validate("نام", "x" * 613) != ""
    assert validate("نام", "متن کوتاه") == ""


# ── phone numbers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("09121234567", "09121234567"),
    ("۰۹۱۲۱۲۳۴۵۶۷", "09121234567"),
    ("+989121234567", "09121234567"),
    ("00989121234567", "09121234567"),
    ("989121234567", "09121234567"),
    ("9121234567", "09121234567"),
    ("0912-123 4567", "09121234567"),
    ("12345", ""),
    ("0812345678", ""),
    ("", ""),
])
def test_normalise_phone(raw, expected):
    assert normalise_phone(raw) == expected


def test_parse_phone_list_drops_junk_and_duplicates():
    listed = parse_phone_list("09121234567، 09121234567\n12345, 09351234567")
    assert listed == ["09121234567", "09351234567"]


# ── the audience ─────────────────────────────────────────────────────────────

def test_recipients_respect_consent_archive_and_a_blocking_tag(db_session):
    make_customer(db_session, first_name="عادی")
    make_customer(db_session, first_name="انصرافی", sms_opt_in=False)
    make_customer(db_session, first_name="بایگانی", is_archived=True)
    make_customer(db_session, first_name="بلاک", tags="blocked")

    plan = resolve_recipients(db_session)

    assert [row["customer"].first_name for row in plan["recipients"]] == ["عادی"]
    assert plan["skipped"]["opted_out"] == 1
    assert plan["skipped"]["archived"] == 1
    assert plan["skipped"]["blocked"] == 1


def test_cap_leaves_the_rest_for_the_next_round(db_session):
    set_setting(db_session, "campaign_sms_limit", 2)
    for index in range(3):
        make_customer(db_session, first_name=f"مشتری{index}")

    plan = resolve_recipients(db_session)

    assert plan["count"] == 2
    assert plan["matched"] == 3
    assert plan["capped"] is True
    assert plan["limit"] == 2


def test_transactional_messages_ignore_consent_but_not_the_archive(db_session):
    make_customer(db_session, first_name="انصرافی", sms_opt_in=False)
    make_customer(db_session, first_name="بایگانی", is_archived=True)

    plan = resolve_recipients(db_session, transactional=True)

    assert [row["customer"].first_name for row in plan["recipients"]] == ["انصرافی"]
    assert plan["skipped"]["archived"] == 1
    assert plan["skipped"]["opted_out"] == 0


def test_numbers_mode_links_a_customer_and_reports_the_junk(db_session):
    customer = make_customer(db_session, first_name="سارا")

    plan = resolve_recipients(
        db_session, mode="numbers",
        numbers=f"{customer.phone}\n{customer.phone}\n12345\n09359999999",
    )

    assert plan["count"] == 2
    assert plan["recipients"][0]["customer"].id == customer.id
    assert plan["recipients"][1]["customer"] is None
    assert plan["skipped"]["duplicate"] == 1
    assert plan["skipped"]["invalid"] == 1


def test_plan_from_form_reads_the_tier_and_tag_prefixes(db_session):
    make_customer(db_session, tier="gold")
    make_customer(db_session, tier="silver")
    make_customer(db_session, tags="vip", tier="silver")

    assert plan_from_form(db_session, audience="tier:gold")["count"] == 1
    assert plan_from_form(db_session, audience="tag:vip")["count"] == 1
    assert plan_from_form(db_session, audience="all")["count"] == 3
    assert plan_from_form(db_session, audience="nonsense")["count"] == 3


# ── queueing and the log ─────────────────────────────────────────────────────

def test_queue_renders_once_and_logs_the_message(db_session):
    set_setting(db_session, "sms_pattern_welcome", "سلام %var1%")
    ensure_seeded(db_session)
    customer = make_customer(db_session)

    job = asyncio.run(queue_sms("سلام %var1%", customer.phone, {"var1": "سارا"}, db_session,
                                template_key="welcome", source="welcome", customer=customer))

    assert job is not None
    row = db_session.query(SmsMessage).one()
    assert row.body == "سلام سارا"
    assert row.status == "queued"
    assert row.source == "welcome"
    assert row.kind == "marketing"
    assert row.customer_id == customer.id
    assert row.job_id == job.id
    payload = job.payload
    # The text is frozen at queue time: editing the template later cannot change
    # what this message says.
    assert "سلام سارا" in payload
    assert str(row.id) in payload


def test_queue_welcome_is_silent_when_the_template_is_off(db_session):
    ensure_seeded(db_session)
    customer = make_customer(db_session)

    assert asyncio.run(queue_welcome_sms(customer.phone, "سارا", "REF1", db_session,
                                         customer=customer)) is None
    assert db_session.query(SmsMessage).count() == 0


def test_worker_marks_the_row_sent(db_session, monkeypatch):
    set_setting(db_session, "sms_pattern_welcome", "سلام %var1%")
    ensure_seeded(db_session)
    customer = make_customer(db_session)
    asyncio.run(queue_sms("سلام %var1%", customer.phone, {"var1": "سارا"}, db_session,
                          template_key="welcome", source="welcome", customer=customer))

    async def fake_send(message, recipient, db):
        assert message == "سلام سارا"
        return True

    monkeypatch.setattr("services.sms.send_sms", fake_send)
    job = claim_next(db_session, "sms")
    assert asyncio.run(process_one(db_session, job)) is True
    complete(db_session, job)

    row = db_session.query(SmsMessage).one()
    assert row.status == "sent"
    assert row.sent_at is not None


def test_worker_marks_the_row_failed_only_after_the_last_retry(db_session, monkeypatch):
    ensure_seeded(db_session)
    customer = make_customer(db_session)
    asyncio.run(queue_sms("سلام", customer.phone, {}, db_session, source="manual",
                          customer=customer))

    async def fake_send(message, recipient, db):
        return False

    monkeypatch.setattr("services.sms.send_sms", fake_send)
    job = claim_next(db_session, "sms")
    with pytest.raises(RuntimeError):
        asyncio.run(process_one(db_session, job))
    assert db_session.query(SmsMessage).one().status == "queued"

    job.retry_count = MAX_RETRIES - 1
    fail(db_session, job, RuntimeError("SMS delivery failed"))
    row = db_session.query(SmsMessage).one()
    assert row.status == "failed"
    assert row.error


def test_send_bulk_queues_one_message_per_recipient(db_session):
    ensure_seeded(db_session)
    for index in range(3):
        make_customer(db_session, first_name=f"مشتری{index}")
    template = get_template(db_session, "campaign")
    template.body = "%var1% عزیز: %var2%"
    template.is_active = True
    db_session.commit()

    plan = resolve_recipients(db_session)
    summary = asyncio.run(send_bulk(db_session, template=template, plan=plan, source="manual"))

    assert summary["queued"] == 3
    assert summary["empty"] == 0
    assert db_session.query(SmsMessage).count() == 3


# ── the log's own pages and filters ──────────────────────────────────────────

def test_history_filters_by_status_source_and_search(db_session):
    ensure_seeded(db_session)
    customer = make_customer(db_session, first_name="سارا")
    db_session.add(SmsMessage(phone=customer.phone, body="سلام سارا", status="sent",
                              source="welcome", kind="marketing", customer_id=customer.id))
    db_session.add(SmsMessage(phone="09350000000", body="یادآوری بدهی", status="queued",
                              source="credit_reminder", kind="transactional"))
    db_session.commit()

    assert message_filtered(db_session)["total"] == 2
    assert message_filtered(db_session, status="sent")["total"] == 1
    assert message_filtered(db_session, source="credit_reminder")["total"] == 1
    assert message_filtered(db_session, search="سارا")["total"] == 1
    assert message_filtered(db_session, search="0935")["total"] == 1
    assert message_filtered(db_session, search="هیچ‌چیز")["total"] == 0
    assert message_overview(db_session)["total"] == 2
    assert message_overview(db_session)["queued"] == 1
    assert message_overview(db_session)["failed"] == 0


def test_template_card_counts_what_it_sent(db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    db_session.add(SmsMessage(phone="09120000000", body="سلام", status="sent",
                              source="welcome", template_id=template.id, template_key=template.key))
    db_session.add(SmsMessage(phone="09120000001", body="سلام", status="failed",
                              source="welcome", template_id=template.id, template_key=template.key))
    db_session.commit()

    card = template_card(db_session, template)

    assert card["sent"] == 2
    assert card["failed"] == 1
    assert [variable["token"] for variable in card["variables"]] == ["var1", "var2"]


def test_delete_is_refused_for_builtins_and_for_anything_already_sent(db_session):
    ensure_seeded(db_session)
    assert delete_blocked_reason(db_session, get_template(db_session, "welcome")) != ""

    custom = create_custom(db_session, name="تبریک عید", body="عید مبارک")
    db_session.commit()
    assert delete_blocked_reason(db_session, custom) == ""

    db_session.add(SmsMessage(phone="09120000000", body="عید مبارک", template_id=custom.id))
    db_session.commit()
    assert delete_blocked_reason(db_session, custom) != ""


# ── the pages ────────────────────────────────────────────────────────────────

def test_sms_pages_need_a_login(client):
    response = client.get("/admin/sms", follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]


def test_manager_page_lists_every_template(client, authed, db_session):
    set_setting(db_session, "sms_pattern_birthday", "تولدت مبارک %var1%")

    response = authed.get("/admin/sms")

    assert response.status_code == 200
    assert "پیامک تبریک تولد" in response.text
    assert "درگاه پیامک" in response.text
    assert "تاریخچه ارسال" in response.text


def test_editor_saves_the_text_and_toggles_it_off(client, authed, db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    token = csrf_token(client, f"/admin/sms/templates/{template.id}/edit")

    saved = authed.post(f"/admin/sms/templates/{template.id}", data={
        "csrf_token": token, "name": template.name,
        "body": "خوش آمدی %var1%", "is_active": "on",
    }, follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()
    assert get_sms_config(db_session)["welcome_pattern"] == "خوش آمدی %var1%"

    off = authed.post(f"/admin/sms/templates/{template.id}/toggle", data={"csrf_token": token},
                      follow_redirects=False)
    assert off.status_code == 303
    db_session.expire_all()
    assert get_sms_config(db_session)["welcome_pattern"] == ""
    assert get_template(db_session, "welcome").body == "خوش آمدی %var1%"


def test_custom_template_lifecycle_through_the_pages(client, authed, db_session):
    token = csrf_token(client, "/admin/sms/templates/new")

    created = authed.post("/admin/sms/templates/new", data={
        "csrf_token": token, "name": "تبریک عید",
        "body": "%var1% عزیز، عید مبارک", "label_var1": "نام مشتری", "sample_var1": "سارا",
    }, follow_redirects=False)
    assert created.status_code == 303

    template = db_session.query(SmsTemplate).filter(SmsTemplate.category == "custom").one()
    assert template.name == "تبریک عید"
    assert template.setting_key is None
    assert template_variables(template)[0]["label"] == "نام مشتری"

    editor = authed.get(f"/admin/sms/templates/{template.id}/edit")
    assert editor.status_code == 200
    assert "عید مبارک" in editor.text

    copied = authed.post(f"/admin/sms/templates/{template.id}/duplicate",
                         data={"csrf_token": token}, follow_redirects=False)
    assert copied.status_code == 303
    assert db_session.query(SmsTemplate).count() == len(BUILTIN_TEMPLATES) + 2

    deleted = authed.post(f"/admin/sms/templates/{template.id}/delete",
                          data={"csrf_token": token}, follow_redirects=False)
    assert deleted.status_code == 303
    assert db_session.query(SmsTemplate).filter(SmsTemplate.id == template.id).first() is None


def test_a_builtin_cannot_be_deleted_from_the_page(client, authed, db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    token = csrf_token(client, "/admin/sms")

    response = authed.post(f"/admin/sms/templates/{template.id}/delete",
                           data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert get_template(db_session, "welcome") is not None


def test_sending_previews_before_it_queues(client, authed, db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "campaign")
    template.body = "%var1% عزیز، کمپین %var2%"
    template.is_active = True
    db_session.commit()
    make_customer(db_session, first_name="سارا")
    make_customer(db_session, first_name="نیلو")
    token = csrf_token(client, "/admin/sms/send")

    preview = authed.post("/admin/sms/send", data={
        "csrf_token": token, "template_id": template.id, "audience": "all",
        "numbers": "", "action": "preview",
    })
    assert preview.status_code == 200
    assert "سارا" in preview.text
    assert db_session.query(SmsMessage).count() == 0

    sent = authed.post("/admin/sms/send", data={
        "csrf_token": token, "template_id": template.id, "audience": "all",
        "numbers": "", "action": "send",
    }, follow_redirects=False)
    assert sent.status_code == 303
    assert "/admin/sms/history" in sent.headers["location"]
    rows = db_session.query(SmsMessage).all()
    assert len(rows) == 2
    assert {row.source for row in rows} == {"manual"}
    assert all("عزیز" in row.body for row in rows)


def test_a_test_send_goes_to_one_number_and_is_logged_as_transactional(client, authed, db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "campaign")
    template.body = "%var2% با %var4% تخفیف"
    template.is_active = True
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{template.id}/edit")

    response = authed.post(f"/admin/sms/templates/{template.id}/test", data={
        "csrf_token": token, "phone": "۰۹۱۲۳۴۵۶۷۸۹",
    }, follow_redirects=False)

    assert response.status_code == 303
    row = db_session.query(SmsMessage).one()
    assert row.phone == "09123456789"
    assert row.source == "test"
    assert row.kind == "transactional"


def test_history_page_shows_the_recipient_and_the_text(client, authed, db_session):
    customer = make_customer(db_session, first_name="سارا")
    db_session.add(SmsMessage(phone=customer.phone, body="یادآوری بدهی شما", status="sent",
                              source="credit_reminder", kind="transactional",
                              customer_id=customer.id, template_name="یادآوری بدهی نسیه"))
    db_session.commit()

    response = authed.get("/admin/sms/history")

    assert response.status_code == 200
    assert "یادآوری بدهی شما" in response.text
    assert customer.first_name in response.text
    assert "ارسال‌شده" in response.text


def test_settings_page_points_at_the_new_page(client, authed):
    response = authed.get("/admin/settings")

    assert response.status_code == 200
    assert "/admin/sms" in response.text
    assert 'name="sms_pattern_welcome"' not in response.text
    assert 'name="campaign_sms_limit"' not in response.text


def test_campaign_send_uses_the_template_and_is_logged(client, authed, db_session):
    campaign = make_campaign(db_session, code="AUTUMN")
    make_customer(db_session, first_name="سارا")
    make_customer(db_session, first_name="نیلو")
    ensure_seeded(db_session)
    template = get_template(db_session, "campaign")
    template.body = "%var1% عزیز، کمپین %var2% با کد %var3%"
    template.is_active = True
    db_session.commit()
    token = csrf_token(client, f"/admin/campaigns/{campaign.id}")

    response = authed.post(f"/admin/campaigns/{campaign.id}/send",
                           data={"csrf_token": token, "audience": "all"},
                           follow_redirects=False)

    assert response.status_code == 303
    rows = db_session.query(SmsMessage).all()
    assert len(rows) == 2
    assert {row.source for row in rows} == {"campaign"}
    assert all(campaign.name in row.body for row in rows)


def test_sms_styles_come_from_theme_tokens():
    section = STYLE_CSS[STYLE_CSS.index("/* ── پیامک "):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section), section[:300]
    for token in ("var(--rule)", "var(--card)", "var(--ink-soft)",
                  "var(--surface-soft)", "var(--sunshine)", "var(--persimmon)",
                  "var(--mint)", "var(--mint-dark)", "var(--lavender)"):
        assert token in section, token
    # State is text beside colour, never colour alone, in every theme.
    for state in (".status-badge.is-queued", ".status-badge.is-sent", ".status-badge.is-failed"):
        assert state in STYLE_CSS, state
    assert 'html[data-theme-mode="high-contrast"] .sms-audience-option' in STYLE_CSS
    assert "@media print" in STYLE_CSS


def test_the_sms_tables_never_hide_their_state_behind_a_sideways_scroll():
    """Both SMS tables carry the text and its metrics inside the name cell, so the
    state and the actions stay inside the card at any width. The app-wide mobile
    rule turns every table into a horizontal scroller; these two opt out.
    """
    assert ".sms-table { min-width: 0; }" in STYLE_CSS
    assert ".sms-history-table { min-width: 0; }" in STYLE_CSS
    assert "  .sms-table { white-space: normal; }" in STYLE_CSS
    # Three columns each: something, its detail, its state (and actions).
    assert 'قالب و متن' in SMS_HTML and "عملیات" in SMS_HTML
    assert 'مخاطب و متن' in HISTORY_HTML
    assert "status-badge is-{{ message.status }}" in HISTORY_HTML


def test_settings_hands_sms_over_to_the_new_page():
    """The six textareas and the gateway key left the settings form; the page now
    points at /admin/sms instead of keeping a second, drifting copy."""
    assert "sms_pattern_welcome" not in SETTINGS_HTML
    assert "campaign_sms_limit" not in SETTINGS_HTML
    assert 'href="/admin/sms"' in SETTINGS_HTML
    # The sidebar exposes the page next to the campaigns it sends for.
    assert 'href="/admin/sms" data-nav="sms"' in BASE_HTML


def test_campaign_send_is_refused_when_the_template_is_switched_off(client, authed, db_session):
    campaign = make_campaign(db_session)
    make_customer(db_session)
    ensure_seeded(db_session)
    token = csrf_token(client, f"/admin/campaigns/{campaign.id}")

    response = authed.post(f"/admin/campaigns/{campaign.id}/send",
                           data={"csrf_token": token, "audience": "all"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert db_session.query(SmsMessage).count() == 0
