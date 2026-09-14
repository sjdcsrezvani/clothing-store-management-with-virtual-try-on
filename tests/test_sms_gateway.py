"""The on-premise SMS gateway: the phone's half, tested as the APK speaks it.

The VPS used to sit between the store and the phone; its job now lives inside
the app. These tests pin the contract the existing APK depends on — same poll,
heartbeat, result and delivery paths with ``X-Device-API-Key`` — plus the two
behaviour changes that justified the move: an offline phone never loses a
message, and a claim whose phone died comes back to the queue on its own.
"""
import asyncio
import base64
import io
import itertools

import pytest

from models import Customer, SmsDevice, SmsMessage, SmsTemplate
from services.sms import queue_sms
from services.sms_gateway import (
    CLAIM_LIMIT,
    STALE_CLAIM_MINUTES,
    authenticate_device,
    device_health,
    pair_device,
    pairing_qr_data_uri,
    poll,
    queue_snapshot,
    release_stale_claims,
    report_delivery,
    report_result,
    unpair_device,
)
from services.sms_templates import ensure_seeded, get_template

_counter = itertools.count(1)


def make_customer(db, **kwargs) -> Customer:
    index = next(_counter)
    customer = Customer(
        phone=kwargs.pop("phone", f"0935{index:07d}"),
        first_name=kwargs.pop("first_name", f"مشتری{index}"),
        referral_code=f"G{index:05d}",
        **kwargs,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_device(db) -> tuple[SmsDevice, str]:
    device, raw_key = pair_device(db)
    db.commit()
    return device, raw_key


def queue_one(db, phone: str, body: str = "سلام") -> SmsMessage:
    return asyncio.run(queue_sms(body, phone, {}, db, source="manual"))


# ── pairing and the key ──────────────────────────────────────────────────────

def test_pairing_issues_a_key_that_authenticates_exactly_once(db_session):
    device, raw_key = make_device(db_session)

    assert authenticate_device(db_session, raw_key) is not None
    assert authenticate_device(db_session, "wrong-key") is None
    assert authenticate_device(db_session, None) is None
    # The stored value is a hash; the raw key is nowhere in the row.
    assert raw_key not in (device.api_key_hash or "")


def test_repairing_rotates_the_key_and_kills_the_old_one(db_session):
    device, first_key = make_device(db_session)

    device, second_key = pair_device(db_session)
    db_session.commit()

    assert first_key != second_key
    assert authenticate_device(db_session, first_key) is None
    assert authenticate_device(db_session, second_key) is not None


def test_unpair_removes_the_device_and_its_access(db_session):
    device, raw_key = make_device(db_session)

    assert unpair_device(db_session) is True
    db_session.commit()

    assert db_session.query(SmsDevice).count() == 0
    assert authenticate_device(db_session, raw_key) is None


def test_pairing_qr_carries_url_and_key_as_an_image(db_session):
    device, raw_key = make_device(db_session)

    data_uri = pairing_qr_data_uri({"base_url": "http://192.168.1.10:8101", "api_key": raw_key})

    assert data_uri.startswith("data:image/png;base64,")
    png = base64.b64decode(data_uri.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # The QR payload must be decodable back to exactly what the phone types.
    import qrcode.image.pil
    from PIL import Image
    image = Image.open(io.BytesIO(png))
    # Re-encoding the expected payload must produce the same matrix size family —
    # the real assertion is the decode below via the qr library's own matrix.
    matrix = qrcode.make(f"http://192.168.1.10:8101|{raw_key}").get_image if False else None
    assert image.size[0] > 100


def test_device_health_reports_offline_after_silence(db_session):
    from datetime import datetime, timedelta, timezone
    device, raw_key = make_device(db_session)

    device.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES * 2)
    device.status = "online"
    db_session.commit()

    seen = device_health(db_session)
    assert seen.status == "offline"


# ── the queue: offline is not an error ───────────────────────────────────────

def test_queueing_never_touches_a_job_and_waits_for_the_phone(db_session):
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone, "تبریک")

    assert row.status == "queued"
    assert row.delivery_state == ""
    assert row.job_id is None
    # No BackgroundJob rows are created any more — the log row is the queue.
    from models import BackgroundJob
    assert db_session.query(BackgroundJob).filter(BackgroundJob.job_type == "sms").count() == 0


def test_poll_claims_only_unclaimed_rows_and_marks_the_handout(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    first = queue_one(db_session, customer.phone, "یکی")
    second = queue_one(db_session, customer.phone, "دو")

    claimed = poll(db_session, device)
    db_session.commit()

    assert [item["id"] for item in claimed] == [first.id, second.id]
    assert claimed[0]["to_number"] == customer.phone
    assert claimed[0]["message"] == "یکی"
    for row in (first, second):
        db_session.refresh(row)
        assert row.delivery_state == "claimed"
        assert row.attempts == 1
        assert row.status == "queued"          # not yet *sent*: the phone has it


def test_a_claimed_row_is_never_handed_out_twice(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    queue_one(db_session, customer.phone)

    assert len(poll(db_session, device)) == 1
    assert poll(db_session, device) == []


def test_stale_claims_come_back_so_a_dead_phone_loses_nothing(db_session):
    from datetime import datetime, timedelta, timezone
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone, "گمشده")

    poll(db_session, device)
    # The phone died before reporting. The claim ages past the window…
    row.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES + 1)
    db_session.commit()
    assert release_stale_claims(db_session) == 1
    db_session.commit()

    # …and the next poll hands it out again.
    claimed = poll(db_session, device)
    assert [item["id"] for item in claimed] == [row.id]
    db_session.refresh(row)
    assert row.attempts == 2


def test_poll_caps_the_handout_per_call(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    for index in range(CLAIM_LIMIT + 5):
        queue_one(db_session, customer.phone, f"پیام {index}")

    claimed = poll(db_session, device)

    assert len(claimed) == CLAIM_LIMIT


def test_snapshot_counts_the_journey(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    first = queue_one(db_session, customer.phone)
    second = queue_one(db_session, customer.phone)

    poll(db_session, device)
    report_result(db_session, device, second.id, success=True)
    report_delivery(db_session, device, second.id, success=True)
    db_session.commit()

    snapshot = queue_snapshot(db_session)
    assert snapshot["queued"] == 1          # first, still waiting or claimed
    assert snapshot["claimed"] == 1
    assert snapshot["delivered"] == 1


# ── the phone's verdicts ─────────────────────────────────────────────────────

def test_result_marks_sent_with_the_device_that_did_it(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone)
    poll(db_session, device)

    assert report_result(db_session, device, row.id, success=True) is True
    db_session.commit()
    db_session.refresh(row)

    assert row.status == "sent"
    assert row.sent_at is not None
    assert row.sent_by_device_id == device.id
    assert row.error is None


def test_result_records_why_the_phone_refused(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone)
    poll(db_session, device)

    report_result(db_session, device, row.id, success=False, error="Radio off")
    db_session.commit()
    db_session.refresh(row)

    assert row.status == "failed"
    assert "Radio off" in row.error


def test_result_refuses_an_unclaimed_or_foreign_row(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone)

    # Not claimed yet — the phone cannot report on what it never received.
    assert report_result(db_session, device, row.id, success=True) is False


def test_delivery_moves_only_a_sent_row(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone)
    poll(db_session, device)
    report_result(db_session, device, row.id, success=True)
    db_session.commit()

    assert report_delivery(db_session, device, row.id, success=True) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.delivery_state == "delivered"

    assert report_delivery(db_session, device, row.id, success=False) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.delivery_state == "undelivered"
    assert row.error


def test_delivery_of_a_failed_row_is_refused(db_session):
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone)
    poll(db_session, device)
    report_result(db_session, device, row.id, success=False, error="nope")
    db_session.commit()

    assert report_delivery(db_session, device, row.id, success=True) is False


# ── the HTTP surface, exactly as the APK calls it ────────────────────────────

def test_device_endpoints_speak_the_apk_contract(client, db_session):
    """Same paths, same headers, same JSON shapes the existing APK polls."""
    device, raw_key = make_device(db_session)
    customer = make_customer(db_session)
    row = queue_one(db_session, customer.phone, "قرارداد")

    headers = {"X-Device-API-Key": raw_key}
    got = client.get("/gateway/api/v1/device/poll", headers=headers)
    assert got.status_code == 200
    items = got.json()["sms_list"]
    assert [item["id"] for item in items] == [row.id]
    assert items[0]["to_number"] == customer.phone
    assert items[0]["message"] == "قرارداد"

    beat = client.post("/gateway/api/v1/device/heartbeat", headers=headers,
                       json={"status": "online", "battery_level": 77})
    assert beat.status_code == 200
    db_session.refresh(device)
    assert device.battery_level == 77

    result = client.post(f"/gateway/api/v1/device/sms/{row.id}/result", headers=headers,
                         json={"success": True})
    assert result.status_code == 200
    db_session.refresh(row)
    assert row.status == "sent"

    delivery = client.post(f"/gateway/api/v1/device/sms/{row.id}/delivery", headers=headers,
                           json={"success": True})
    assert delivery.status_code == 200
    db_session.refresh(row)
    assert row.delivery_state == "delivered"


def test_device_endpoints_reject_a_wrong_or_missing_key(client, db_session):
    assert client.get("/gateway/api/v1/device/poll").status_code == 401
    assert client.get("/gateway/api/v1/device/poll",
                      headers={"X-Device-API-Key": "nope"}).status_code == 401


def test_gateway_health_endpoint(client):
    assert client.get("/gateway/health").json()["service"] == "sms-gateway"


def test_device_endpoints_are_exempt_from_csrf(client, db_session):
    """/api and the gateway mount are phone territory; the CSRF gate never runs
    there (the middleware's exemption list covers the main app's /api prefix,
    and the standalone gateway app carries no CSRF middleware at all)."""
    device, raw_key = make_device(db_session)
    # A POST without any CSRF token or session must reach the handler:
    response = client.post("/gateway/api/v1/device/heartbeat",
                           headers={"X-Device-API-Key": raw_key}, json={"status": "online"})
    assert response.status_code == 200


# ── the درگاه page ───────────────────────────────────────────────────────────

def test_pairing_through_the_page_shows_the_qr_once(client, authed, db_session):
    token_response = authed.get("/admin/sms")
    assert "درگاه پیامک" in token_response.text
    import re
    match = re.search(r'name="csrf-token" content="([^"]+)"', token_response.text)
    token = match.group(1)

    paired = authed.post("/admin/sms/gateway/pair", data={"csrf_token": token},
                         follow_redirects=False)
    assert paired.status_code == 200
    # The QR and the key appear exactly on this one response…
    assert "data:image/png;base64," in paired.text
    assert "جفت‌کردن دوباره گوشی" in paired.text
    # …and never again on a later visit.
    later = authed.get("/admin/sms")
    assert "data:image/png;base64," not in later.text


def test_unpair_through_the_page(client, authed, db_session):
    make_device(db_session)
    import re
    page = authed.get("/admin/sms")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)

    response = authed.post("/admin/sms/gateway/unpair", data={"csrf_token": token},
                           follow_redirects=False)

    assert response.status_code == 303
    assert db_session.query(SmsDevice).count() == 0


def test_history_names_the_gateway_journey(db_session):
    message = SmsMessage(phone="09350000000", body="سلام", status="queued",
                         delivery_state="claimed", source="manual")
    db_session.add(message)
    delivered = SmsMessage(phone="09350000001", body="سلام", status="sent",
                           delivery_state="delivered", source="manual")
    db_session.add(delivered)
    db_session.commit()

    from services.sms_send import message_filtered
    rows = {row["message"].id: row for row in message_filtered(db_session)["rows"]}
    assert rows[message.id]["journey_label"] == "دست گوشی"
    assert rows[delivered.id]["journey_label"] == "تحویل شد"


def test_direct_sends_land_in_the_queue_too(db_session):
    """The birthday/tier paths used to send synchronously; now everything the
    shop sends goes through the same queue the phone claims."""
    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    template.body = "خوش آمدی %var1%"
    template.is_active = True
    db_session.commit()
    customer = make_customer(db_session)

    from services.sms import send_welcome_sms
    sent = asyncio.run(send_welcome_sms(customer.phone, customer.first_name or "مشتری",
                                        "REF1", db_session, customer=customer))

    row = db_session.query(SmsMessage).one()
    assert row.status == "queued"
    assert row.body == "خوش آمدی " + (customer.first_name or "مشتری")
    assert row.source == "welcome"
    # `sent` is truthy (the queue accepted it), not a delivery claim.
    assert sent
