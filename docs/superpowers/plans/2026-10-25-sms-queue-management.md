# SMS Queue Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner delete queued/failed SMS into a restorable trash, bulk-delete or retry selected rows (including all rows matching the current filter), and inspect each message's timeline and retry chain, without ever handing a trashed message to the gateway.

**Architecture:** Add three nullable columns to `sms_messages` (deleted timestamp/actor and retry link). Treat a non-null `deleted_at` as the “trash” status everywhere: filters, counts, gateway claims. Destructive routes are owner-only; retry routes stay manager-level. A new detail page reuses the same row data as the history list.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, Jinja2, pytest, existing `services/sms_send.py` and `services/sms_gateway.py` helpers.

**Spec:** `docs/superpowers/specs/2026-10-25-sms-queue-management-design.md`

## Global Constraints

- Keep all existing SMS history/audit semantics: the frozen body is never rewritten.
- No hard delete in this build. Trashed rows stay restorable.
- Owner-only: delete, restore. Manager and above: retry, history, detail.
- A trashed row must never be claimed by the SMS gateway.
- A message claimed by the phone within the last `STALE_CLAIM_MINUTES` (5) is not deletable.
- `report_result` must still record a late phone verdict on a trashed claimed row.
- All query-param filters must degrade to safe defaults, never a 500.
- Tests run with `python -m pytest`; the full suite is the release gate.

## Review Focus

1. **Claim/delete race:** a queued row can be claimed by the gateway thread while the owner deletes it. The claim and delete must be conditional UPDATEs, not read-then-write.
2. **Trash visibility:** a trashed row must not appear in queue counts, gateway polls, or default history, but must still count in `already_fired`.
3. **Late phone report:** a phone that reports after deletion must update the row's real send result without clearing `deleted_at`; restoring must not re-queue it.
4. **Bulk all-filtered:** `all=1` must use the same sanitized filters as the list, not the current page's ids.
5. **Permissions:** the manager must not see or execute delete/restore; the route enforces it independently of the template.
6. **Trash ordering:** an old message deleted today must sort/date-filter by `deleted_at`, not `created_at`.

---

### Task 1: Schema, migration, status vocabulary

**Files:**
- Modify: `models.py` (SmsMessage, around line 1211)
- Modify: `migrations.py` (`MIGRATION_VERSION`, `_apply_revision`)
- Modify: `services/sms_templates.py` (`STATUS_LABELS`, line 64)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `SmsMessage.deleted_at`, `SmsMessage.deleted_by_user_id`, `SmsMessage.retried_from_id`
- Produces: `STATUS_LABELS["deleted"] == "حذف‌شده"`

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_migrations.py`:

```python
def test_revision_38_adds_sms_queue_management_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'v37-sms.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 37)"))
        conn.execute(text("""
            CREATE TABLE sms_messages (
                id INTEGER PRIMARY KEY, phone VARCHAR(20) NOT NULL, body TEXT NOT NULL,
                status VARCHAR(20) NOT NULL, kind VARCHAR(20) NOT NULL,
                source VARCHAR(30) NOT NULL, created_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("INSERT INTO sms_messages (phone, body, status, kind, source, created_at) "
                          "VALUES ('09120000000', 'سلام', 'queued', 'marketing', 'manual', "
                          "'2026-01-01 00:00:00')"))

    assert upgrade(engine) == MIGRATION_VERSION == 38

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(sms_messages)"))}
        assert {"deleted_at", "deleted_by_user_id", "retried_from_id"} <= columns
        row = conn.execute(text(
            "SELECT deleted_at, deleted_by_user_id, retried_from_id FROM sms_messages WHERE id=1"
        )).one()
        assert row == (None, None, None)
```

Update the existing assertion in `test_revision_36_and_37_add_the_sizing_columns`:

```python
assert upgrade(engine) == MIGRATION_VERSION == 38
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_migrations.py::test_revision_38_adds_sms_queue_management_columns -v`
Expected: FAIL (version is still 37 / columns missing).

- [ ] **Step 3: Implement the columns and revision**

In `models.py`, inside `SmsMessage` after `sent_at`:

```python
    # Trash: a non-null deleted_at hides the row from the active queue/history.
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    # Retry chain: the new queued row points at the failed row it was born from.
    retried_from_id = Column(Integer, ForeignKey("sms_messages.id", ondelete="SET NULL"),
                             nullable=True)
```

In `migrations.py`:

```python
MIGRATION_VERSION = 38
```

Add the revision body at the top of `_apply_revision`:

```python
    if version == 38:
        # SMS queue management: soft-delete marker/actor and retry chain.
        # Additive; legacy rows keep NULL and read exactly as before.
        _add_column_if_missing(conn, "sms_messages", "deleted_at", "DATETIME")
        _add_column_if_missing(conn, "sms_messages", "deleted_by_user_id", "INTEGER")
        _add_column_if_missing(conn, "sms_messages", "retried_from_id", "INTEGER")
        return
```

In `services/sms_templates.py`:

```python
STATUS_LABELS = {"queued": "در صف", "sent": "ارسال‌شده", "failed": "ناموفق",
                 "deleted": "حذف‌شده"}
```

- [ ] **Step 4: Run the migration tests**

Run: `python -m pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py migrations.py services/sms_templates.py tests/test_migrations.py
git commit -m "feat: add SMS trash and retry-link columns"
```

---

### Task 2: Read-side status, filters, ordering, and counts

**Files:**
- Modify: `services/sms_send.py`
- Modify: `services/sms_gateway.py`
- Test: `tests/test_sms_queue_management.py` (new)

**Interfaces:**
- Produces: `display_status(message) -> str` (`"deleted"` when `deleted_at` is set, else `message.status`)
- Produces: `message_ids_matching(db, *, search, status, source, date_preset) -> list[int]`
- Consumes: `SmsMessage.deleted_at` from Task 1

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sms_queue_management.py` with:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_sms_queue_management.py -v`
Expected: FAIL (`display_status`/`message_ids_matching` missing, counts include trash).

- [ ] **Step 3: Implement the read-side service changes**

In `services/sms_send.py`:

```python
def display_status(message: SmsMessage) -> str:
    """The status the history shows: trash wins over the delivery state."""
    return "deleted" if message.deleted_at is not None else message.status
```

Extract the existing filter/search body of `message_filtered` into:

```python
def _message_query(db: Session, *, search: str = "", status: str = "all",
                   source: str = "all", date_preset: str = "all"):
    query = db.query(SmsMessage)
    if status == "deleted":
        query = query.filter(SmsMessage.deleted_at.is_not(None))
    elif status in ("queued", "sent", "failed"):
        query = query.filter(SmsMessage.status == status,
                             SmsMessage.deleted_at.is_(None))
    else:
        query = query.filter(SmsMessage.deleted_at.is_(None))
    if source in SOURCE_LABELS:
        query = query.filter(SmsMessage.source == source)
    cutoff = _preset_cutoff(date_preset if date_preset in DATE_PRESETS else "all")
    date_column = SmsMessage.deleted_at if status == "deleted" else SmsMessage.created_at
    if cutoff is not None:
        query = query.filter(date_column >= cutoff)
    # ... move the existing search branch here unchanged ...
    return query
```

`message_filtered` then starts with `query = _message_query(...)`, orders by
`deleted_at` (then `id`) when `status == "deleted"`, otherwise by `id` as today,
and sets `"status_key": display_status(row)` and
`"late_sent": bool(row.deleted_at and row.status == "sent")` in each row dict.
Its row `status_label` becomes `STATUS_LABELS.get(display_status(row), ...)`.

Add:

```python
def message_ids_matching(db: Session, *, search: str = "", status: str = "all",
                         source: str = "all", date_preset: str = "all") -> list[int]:
    query = _message_query(db, search=search, status=status, source=source,
                           date_preset=date_preset).with_entities(SmsMessage.id)
    return [row[0] for row in query.all()]
```

Change `sms_queue_snapshot` to count `deleted_at.is_(None)` for queued and failed.

Change `message_overview`: use an `active = SmsMessage.deleted_at.is_(None)` filter
for total/sent/queued/failed/today/marketing; add `"deleted": count(deleted_at.is_not(None))`.

Change `template_card`: keep `total = count()` (all history), but compute
`failed = count(active, status == "failed")`, `queued = count(active, status == "queued")`.

In `services/sms_gateway.py`, add `SmsMessage.deleted_at.is_(None)` to the queued
count in `gateway_status` and to the three counts in `queue_snapshot`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms.py tests/test_sms_gateway.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/sms_send.py services/sms_gateway.py tests/test_sms_queue_management.py
git commit -m "feat: filter, order, and count trashed SMS"
```

---

### Task 3: Delete/restore services and claim safety

**Files:**
- Modify: `services/sms_send.py`
- Modify: `services/sms_gateway.py`
- Test: `tests/test_sms_queue_management.py`
- Test: `tests/test_sms_gateway.py`

**Interfaces:**
- Produces: `delete_messages(db, ids, *, user_id) -> dict`
  with keys `deleted`, `skipped_busy`, `skipped_sent`, `already_deleted`
- Produces: `restore_messages(db, ids) -> dict` with keys `restored`, `skipped`
- Changes: `poll` claims with conditional UPDATE and `deleted_at IS NULL`
- Changes: `report_result` records late verdicts on trashed claimed rows

- [ ] **Step 1: Write the failing service tests**

Append to `tests/test_sms_queue_management.py`:

```python
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
```

Append to `tests/test_sms_gateway.py`:

```python
def test_poll_never_claims_a_trashed_row(db_session):
    from services.sms_gateway import poll
    from datetime import datetime, timezone
    from models import SmsMessage

    device, _ = make_device(db_session)
    db_session.add(SmsMessage(phone="09120000077", body="trash", status="queued",
                              source="manual", kind="marketing",
                              deleted_at=datetime.now(timezone.utc)))
    db_session.commit()

    assert poll(db_session, device) == []


def test_late_result_on_a_trashed_claim_is_recorded(db_session):
    from services.sms_gateway import report_result
    from services.sms_send import restore_messages
    from datetime import datetime, timezone
    from models import SmsMessage

    device, _ = make_device(db_session)
    row = SmsMessage(phone="09120000078", body="late", status="queued",
                     source="manual", kind="marketing", delivery_state="claimed",
                     claimed_at=datetime.now(timezone.utc),
                     deleted_at=datetime.now(timezone.utc))
    db_session.add(row)
    db_session.commit()

    assert report_result(db_session, device, row.id, success=True) is True
    db_session.refresh(row)
    assert row.status == "sent" and row.sent_at is not None
    assert row.deleted_at is not None

    assert restore_messages(db_session, [row.id]) == {"restored": 1, "skipped": 0}
    db_session.refresh(row)
    assert row.deleted_at is None and row.status == "sent"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms_gateway.py -v`
Expected: FAIL (`delete_messages`/`restore_messages` missing, poll claims trash).

- [ ] **Step 3: Implement delete/restore**

In `services/sms_send.py`:

```python
def delete_messages(db: Session, ids: list[int], *, user_id: int) -> dict:
    """Move queued/failed rows to trash. Fresh claims and sent rows are refused."""
    from services.sms_gateway import STALE_CLAIM_MINUTES

    unique_ids = list(dict.fromkeys(int(i) for i in ids if str(i).isdigit()))
    result = {"deleted": 0, "skipped_busy": 0, "skipped_sent": 0,
              "already_deleted": 0}
    if not unique_ids:
        return result

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=STALE_CLAIM_MINUTES)).replace(tzinfo=None)
    for row in db.query(SmsMessage).filter(SmsMessage.id.in_(unique_ids)).all():
        if row.deleted_at is not None:
            result["already_deleted"] += 1
            continue
        if row.status == "sent":
            result["skipped_sent"] += 1
            continue
        if row.status not in ("queued", "failed"):
            continue
        conditions = [SmsMessage.id == row.id, SmsMessage.deleted_at.is_(None),
                      SmsMessage.status.in_(("queued", "failed"))]
        if row.status == "queued":
            conditions.append(or_(
                SmsMessage.delivery_state != "claimed",
                SmsMessage.claimed_at.is_(None),
                SmsMessage.claimed_at < cutoff,
            ))
        changed = db.query(SmsMessage).filter(*conditions).update(
            {"deleted_at": now, "deleted_by_user_id": user_id},
            synchronize_session=False,
        )
        if changed:
            result["deleted"] += 1
            continue
        db.refresh(row)
        if row.deleted_at is not None:
            result["already_deleted"] += 1
        elif row.status == "sent":
            result["skipped_sent"] += 1
        elif row.status == "queued" and row.delivery_state == "claimed":
            result["skipped_busy"] += 1
    db.commit()
    return result
```

```python
def restore_messages(db: Session, ids: list[int]) -> dict:
    ids = list(dict.fromkeys(int(i) for i in ids if str(i).isdigit()))
    rows = db.query(SmsMessage).filter(
        SmsMessage.id.in_(ids or [0]), SmsMessage.deleted_at.is_not(None),
    ).all() if ids else []
    for row in rows:
        row.deleted_at = None
        row.deleted_by_user_id = None
    db.commit()
    return {"restored": len(rows), "skipped": max(0, len(ids) - len(rows))}
```

- [ ] **Step 4: Implement gateway claim safety**

In `services/sms_gateway.py`, replace the claim loop in `poll` with conditional
updates. Keep the fresh/stale SELECTs but add `SmsMessage.deleted_at.is_(None)`.
For each candidate:

```python
    claimed = []
    for row in (*fresh, *stale):
        conditions = [
            SmsMessage.id == row.id,
            SmsMessage.status == "queued",
            SmsMessage.deleted_at.is_(None),
        ]
        if row.delivery_state == "":
            conditions.append(SmsMessage.delivery_state == "")
        else:
            conditions.extend([
                SmsMessage.delivery_state == "claimed",
                SmsMessage.claimed_at.is_not(None),
                SmsMessage.claimed_at < stale_cutoff.replace(tzinfo=None),
            ])
        changed = db.query(SmsMessage).filter(*conditions).update(
            {"delivery_state": "claimed", "claimed_at": now,
             "attempts": SmsMessage.attempts + 1},
            synchronize_session=False,
        )
        if changed:
            claimed.append({"id": row.id, "to_number": row.phone,
                            "message": row.body})
    db.flush()
    return claimed
```

Add `SmsMessage.deleted_at.is_(None)` to `release_stale_claims`.
In `report_result`, remove any `deleted_at is None` filter if one was added; keep
the existing status/claim filters. On success, set `row.error` to the late-send
note when `row.deleted_at` is set; keep `deleted_at` untouched. On failure, keep
`deleted_at` too.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms_gateway.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/sms_send.py services/sms_gateway.py tests/test_sms_queue_management.py tests/test_sms_gateway.py
git commit -m "feat: soft-delete SMS and keep the gateway claim race closed"
```

---

### Task 4: Retry link and detail data

**Files:**
- Modify: `services/sms_send.py`
- Test: `tests/test_sms_queue_management.py`

**Interfaces:**
- Produces: `retry_message` stores `new_row.retried_from_id = original.id`
- Produces: `retry_messages(db, ids) -> dict` with `resent`, `skipped`
- Produces: `message_detail(db, message_id) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sms_queue_management.py`:

```python
def test_retry_links_new_row_to_original(db_session):
    import asyncio
    from services.sms_send import retry_message

    original = _row(db_session, status="failed", body="دوباره", source="manual",
                    kind="marketing")
    row, error = asyncio.run(retry_message(db_session, original.id))
    assert error == "" and row is not None
    assert row.status == "queued" and row.body == "دوباره"
    assert row.retried_from_id == original.id
    assert original.status == "failed"


def test_trashed_failed_row_does_not_retry_until_restored(db_session):
    import asyncio
    from services.sms_send import retry_message, restore_messages

    original = _row(db_session, status="failed",
                    deleted_at=datetime.now(timezone.utc))
    row, error = asyncio.run(retry_message(db_session, original.id))
    assert row is None and "سطل زباله" in error

    restore_messages(db_session, [original.id])
    row, error = asyncio.run(retry_message(db_session, original.id))
    assert error == "" and row is not None and row.retried_from_id == original.id


def test_message_detail_returns_retry_chain(db_session):
    import asyncio
    from services.sms_send import message_detail, retry_message

    original = _row(db_session, status="failed", body="اصل")
    retry, _ = asyncio.run(retry_message(db_session, original.id))
    detail = message_detail(db_session, original.id)
    assert detail["message"].id == original.id
    assert [row.id for row in detail["retries"]] == [retry.id]

    child = message_detail(db_session, retry.id)
    assert child["original"].id == original.id
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_sms_queue_management.py -v`
Expected: FAIL (`retried_from_id` not set, `message_detail` missing).

- [ ] **Step 3: Implement**

In `retry_message`, before the status check:

```python
    if message.deleted_at is not None:
        return None, "این پیامک در سطل زباله است؛ اول آن را بازگردانید."
```

After `queue_sms` returns a row:

```python
    new_row.retried_from_id = message.id
    db.commit()
```

Add:

```python
async def retry_messages(db: Session, ids: list[int]) -> dict:
    ids = [int(i) for i in ids if str(i).isdigit()]
    resent = skipped = 0
    for message_id in ids:
        row, _ = await retry_message(db, message_id)
        if row is not None:
            resent += 1
        else:
            skipped += 1
    return {"resent": resent, "skipped": skipped}
```

Add `message_detail(db, message_id)` returning the same row data as
`message_filtered` for one message, plus `original`, `retries`, `device`, and
`late_sent`. Use `message_values(message, template)` for `audit`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms.py -v`
Expected: PASS (including the existing retry test).

- [ ] **Step 5: Commit**

```bash
git add services/sms_send.py tests/test_sms_queue_management.py
git commit -m "feat: link SMS retries to their original message"
```

---

### Task 5: History routes, permissions, bulk actions

**Files:**
- Modify: `routers/sms.py`
- Test: `tests/test_sms_queue_management.py`

**Interfaces:**
- Produces routes:
  - `POST /admin/sms/history/bulk-delete` (owner)
  - `POST /admin/sms/history/bulk-restore` (owner)
  - `POST /admin/sms/history/bulk-resend` (manager)
- The history context gains `can_delete` (owner) and `can_retry` (True).
- `all=1` in the form resolves ids via `message_ids_matching` with the posted
  `search`, `status`, `source`, `date_preset`.

- [ ] **Step 1: Write the failing route tests**

Append to `tests/test_sms_queue_management.py`:

```python
def test_owner_can_delete_and_restore_via_bulk_routes(client, authed, db_session):
    from tests.conftest import csrf_token
    row = _row(db_session, status="queued")
    token = csrf_token(client, "/admin/sms/history")

    deleted = authed.post("/admin/sms/history/bulk-delete",
                          data={"csrf_token": token, "ids": str(row.id)},
                          follow_redirects=False)
    assert deleted.status_code == 303
    db_session.refresh(row)
    assert row.deleted_at is not None

    restored = authed.post("/admin/sms/history/bulk-restore",
                           data={"csrf_token": token, "ids": str(row.id)},
                           follow_redirects=False)
    assert restored.status_code == 303
    db_session.refresh(row)
    assert row.deleted_at is None


def test_manager_cannot_delete_or_restore(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _session_as, _staff

    manager, password = _staff(db_session, "sms-mgr-delete", "manager")
    _session_as(client, manager, password)
    row = _row(db_session, status="queued")
    token = csrf_token(client, "/admin/sms/history")

    assert client.post("/admin/sms/history/bulk-delete",
                       data={"csrf_token": token, "ids": str(row.id)},
                       follow_redirects=False).status_code == 403
    assert client.post("/admin/sms/history/bulk-restore",
                       data={"csrf_token": token, "ids": str(row.id)},
                       follow_redirects=False).status_code == 403
    assert row.deleted_at is None


def test_manager_can_bulk_resend_failed(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _session_as, _staff

    manager, password = _staff(db_session, "sms-mgr-retry", "manager")
    _session_as(client, manager, password)
    failed = [_row(db_session, status="failed", body=f"f{i}") for i in range(2)]
    token = csrf_token(client, "/admin/sms/history")

    response = client.post("/admin/sms/history/bulk-resend",
                           data={"csrf_token": token,
                                 "ids": [str(row.id) for row in failed]},
                           follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(SmsMessage).filter(SmsMessage.status == "queued").count() == 2


def test_bulk_delete_all_filtered_goes_beyond_the_page(client, authed, db_session):
    from tests.conftest import csrf_token
    rows = [_row(db_session, status="failed", body=f"bulk{i}") for i in range(30)]
    token = csrf_token(client, "/admin/sms/history")

    response = authed.post("/admin/sms/history/bulk-delete", data={
        "csrf_token": token, "all": "1", "status": "failed",
        "search": "", "source": "all", "date_preset": "all",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(SmsMessage).filter(SmsMessage.deleted_at.isnot(None)).count() == 30
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_sms_queue_management.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Implement the routes**

Add `from urllib.parse import quote_plus` to `routers/sms.py`. Add a helper:

```python
def _ids_from_form(form) -> list[int]:
    return [int(raw) for raw in form.getlist("ids") if str(raw).isdigit()]


def _bulk_ids(form, db) -> list[int]:
    if str(form.get("all", "0")) == "1":
        return message_ids_matching(
            db, search=str(form.get("search", "")),
            status=str(form.get("status", "all")),
            source=str(form.get("source", "all")),
            date_preset=str(form.get("date_preset", "all")),
        )
    return _ids_from_form(form)
```

Add the three POST routes with `_owner_guard` / `_guard` as above, calling
`delete_messages`, `restore_messages`, `retry_messages`, `log_action`, and
redirecting with a `quote_plus` message. Add `"can_delete": guard.role == "owner"`,
`"can_retry": True` to the history context.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routers/sms.py tests/test_sms_queue_management.py
git commit -m "feat: add owner bulk-delete/restore and manager bulk-retry routes"
```

---

### Task 6: History page UI and bulk selection

**Files:**
- Modify: `templates/admin/sms_history.html`
- Modify: `templates/admin/sms.html`
- Modify: `static/css/style.css`
- Test: `tests/test_sms_queue_management.py`

**Interfaces:**
- Consumes: `can_delete`, `can_retry`, `overview.deleted`, rows with
  `status_key`/`late_sent`.

- [ ] **Step 1: Write the failing template tests**

Append to `tests/test_sms_queue_management.py`:

```python
def test_history_page_offers_owner_delete_and_trash_controls(client, authed, db_session):
    row = _row(db_session, status="queued")
    page = authed.get("/admin/sms/history").text
    assert "سطل زباله" in page
    assert "/admin/sms/history/bulk-delete" in page
    assert "sms-bulk-check" in page
    assert f"/admin/sms/history/{row.id}" in page


def test_history_page_hides_delete_from_a_manager(client, db_session):
    from tests.test_roles import _session_as, _staff

    manager, password = _staff(db_session, "sms-mgr-page", "manager")
    _session_as(client, manager, password)
    _row(db_session, status="queued")
    page = client.get("/admin/sms/history").text
    assert "/admin/sms/history/bulk-delete" not in page
    assert "sms-bulk-check" not in page


def test_trash_kpi_links_to_the_deleted_filter(client, authed):
    page = authed.get("/admin/sms/history").text
    assert "/admin/sms/history?status=deleted" in page
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_sms_queue_management.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the history template**

- Add a fifth KPI card for `overview.deleted` linking to
  `/admin/sms/history?status=deleted{{ kpi_qs }}` (same pattern in `sms.html`).
- Add a `bulk-cell` checkbox header and a checkbox cell for selectable rows.
- Add an `عملیات` column with `جزئیات`, `ارسال دوباره` (failed), `حذف`
  (owner, queued/failed), `بازگردانی` (owner, deleted).
- Add the bulk form before the table with hidden `search`, `status`, `source`,
  `date_preset`, `all`; buttons `bulk-delete` / `bulk-resend` / `bulk-restore`;
  and the `#sms-bulk-select-all` button when
  `(status == 'failed') or (can_delete and status in ('queued', 'deleted'))`.
- Remove the old retry form from the status cell.
- Add `initSmsHistoryRegion` and `htmx:load` rebinding (copy the customers-page
  pattern). The confirm gate must use `event.submitter` and
  `form.requestSubmit(submitter)` so the bulk form keeps its `formaction` and
  `name=action`-equivalent; keep the `pendingForm` bypass to avoid a loop.
- Add `.status-badge.is-deleted` CSS.

- [ ] **Step 4: Run the template tests and the SMS suite**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/admin/sms_history.html templates/admin/sms.html static/css/style.css tests/test_sms_queue_management.py
git commit -m "feat: add SMS trash and bulk selection to history"
```

---

### Task 7: Message detail page

**Files:**
- Modify: `routers/sms.py`
- Create: `templates/admin/sms_message.html`
- Test: `tests/test_sms_queue_management.py`

**Interfaces:**
- Produces: `GET /admin/sms/history/{message_id}` (manager+)
- Consumes: `message_detail(db, message_id)`

- [ ] **Step 1: Write the failing detail tests**

Append to `tests/test_sms_queue_management.py`:

```python
def test_detail_page_shows_timeline_body_and_retry_chain(client, authed, db_session):
    import asyncio
    from services.sms_send import retry_message
    original = _row(db_session, status="failed", body="متن اصلی")
    retry, _ = asyncio.run(retry_message(db_session, original.id))

    page = authed.get(f"/admin/sms/history/{original.id}").text
    assert "متن اصلی" in page
    assert "جزئیات" in page
    assert f"/admin/sms/history/{retry.id}" in page


def test_detail_page_actions_match_role(client, db_session):
    from tests.test_roles import _session_as, _staff
    from tests.conftest import csrf_token

    row = _row(db_session, status="failed")
    owner_page = client.get("/admin/sms/history")  # authed fixture handles owner
    # this test is split below for the manager case
```

Simplify: use the `authed` fixture for the owner assertions in the first test,
and a separate manager test:

```python
def test_detail_page_hides_delete_from_manager(client, db_session):
    from tests.test_roles import _session_as, _staff
    manager, password = _staff(db_session, "sms-mgr-detail", "manager")
    _session_as(client, manager, password)
    row = _row(db_session, status="failed")
    page = client.get(f"/admin/sms/history/{row.id}").text
    assert "/admin/sms/history/bulk-delete" not in page
    assert "ارسال دوباره" in page
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_sms_queue_management.py -v`
Expected: FAIL (route/template 404).

- [ ] **Step 3: Implement**

Add the GET route after the history route. If `message_detail` returns `None`,
raise `HTTPException(404, "پیامک پیدا نشد.")`. Pass
`can_delete=guard.role == "owner"`, `can_retry=True` and the detail dict.

Create `templates/admin/sms_message.html` with:
- page header and back link;
- status badge (including deleted), phone, customer, source, template;
- created/claimed/sent/deleted timestamps;
- attempts, device, error, late-send note;
- body and `audit` values/replay;
- retry chain links;
- single-row delete/restore/retry forms with the simple confirm dialog pattern.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_sms_queue_management.py tests/test_sms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routers/sms.py templates/admin/sms_message.html tests/test_sms_queue_management.py
git commit -m "feat: add the SMS message detail page"
```

---

### Task 8: Full regression pass

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: all tests pass, zero failures.

- [ ] **Step 2: Fix any regressions**

Likely touch points: existing SMS tests that assert a 4-column table, counts
that now exclude trash, and the migration version assertion. Fix the test or
the code, never by weakening the new behaviour.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: keep the SMS suite green after queue management"
```

## Self-Review

- **Spec coverage:** schema/status, filters/counts, delete/restore, claim race,
  late reports, retry link, bulk all-filtered, permissions, detail page, trash
  ordering, KPI card, audit — each maps to a task above.
- **Type consistency:** `delete_messages` keys are used verbatim in the route
  summary; `restore_messages` returns `restored`/`skipped`; `retry_messages`
  returns `resent`/`skipped`; `message_detail` returns the keys the detail
  template consumes.
- **Review focus tests:** claim race (Task 3), trash visibility (Task 2/3),
  late report (Task 3), bulk all-filtered (Task 5), permissions (Task 5/6),
  trash ordering (Task 2).
- **Not included:** pause/resume, CSV export, dashboard alerts, scheduled
  sending, permanent delete. These are listed as non-goals in the spec.
