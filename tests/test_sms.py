"""پیامک: the templates, the mirror, the audience, and the log.

The patterns used to live in the settings table with no record of what was sent.
These tests pin the four things that changed: seeding never invents text, a
template switched off still stops the send (through the same legacy keys), a
blast reaches exactly the people it promised, and every message that leaves the
shop is written down with the text it actually carried.
"""
import asyncio
import itertools
import json
import re
from datetime import datetime, timezone
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
from services.jobs import claim_next, complete, process_one
from services.sms import get_sms_config, queue_sms, queue_welcome_sms
from services.sms_send import (
    journey_label,
    message_filtered,
    message_overview,
    message_values,
    normalise_phone,
    parse_phone_list,
    plan_from_form,
    preview_body,
    recorded_rows,
    resolve_recipients,
    send_bulk,
    template_card,
)
from services.sms_templates import (
    BUILTIN_TEMPLATES,
    CUSTOM_SEND_URL,
    SMS_SENTENCES,
    SOURCE_FIELDS,
    active_pattern,
    create_custom,
    delete_blocked_reason,
    ensure_seeded,
    fire_summary,
    get_template,
    render_template,
    render_text,
    send_info,
    sentences_for,
    sms_metrics,
    sync_to_settings,
    template_variables,
    templates_for,
    unfilled_tokens,
    validate,
    values_for_customer,
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

    row = asyncio.run(queue_sms("سلام %var1%", customer.phone, {"var1": "سارا"}, db_session,
                                template_key="welcome", source="welcome", customer=customer))

    assert row is not None
    assert row.body == "سلام سارا"
    assert row.status == "queued"
    assert row.source == "welcome"
    assert row.kind == "marketing"
    assert row.customer_id == customer.id
    # The log row *is* the gateway queue item now; no BackgroundJob in front.
    assert row.job_id is None


def test_queue_welcome_is_silent_when_the_template_is_off(db_session):
    ensure_seeded(db_session)
    customer = make_customer(db_session)

    assert asyncio.run(queue_welcome_sms(customer.phone, "سارا", "REF1", db_session,
                                         customer=customer)) is None
    assert db_session.query(SmsMessage).count() == 0


# ── what a message was built out of ──────────────────────────────────────────

def queue_welcome(db, customer, body, values):
    """Queue one welcome-pattern message the way the signup path does."""
    set_setting(db, "sms_pattern_welcome", body)
    ensure_seeded(db)
    return asyncio.run(queue_sms(body, customer.phone, values, db,
                                 template_key="welcome", source="welcome",
                                 customer=customer))


def test_the_log_records_which_values_the_text_was_built_from(db_session):
    customer = make_customer(db_session, first_name="سارا")

    row = queue_welcome(db_session, customer,
                        "%var1% عزیز، کد معرف شما %var2% است.",
                        {"var1": "سارا", "var2": "REF9"})

    recorded = recorded_rows(row)
    assert [item["token"] for item in recorded] == ["var1", "var2"]
    # The shop's own label travels with the value, never the placeholder name —
    # «var2» means nothing to whoever reads the history in a year.
    assert recorded[0]["label"] == "نام مشتری"
    assert recorded[1]["label"] == "کد معرف مشتری"
    assert [item["value"] for item in recorded] == ["سارا", "REF9"]


def test_a_value_the_text_never_uses_is_not_part_of_the_record(db_session):
    """Only what shaped the sentence is written down, so an unused slot cannot
    make the row read as if it contributed something."""
    customer = make_customer(db_session, first_name="سارا")

    row = queue_welcome(db_session, customer, "%var1% عزیز، خوش آمدید.",
                        {"var1": "سارا", "var2": "REF9"})

    assert [item["token"] for item in recorded_rows(row)] == ["var1"]


def test_a_text_with_no_placeholders_says_so_rather_than_nothing(db_session):
    """``"[]"`` و ``""`` دو چیز مختلف‌اند: یکی «سؤال پرسیده شد و جوابی نبود» و
    دیگری «این ردیف پیش از وجود سابقه فرستاده شده». Merging them would let the
    page claim a legacy row had no placeholders."""
    customer = make_customer(db_session, first_name="سارا")

    row = queue_welcome(db_session, customer, "به فروشگاه ما خوش آمدید.", {})

    assert row.values_json == "[]"
    assert message_values(row, get_template(db_session, "welcome"))["state"] == "none"


def test_a_send_the_router_rendered_itself_still_records_its_values(client, authed, db_session):
    """The bulk sender renders the body itself and then hands over a raw string,
    so its values have to be passed explicitly — that is exactly the path where
    the record would otherwise be silently empty."""
    ensure_seeded(db_session)
    template = create_custom(db_session, name="تشکر", body="%var1% عزیز، ممنون از خریدتان.")
    make_customer(db_session, first_name="سارا")
    token = csrf_token(client, "/admin/sms/send")

    sent = authed.post("/admin/sms/send", data={
        "csrf_token": token, "template_id": template.id, "audience": "all",
        "numbers": "", "action": "send",
    }, follow_redirects=False)

    assert sent.status_code == 303
    row = db_session.query(SmsMessage).one()
    assert row.body == "سارا عزیز، ممنون از خریدتان."
    assert recorded_rows(row) == [{"token": "var1", "label": "نام مشتری", "value": "سارا"}]
    assert message_values(row, template)["state"] == "match"


def test_a_replay_reproduces_what_the_customer_received(db_session):
    customer = make_customer(db_session, first_name="سارا")
    row = queue_welcome(db_session, customer, "%var1% عزیز، کد شما %var2% است.",
                        {"var1": "سارا", "var2": "REF9"})

    audit = message_values(row, get_template(db_session, "welcome"))

    assert audit["state"] == "match"
    assert audit["replay_body"] == row.body


def test_editing_the_template_later_does_not_rewrite_the_record(db_session):
    """The body is frozen for this reason; the record behind it has to be just
    as durable, labels included, or the history becomes unreadable the first
    time someone renames a slot."""
    customer = make_customer(db_session, first_name="سارا")
    row = queue_welcome(db_session, customer, "%var1% عزیز، سلام!", {"var1": "سارا"})
    template = get_template(db_session, "welcome")
    template.body = "%var1% عزیز، سلام! %var1% جان، منتظرتان هستیم."
    template.variables = json.dumps([
        {"token": "var1", "label": "اسم کوچک", "sample": "سارا", "field": "first_name"},
    ], ensure_ascii=False)
    db_session.commit()

    audit = message_values(row, template)

    assert audit["state"] == "differs"
    assert audit["replay_body"] != row.body
    # The record still carries the label and value of the day it was sent.
    assert audit["recorded"] == [{"token": "var1", "label": "نام مشتری", "value": "سارا"}]
    assert row.body == "سارا عزیز، سلام!"


def test_a_deleted_template_leaves_its_recorded_values_readable(db_session):
    customer = make_customer(db_session, first_name="سارا")
    row = queue_welcome(db_session, customer, "%var1% عزیز، سلام!", {"var1": "سارا"})

    audit = message_values(row, None)

    assert audit["state"] == "template_gone"
    assert audit["recorded"][0]["value"] == "سارا"
    assert audit["recorded"][0]["label"] == "نام مشتری"


def test_a_row_older_than_the_record_claims_nothing(db_session):
    """An existing shop's history must not be dressed up as «no placeholders»:
    those rows were sent before anything recorded it, and the page says exactly
    that."""
    customer = make_customer(db_session, first_name="سارا")
    row = SmsMessage(phone=customer.phone, body="سلام سارا", status="sent",
                     source="welcome", kind="marketing", customer_id=customer.id)
    db_session.add(row)
    db_session.commit()

    assert row.values_json == ""
    assert message_values(row, None)["state"] == "unrecorded"


def test_an_unreadable_record_is_not_blamed_on_the_message(db_session):
    """Odd JSON costs the explanation, never the message: the row stays, the
    page opens, and the state says the record could not be trusted."""
    customer = make_customer(db_session, first_name="سارا")
    row = SmsMessage(phone=customer.phone, body="سلام سارا", status="sent",
                     source="welcome", kind="marketing", customer_id=customer.id,
                     values_json="{not json")
    db_session.add(row)
    db_session.commit()

    assert recorded_rows(row) == []
    assert message_values(row, None)["state"] == "unreadable"


def test_the_history_page_shows_the_values_and_the_replay_verdict(client, authed, db_session):
    customer = make_customer(db_session, first_name="سارا")
    queue_welcome(db_session, customer, "%var1% عزیز، کد شما %var2% است.",
                  {"var1": "سارا", "var2": "REF9"})

    response = authed.get("/admin/sms/history")

    assert response.status_code == 200
    assert "مقادیری که این متن از آن‌ها ساخته شده" in response.text
    assert "نام مشتری" in response.text and "REF9" in response.text
    assert "بازپخش: همین مقادیر با قالب فعلی دقیقاً همین متن را می‌سازند." in response.text


def test_the_history_page_is_honest_about_a_row_with_no_record(client, authed, db_session):
    customer = make_customer(db_session, first_name="سارا")
    db_session.add(SmsMessage(phone=customer.phone, body="سلام سارا", status="sent",
                              source="welcome", kind="marketing", customer_id=customer.id))
    db_session.commit()

    response = authed.get("/admin/sms/history")

    assert response.status_code == 200
    assert "مقادیر این پیامک ثبت نشده" in response.text
    assert "بازپخش" not in response.text


def test_worker_passes_legacy_sms_jobs_through(db_session, monkeypatch):
    """BackgroundJob("sms") rows queued before the gateway existed are no-ops:
    the message row itself is the queue item, and completing the job keeps it
    from spinning forever in the scheduler."""
    from models import BackgroundJob
    from datetime import datetime, timezone
    job = BackgroundJob(job_type="sms", payload='{"message": "سلام", "recipient": "09120000000", "message_id": null}',
                        status="pending", next_retry_at=datetime.now(timezone.utc))
    db_session.add(job)
    db_session.commit()

    claimed = claim_next(db_session, "sms")
    assert asyncio.run(process_one(db_session, claimed)) is True
    complete(db_session, claimed)
    assert claimed.status == "completed"


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
    """The row separates the queue's verdicts, so «ارسال‌شده» can no longer
    flatter a message that is still waiting or that the gateway refused."""
    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    db_session.add(SmsMessage(phone="09120000000", body="سلام", status="sent",
                              source="welcome", template_id=template.id, template_key=template.key))
    db_session.add(SmsMessage(phone="09120000001", body="سلام", status="failed",
                              source="welcome", template_id=template.id, template_key=template.key))
    db_session.add(SmsMessage(phone="09120000002", body="سلام", status="queued",
                              source="welcome", template_id=template.id, template_key=template.key))
    db_session.commit()

    card = template_card(db_session, template)

    assert card["total"] == 3
    assert card["sent"] == 1
    assert card["failed"] == 1
    assert card["queued"] == 1
    assert [variable["token"] for variable in card["variables"]] == ["var1", "var2"]


def test_template_card_dates_the_last_send_and_flags_a_never_used_one(db_session):
    """«آخرین ارسال» needs a date even for a row written before ``sent_at``
    existed, and a template nobody ever sent must say so rather than show a zero."""
    ensure_seeded(db_session)
    welcome = get_template(db_session, "welcome")
    birthday = get_template(db_session, "birthday")
    # status «sent» with no ``sent_at``: the row's creation time is the honest
    # fallback for the date it actually went out.
    db_session.add(SmsMessage(phone="09120000000", body="سلام", status="sent",
                              source="welcome", template_id=welcome.id,
                              template_key=welcome.key))
    db_session.commit()

    used = template_card(db_session, welcome)
    unused = template_card(db_session, birthday)

    assert used["last_sent_at"] is not None
    assert used["last_activity_at"] is not None
    assert unused["total"] == 0
    assert unused["last_sent_at"] is None


def test_manager_row_shows_the_last_send_and_the_total(client, authed, db_session):
    """A silent template used to read like any other row; now it says «هنوز ارسال
    نشده» and carries the badge, while a used one shows its total and last date."""
    ensure_seeded(db_session)
    welcome = get_template(db_session, "welcome")
    db_session.add(SmsMessage(phone="09120000000", body="سلام", status="sent",
                              source="welcome", template_id=welcome.id,
                              template_key=welcome.key))
    db_session.add(SmsMessage(phone="09120000001", body="سلام", status="failed",
                              source="welcome", template_id=welcome.id,
                              template_key=welcome.key))
    db_session.commit()

    page = authed.get("/admin/sms").text

    assert "sms-unused-badge" in page
    assert "هنوز پیامکی از این قالب فرستاده نشده" in page
    assert "2 پیامک · 1 ناموفق · آخرین ارسال" in page
    # The last-sent date is a Persian date, not a raw Gregorian timestamp.
    assert re.search(r"آخرین ارسال ۱۴\d\d/\d\d/\d\d", page)


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


def test_manager_page_says_how_each_template_fires(client, authed, db_session):
    """«خودکار» used to be pinned on every built-in, so the four that are sent by
    hand read as automatic. Each row now carries its real mode and, when it is
    manual, the page it is sent from."""
    ensure_seeded(db_session)
    create_custom(db_session, name="تبریک عید", body="%var1% عزیز، عید مبارک")
    db_session.commit()

    page = authed.get("/admin/sms").text

    assert "sms-fire-badge is-auto" in page and "sms-fire-badge is-manual" in page
    for url in ("/admin/birthdays", "/admin/tier-up", "/admin/campaigns", "/admin/credit"):
        assert f'href="{url}"' in page
    # The custom one spells out that nothing queues it, instead of implying a trigger.
    assert "این قالب خودبه‌خود فرستاده نمی‌شود" in page
    assert 'href="/admin/sms/send"' in page


def test_only_the_welcome_message_leaves_without_a_click(db_session):
    """The pages' claim comes from one function, so it cannot drift: only
    «خوش‌آمدگویی» is automatic, and a custom template is always hand-sent."""
    ensure_seeded(db_session)
    modes = {template.key: send_info(template)["mode"] for template in templates_for(db_session)}
    assert modes["welcome"] == "auto"
    assert [key for key, mode in modes.items() if mode == "auto"] == ["welcome"]

    custom = send_info(create_custom(db_session, name="تبریک عید", body="عید مبارک"))
    assert custom["mode"] == "manual"
    assert custom["url"] == CUSTOM_SEND_URL
    assert "ارسال پیامک" in custom["text"]

    summary = fire_summary(db_session)
    assert [template.key for template in summary["auto"]] == ["welcome"]
    assert {template.key for template in summary["manual"]} >= {
        "birthday", "tier_up_gold", "campaign", "credit_reminder",
    }


def test_editor_states_how_the_template_fires(client, authed, db_session):
    """A custom template's editor used to say nothing at all about sending; now it
    says in words that nothing queues it and it goes out by hand."""
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، عید مبارک")
    db_session.commit()

    editor = authed.get(f"/admin/sms/templates/{custom.id}/edit").text
    assert "این قالب چگونه فرستاده می‌شود؟" in editor
    assert "sms-fire-badge is-manual" in editor
    assert "این قالب خودبه‌خود فرستاده نمی‌شود" in editor
    assert 'href="/admin/sms/send"' in editor

    welcome = get_template(db_session, "welcome")
    welcome.body, welcome.is_active = "خوش آمدی %var1%", True
    db_session.commit()
    auto_editor = authed.get(f"/admin/sms/templates/{welcome.id}/edit").text
    assert "sms-fire-badge is-auto" in auto_editor
    assert "این پیامک خودکار فرستاده شود" in auto_editor


def test_the_new_template_page_says_it_will_be_hand_sent(client, authed):
    page = authed.get("/admin/sms/templates/new").text
    assert "این قالب چگونه فرستاده می‌شود؟" in page
    assert "sms-fire-badge is-manual" in page
    # The slot editor is there before anything exists, so the first slot is bound
    # to the customer's name from the start — and the owner is told the token's
    # position in the text is free.
    assert 'name="source_var1"' in page
    assert "نه ترتیبش" in page


def test_the_send_page_says_how_the_chosen_template_fires(client, authed, db_session):
    """The person holding the send button gets the same answer as the manager and
    the editor: this one is hand-sent from here, that one is normally automatic."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، عید مبارک")
    db_session.commit()

    page = authed.get(f"/admin/sms/send?template_id={custom.id}").text
    assert "این قالب چگونه فرستاده می‌شود؟" in page
    assert "sms-fire-badge is-manual" in page
    assert "این قالب خودبه‌خود فرستاده نمی‌شود" in page
    assert "همین صفحه جای فرستادن آن است." in page

    welcome = get_template(db_session, "welcome")
    welcome.body, welcome.is_active = "خوش آمدی %var1%", True
    birthday = get_template(db_session, "birthday")
    birthday.body, birthday.is_active = "تولدت مبارک %var1%", True
    db_session.commit()

    auto_page = authed.get(f"/admin/sms/send?template_id={welcome.id}").text
    assert "sms-fire-badge is-auto" in auto_page
    assert "پیام تکراری" in auto_page

    # A manual built-in points at its own page, so the sender is not left guessing
    # why they have never sent it from here.
    birthday_page = authed.get(f"/admin/sms/send?template_id={birthday.id}").text
    assert "sms-fire-badge is-manual" in birthday_page
    assert 'href="/admin/birthdays"' in birthday_page


def test_the_send_page_warns_before_sending_a_message_with_a_hole(client, authed, db_session):
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، عید مبارک! %var2%")
    db_session.commit()

    page = authed.get(f"/admin/sms/send?template_id={custom.id}").text

    assert "%var2%" in page
    assert "به هیچ مقداری وصل نیست" in page
    assert "عید مبارک!" in page


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


# ── where a variable sits in the text, and what fills it ─────────────────────

def test_a_variable_is_filled_by_its_name_not_its_place_in_the_text(db_session):
    """The owner asked whether the order of variables matters. It does not: the
    token's name decides the value, so «نام آخر پیامک» is just a move of %var1%."""
    set_setting(db_session, "sms_pattern_welcome", "خوش آمدی %var1%! کد معرف تو: %var2%")
    db_session.commit()
    customer = make_customer(db_session, first_name="سارا")

    welcome = get_template(db_session, "welcome")
    welcome.body = "کد معرف تو %var2% است، %var1% جان!"
    welcome.is_active = True
    db_session.commit()

    rendered = render_template(welcome, values_for_customer(customer, welcome))

    assert rendered == f"کد معرف تو {customer.referral_code} است، سارا جان!"


def test_saving_a_custom_template_keeps_what_each_slot_reads(client, authed, db_session):
    """Saving used to rebuild every slot as «no source», so the name a custom
    template was showing in its preview silently vanished from the real send."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید",
                           body="عید مبارک! %var1% عزیز، منتظرتان هستیم.")
    db_session.commit()
    customer = make_customer(db_session, first_name="سارا")
    token = csrf_token(client, f"/admin/sms/templates/{custom.id}/edit")

    saved = authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "تبریک عید",
        "body": "عید مبارک! %var1% عزیز، منتظرتان هستیم.",
        "label_var1": "نام مشتری", "sample_var1": "سارا",
        "source_var1": "first_name",
    }, follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()

    custom = db_session.query(SmsTemplate).filter(SmsTemplate.id == custom.id).one()
    assert template_variables(custom)[0]["field"] == "first_name"
    assert render_template(custom, values_for_customer(customer, custom)) == \
        "عید مبارک! سارا عزیز، منتظرتان هستیم."


def test_a_custom_slot_can_be_pointed_at_any_customer_value(client, authed, db_session):
    """A slot is a choice, so the money or the level can go anywhere in the text."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="یادآوری بدهی",
                           body="%var1% عزیز، %var2% تومان بدهی دارید.")
    db_session.commit()
    customer = make_customer(db_session, first_name="سارا", total_debt=450_000, tier="gold")
    token = csrf_token(client, f"/admin/sms/templates/{custom.id}/edit")

    authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "یادآوری بدهی",
        "body": "%var1% عزیز، %var2% تومان بدهی دارید.",
        "source_var1": "tier", "source_var2": "total_debt",
    }, follow_redirects=False)
    db_session.expire_all()

    custom = db_session.query(SmsTemplate).filter(SmsTemplate.id == custom.id).one()
    rendered = render_template(custom, values_for_customer(customer, custom))
    # The level arrives in Persian and the money with its separators.
    assert rendered == "طلایی عزیز، 450,000 تومان بدهی دارید."


def test_a_slot_left_without_a_source_is_blank_and_the_editor_says_so(client, authed, db_session):
    """Nothing can fill an unbound custom slot, so the preview shows the hole
    instead of a sample the customer would never receive."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید",
                           body="%var1% عزیز، عید مبارک! %var2%")
    db_session.commit()

    preview = preview_body(custom)
    assert "متن نمونه" not in preview
    assert preview.startswith("سارا عزیز، عید مبارک!")

    editor = authed.get(f"/admin/sms/templates/{custom.id}/edit").text
    assert "sms-unfilled" in editor and "%var2%" in editor
    assert 'name="source_var2"' in editor
    assert "نام و نام خانوادگی" in editor          # the source catalog is offered


def test_an_unbound_slot_used_in_the_text_is_refused_with_a_reason(client, authed, db_session):
    """A hole used to be a warning the owner could save straight past. It is a
    refusal now, and the form says which token and what to do about it."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، %var2%")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{custom.id}/edit")

    refused = authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "عید مبارک", "body": "%var1% جان، %var3%",
        "source_var1": "first_name", "trigger_key": "",
    })
    db_session.expire_all()

    assert refused.status_code == 200                     # the form came back
    assert "به هیچ مقداری وصل نیست" in refused.text
    assert "%var3%" in refused.text                       # it names the token
    assert "ذخیره نمی‌شود" in refused.text                 # and what that means
    # Nothing was written: not the new name, not the new text.
    stored = db_session.query(SmsTemplate).filter(SmsTemplate.id == custom.id).one()
    assert (stored.name, stored.body) == ("تبریک عید", "%var1% عزیز، %var2%")

    # Binding the slot is what lets the text through.
    saved = authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "عید مبارک", "body": "%var1% جان، %var3%",
        "source_var1": "first_name", "source_var3": "total_debt", "trigger_key": "",
    }, follow_redirects=False)

    assert saved.status_code == 303
    db_session.expire_all()
    assert db_session.query(SmsTemplate).filter(
        SmsTemplate.id == custom.id,
    ).one().body == "%var1% جان، %var3%"


def test_the_refused_form_warns_about_the_text_that_was_rejected(client, authed, db_session):
    """The warning beside the form lists the token of the text just posted, not
    of the stored one — otherwise it points the owner at the wrong slot."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{custom.id}/edit")

    refused = authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "تبریک عید", "body": "%var1% عزیز، %var2%",
        "source_var1": "first_name",
    })

    assert refused.status_code == 200
    warning = refused.text.split('id="sms-unfilled"', 1)[1].split("</p>", 1)[0]
    assert "hidden" not in warning.split(">", 1)[0]       # the warning is up
    assert "%var2%" in warning                             # naming the rejected token


def test_a_new_template_with_an_unbound_slot_is_refused_too(client, authed, db_session):
    """The create form is guarded by the same rule, so a hole cannot be born."""
    token = csrf_token(client, "/admin/sms/templates/new")

    refused = authed.post("/admin/sms/templates/new", data={
        "csrf_token": token, "name": "تبریک عید", "body": "عید مبارک! %var2%",
    })

    assert refused.status_code == 200
    assert "به هیچ مقداری وصل نیست" in refused.text
    assert db_session.query(SmsTemplate).filter(SmsTemplate.category == "custom").count() == 0


def test_a_slot_the_text_does_not_use_may_stay_empty(client, authed, db_session):
    """The rule is about tokens the text uses. An unused slot may sit unbound, or
    it would have become a demand to fill all three every time."""
    ensure_seeded(db_session)
    custom = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، %var2%")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{custom.id}/edit")

    saved = authed.post(f"/admin/sms/templates/{custom.id}", data={
        "csrf_token": token, "name": "تبریک عید", "body": "%var1% عزیز",
        "source_var1": "first_name",
    }, follow_redirects=False)

    assert saved.status_code == 303
    db_session.expire_all()
    assert db_session.query(SmsTemplate).filter(
        SmsTemplate.id == custom.id,
    ).one().body == "%var1% عزیز"


# ── the chips insert whole sentences, not bare tokens ───────────────────────

def test_the_editor_offers_whole_sentences_instead_of_bare_tokens(client, authed, db_session):
    """A chip used to insert «%var1%», which is not something a shop writes. Each
    one now carries a sentence, with the wording visible on the chip itself."""
    page = authed.get("/admin/sms/templates/new").text

    assert "جمله‌های آماده" in page
    assert 'data-sentence="custom-greeting"' in page
    assert "سلام! امیدواریم حالتان خوب باشد" in page
    assert 'class="sms-chip" data-token=' not in page       # no bare-token chip left


def test_a_custom_sentence_only_uses_values_a_customer_really_has():
    """Every token in a custom sentence is bound to a real customer field, which
    is what keeps a chip from handing over a template the save would refuse."""
    custom = [item for item in SMS_SENTENCES if "custom" in item["categories"]]
    assert custom

    for sentence in custom:
        used = re.findall(r"%(\w+)%", sentence["text"])
        assert used, sentence["key"]
        for name in used:
            assert sentence["bind"].get(name) in SOURCE_FIELDS, (sentence["key"], name)


def test_every_suggested_custom_sentence_can_actually_be_saved(client, authed, db_session):
    """Posted exactly as the chip would leave the form — its text plus its own
    bindings — because a suggestion that cannot be saved is a trap, not a help."""
    create = csrf_token(client, "/admin/sms/templates/new")
    custom = [item for item in SMS_SENTENCES if "custom" in item["categories"]]

    for index, sentence in enumerate(custom):
        data = {"csrf_token": create, "name": f"جملهٔ {index}", "body": sentence["text"]}
        for slot, field in sentence["bind"].items():
            data[f"source_{slot}"] = field
        response = authed.post("/admin/sms/templates/new", data=data, follow_redirects=False)
        assert response.status_code == 303, sentence["key"]

    assert db_session.query(SmsTemplate).filter(
        SmsTemplate.category == "custom",
    ).count() == len(custom)


def test_a_builtin_sentence_uses_only_the_tokens_that_template_declares(db_session):
    """A built-in is filled by its own sender, so a sentence may only name tokens
    that sender really hands over — and it needs no binding of its own."""
    ensure_seeded(db_session)

    for template in templates_for(db_session):
        if not template.is_builtin:
            continue
        declared = {item["token"] for item in template_variables(template)}
        for sentence in sentences_for(template.category):
            used = set(re.findall(r"%(\w+)%", sentence["text"]))
            assert used <= declared, (template.key, sentence["key"], used - declared)
            assert sentence["bind"] == {}, sentence["key"]


def test_a_builtin_slot_nobody_has_yet_is_still_previewed_with_its_sample(db_session):
    """Built-ins are filled by their own sender (the campaign row, the amount),
    so their unfilled slots must keep previewing a sample."""
    ensure_seeded(db_session)
    campaign = get_template(db_session, "campaign")
    campaign.body = "%var1% عزیز، %var2% با کد %var3%"
    campaign.is_active = True
    db_session.commit()

    assert preview_body(campaign) == "سارا عزیز، جشنواره پاییز با کد AUTUMN20"
    assert unfilled_tokens(campaign) == []


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
    # Three columns each: something (plus how it fires), its state, its actions.
    assert 'قالب، متن و نحوه ارسال' in SMS_HTML and "عملیات" in SMS_HTML
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
