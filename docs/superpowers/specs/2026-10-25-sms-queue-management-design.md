# SMS Queue Management — Bigger Version Design

**Date:** 2026-10-25

**Status:** Direction approved; implementation plan pending review.

## Intent

The owner needs to clear stuck or unwanted SMS from the queue and retry failed
messages, without losing the shop's record of what happened. Today the history
page only offers a per-row retry for failed rows; a queued row cannot be removed.

## Goals

- Delete one queued or failed message. It disappears from the active queue and
  history, but goes to a trash that the owner can inspect and restore.
- Select multiple rows and delete or retry them in one action.
- “Select all with this filter” applies to every matching row, not only the
  current page.
- Restore a trashed message. The trash is the safety net; permanent delete is
  deliberately not part of this build.
- A per-message detail page: text, recipient, source, template, values,
  attempts, device, error, timeline, and retry chain.
- Each retry is a new queued row linked to the original (`retried_from_id`).
- Owner-only destructive actions; managers may retry; every action is audited.
- A deleted message is never handed to the SMS gateway.

## Non-goals for this build

- Pausing/resuming the whole gateway queue.
- CSV export of the queue.
- Stuck-queue dashboard alerts.
- Scheduled sending.
- Permanent delete from the trash. Trashed rows remain restorable; a future
  purge needs a tombstone so automatic triggers cannot recreate a purged
  message.
- Cancelling a message the phone has already claimed within the last five
  minutes. The phone may already hold the text; deleting it then would be a lie.
  The UI refuses with an explanation instead.
- Hard-deleting sent messages. Sent rows are the permanent send log.

## User-facing behaviour

### History list

- Every row gets a checkbox when it can be acted on.
- Queued and failed rows get a **حذف** action (owner only).
- Failed rows keep **ارسال دوباره** (manager and above).
- Trashed rows are hidden from the default list. The status filter gains
  **حذف‌شده** and the KPI row gains a **سطل زباله** card linking to it.
- A **جزئیات** link opens the message detail page.
- The bulk bar appears when at least one row is selected:
  - **حذف انتخاب‌شده‌ها** — moves selected queued/failed rows to trash.
  - **ارسال دوباره ناموفق‌ها** — re-queues selected failed rows.
  - In the trash view: **بازگردانی انتخاب‌شده‌ها**.
- When a specific status filter is active (`queued`, `failed`, or `deleted`),
  a **انتخاب همه N مورد با این فیلتر** button selects the whole filtered set,
  not just the visible page.

### Delete rules

- Allowed: `queued` rows that are unclaimed, or whose claim is older than five
  minutes; and `failed` rows.
- Refused: `sent` rows, already trashed rows, and `queued` rows claimed within
  the last five minutes.
- Deleting a queued row stops it from being handed to the gateway because every
  gateway query excludes trashed rows.
- Deleting a failed row simply moves it out of the active history.
- The delete and the gateway claim are made mutually exclusive by conditional
  `UPDATE` statements: the claim only succeeds while `deleted_at IS NULL`, and
  the delete only succeeds while the row is not freshly claimed. A read filter
  alone would leave a race between the two sessions.

### Trash rules

- **Restore** clears `deleted_at` and `deleted_by_user_id`.
  - Restoring a queued row puts it back in the queue and it may send. The UI
    warns about this.
  - Restoring a failed row returns it to the failed list.
  - Restoring a row the phone reported as sent after deletion returns it to the
    sent log, not the queue; see “Late phone reports” below.
- Trashed rows are ordered by `deleted_at`, newest deletion first, and the
  `today`/`week`/`month` date presets filter on `deleted_at` in the trash view.
  “Undo what I just deleted” must not bury an old message deleted today.
- There is no permanent-delete button in this build. The trash is the final
  state and every row stays restorable.

### Late phone reports

The five-minute guard prevents deleting a message the phone just claimed. A
phone that died can still report after the guard, or after a delete. The store
must not lose that truth:

- `report_result` still finds a claimed row even when it is trashed.
- It records `sent_at` / `status = 'sent'` / `error` as usual, but leaves
  `deleted_at` set.
- The trash row shows a note that the phone reported a send after deletion.
- Restoring that row returns it to the sent log, not to the queue, so a restore
  cannot cause a duplicate send.

### Retry rules

- Only `failed` rows that are not trashed can be retried.
- A retry creates a new `queued` row with the same frozen text, phone, source,
  kind, template, customer, values, and ref.
- The new row stores `retried_from_id = original.id`.
- The original row is unchanged.
- Trashed failed rows must be restored before they can be retried.

### Detail page

`GET /admin/sms/history/{message_id}` shows:

- status badge (including **حذف‌شده**), phone, customer link, source, template;
- created, claimed, sent, and deleted timestamps where present;
- attempts, device, and error;
- full body and the recorded customer values / replay verdict;
- the original message if this row is a retry, and every retry of this row;
- actions allowed for the current role and row state.

## Data model

Additive migration 38; no table rebuild:

- `sms_messages.deleted_at DATETIME NULL`
- `sms_messages.deleted_by_user_id INTEGER NULL` → `staff_users.id`
- `sms_messages.retried_from_id INTEGER NULL` → `sms_messages.id`
  (`ON DELETE SET NULL` in the model for future-proofing; this build never
  deletes a message row)

`STATUS_LABELS` gains `deleted: "حذف‌شده"`. A helper
`display_status(message)` returns `"deleted"` when `deleted_at` is set,
otherwise `message.status`.

Filter behaviour:

| Filter | Query |
|---|---|
| `all` | `deleted_at IS NULL` |
| `queued` | `status = 'queued' AND deleted_at IS NULL` |
| `sent` | `status = 'sent' AND deleted_at IS NULL` |
| `failed` | `status = 'failed' AND deleted_at IS NULL` |
| `deleted` | `deleted_at IS NOT NULL` |

Counts:

- `sms_queue_snapshot` counts queued/failed rows with `deleted_at IS NULL`.
- `gateway_status` queued count excludes trashed rows.
- `message_overview` counts non-deleted rows for total/sent/queued/failed and
  for `today`/`marketing`; it adds a separate `deleted` count. This keeps
  `total - marketing` honest.
- `template_card` keeps `total` as the template's whole history (including
  trashed rows, so “has this ever spoken?” stays true), but counts `failed` and
  `queued` only for non-deleted rows. `queued` is counted explicitly rather
  than `total - sent - failed`.
- `already_fired` deliberately still counts trashed rows, so deleting an
  automatic message does not make the next trigger sweep recreate it.
- `delete_blocked_reason` still counts trashed rows, because the history still
  exists and deleting the template would still erase it.
- gateway `queue_snapshot` counts queued/claimed rows with `deleted_at IS NULL`.

Gateway safety:

- `poll` uses conditional `UPDATE ... WHERE deleted_at IS NULL` for both fresh
  and stale claims, and only returns rows whose update actually matched.
- `release_stale_claims` adds `deleted_at IS NULL`.
- `report_result` deliberately does **not** filter on `deleted_at`; it records
  a late phone verdict on a trashed claimed row (see “Late phone reports”).

## Permissions

- History list and detail page: manager and above (existing `_guard`).
- Delete and restore: owner only (`_owner_guard`).
- Retry: manager and above (existing `_guard`).
- Template shows destructive controls only when the role allows them; routes
  enforce the same rule independently.

## Audit

`log_action` entries:

- `sms_delete` — one row or bulk, with deleted/skipped counts.
- `sms_restore` — one row or bulk.
- `sms_retry` — existing single action; bulk adds one entry with counts.

Single-row actions include `target_type="sms_message"` and `target_id`; bulk
actions record counts in `after`.

## Files

- `models.py` — three new columns.
- `migrations.py` — revision 38 and version bump.
- `services/sms_templates.py` — `STATUS_LABELS`.
- `services/sms_send.py` — display status, filters, counts, delete/restore,
  retry link, detail data.
- `services/sms_gateway.py` — exclude trashed rows from poll/queue; keep late
  phone reports for trashed claimed rows.
- `routers/sms.py` — history filters, single/bulk routes, detail route,
  permissions, audit.
- `templates/admin/sms_history.html` — KPI, filter, checkboxes, bulk bar,
  actions, detail links, JS.
- `templates/admin/sms_message.html` — new detail page.
- `templates/admin/sms.html` — trash KPI card.
- `tests/test_sms.py` — queue management tests.
- `tests/test_migrations.py` — revision 38 columns and version assertion.

## Testing strategy

- Migration: legacy rows keep NULL; new columns exist.
- Filters: default hides trashed; each status filter is exact; trashed rows
  never appear in queue counts.
- Delete: unclaimed queued and failed rows move to trash; fresh claimed and sent
  rows are refused; deleted rows disappear from the gateway poll.
- Restore: trashed rows return; a restored queued row is claimable again; a row
  reported sent after deletion returns to sent, not queued.
- Late phone report: a claimed row deleted after the guard still records
  `sent`/`sent_at` while staying trashed.
- Retry: failed rows create a linked new row; trashed/queued/sent rows are
  refused; bulk retry skips ineligible rows.
- Permissions: owner can delete/restore; manager gets 403 on delete/restore and
  can retry.
- Bulk all-filtered: applies to every matching row, not only the current page.
- Trash ordering and presets use `deleted_at`; an old message deleted today is
  still first.
- Counts: trashed rows leave queue/failed/overview/template-card counts, but
  still count in `already_fired` and `delete_blocked_reason`.
- Detail page: shows the timeline and retry chain; actions match the role.
- Full suite remains green.

## Known limitation

Deleting a message that the phone claimed within the last five minutes is
refused. The store cannot revoke text already handed to the phone without an
APK-side cancellation check, which is outside this build.
