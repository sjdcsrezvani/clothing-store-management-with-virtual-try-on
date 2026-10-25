"""Queue management: trash filters, counts, delete/restore, retry links."""
from datetime import datetime, timedelta, timezone

from models import SmsMessage
from services.sms_send import (
    display_status,
    message_filtered,
    message_ids_matching,
    message_overview,
    sms_queue_snapshot,
    template_card,
)
from services.sms_gateway import queue_snapshot
from services.sms_templates import ensure_seeded, get_template
from tests.test_sms import make_customer


def _row(db, *, status="queued", deleted_at=None, body="سلام", **kwargs):
    row = SmsMessage(phone=kwargs.pop("phone", "09120000001"), body=body,
                     status=status, source=kwargs.pop("source", "manual"),
                     kind=kwargs.pop("kind", "marketing"),
                     deleted_at=deleted_at, **kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_deleted_rows_leave_the_default_history_and_queue_counts(db_session):
    active = _row(db_session, body="active")
    trashed = _row(db_session, body="trashed", deleted_at=datetime.now(timezone.utc))

    page = message_filtered(db_session)
    assert [row["message"].id for row in page["rows"]] == [active.id]
    assert display_status(active) == "queued"
    assert display_status(trashed) == "deleted"

    trash = message_filtered(db_session, status="deleted")
    assert [row["message"].id for row in trash["rows"]] == [trashed.id]
    assert message_overview(db_session)["deleted"] == 1
    assert sms_queue_snapshot(db_session)["queued"] == 1
    assert queue_snapshot(db_session)["queued"] == 1


def test_trash_orders_and_date_presets_by_deleted_at(db_session):
    old_created = datetime.now(timezone.utc) - timedelta(days=40)
    old_deleted_today = _row(db_session, body="old", created_at=old_created,
                             deleted_at=datetime.now(timezone.utc))
    new_deleted_yesterday = _row(db_session, body="new",
                                 created_at=datetime.now(timezone.utc),
                                 deleted_at=datetime.now(timezone.utc) - timedelta(days=1))

    rows = message_filtered(db_session, status="deleted")["rows"]
    assert [row["message"].id for row in rows] == [old_deleted_today.id, new_deleted_yesterday.id]

    today = message_filtered(db_session, status="deleted", date_preset="today")
    assert [row["message"].id for row in today["rows"]] == [old_deleted_today.id]


def test_message_ids_matching_uses_the_same_filter(db_session):
    keep = _row(db_session, status="failed", body="keep")
    _row(db_session, status="failed", deleted_at=datetime.now(timezone.utc), body="trash")
    _row(db_session, status="queued", body="queued")

    ids = message_ids_matching(db_session, status="failed")
    assert ids == [keep.id]


def test_template_card_counts_active_failures_but_keeps_total_history(db_session):
    ensure_seeded(db_session)
    template = get_template(db_session, "campaign")
    template.body = "سلام"
    db_session.commit()

    active = _row(db_session, status="failed", template_id=template.id,
                  template_name=template.name)
    _row(db_session, status="failed", template_id=template.id, template_name=template.name,
         deleted_at=datetime.now(timezone.utc))

    card = template_card(db_session, template)
    assert card["total"] == 2
    assert card["failed"] == 1
    assert card["queued"] == 0


def test_already_fired_still_sees_a_trashed_queued_message(db_session):
    from services.sms_triggers import already_fired

    ensure_seeded(db_session)
    template = get_template(db_session, "welcome")
    customer = make_customer(db_session)
    _row(db_session, status="queued", template_id=template.id, customer_id=customer.id,
         ref="sale:1", deleted_at=datetime.now(timezone.utc))

    assert already_fired(db_session, template, customer, "sale:1") is True


def test_delete_moves_queued_and_failed_rows_to_trash(db_session):
    from services.sms_send import delete_messages
    from tests.test_roles import _staff

    owner, _ = _staff(db_session, "sms-delete-owner", "owner")
    queued = _row(db_session, status="queued")
    failed = _row(db_session, status="failed", body="failed")
    sent = _row(db_session, status="sent", body="sent")

    result = delete_messages(db_session, [queued.id, failed.id, sent.id], user_id=owner.id)
    assert result == {"deleted": 2, "skipped_busy": 0,
                      "skipped_sent": 1, "already_deleted": 0}
    db_session.refresh(queued)
    db_session.refresh(failed)
    db_session.refresh(sent)
    assert queued.deleted_at is not None and queued.deleted_by_user_id == owner.id
    assert failed.deleted_at is not None
    assert sent.deleted_at is None


def test_delete_refuses_a_fresh_claim_and_allows_a_stale_one(db_session):
    from datetime import datetime, timedelta, timezone
    from services.sms_send import delete_messages
    from tests.test_roles import _staff

    owner, _ = _staff(db_session, "sms-claim-owner", "owner")
    fresh = _row(db_session, status="queued", delivery_state="claimed",
                 claimed_at=datetime.now(timezone.utc))
    stale = _row(db_session, status="queued", delivery_state="claimed",
                 claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10))

    result = delete_messages(db_session, [fresh.id, stale.id], user_id=owner.id)
    assert result["deleted"] == 1 and result["skipped_busy"] == 1
    db_session.refresh(fresh)
    db_session.refresh(stale)
    assert fresh.deleted_at is None
    assert stale.deleted_at is not None


def test_restore_brings_a_trashed_row_back(db_session):
    from services.sms_send import restore_messages

    row = _row(db_session, deleted_at=datetime.now(timezone.utc))
    result = restore_messages(db_session, [row.id])
    db_session.refresh(row)
    assert result == {"restored": 1, "skipped": 0}
    assert row.deleted_at is None and row.deleted_by_user_id is None
