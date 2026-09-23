from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from models import CheckRecord, CheckReminder, Settings, to_english_digits
from services._common import parse_jalali_input
from services.events import append_event

DEFAULT_REMINDER_DAYS = (14, 7, 3)
MAX_REMINDER_DAYS = 365
MAX_REMINDERS_PER_CHECK = 10


def now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_reminder_days(value: str | Iterable[int] | None) -> list[int]:
    """Return unique positive reminder offsets in descending order."""
    if value is None:
        return list(DEFAULT_REMINDER_DAYS)
    if isinstance(value, str):
        cleaned = to_english_digits(value)
        parts = re.split(r"[,\s،؛;]+", cleaned.strip()) if cleaned.strip() else []
    else:
        parts = list(value)

    days: set[int] = set()
    for part in parts:
        if part in (None, ""):
            continue
        try:
            day = int(part)
        except (TypeError, ValueError) as error:
            raise ValueError("روزهای هشدار باید عددی باشند") from error
        if day <= 0 or day > MAX_REMINDER_DAYS:
            raise ValueError(f"روز هشدار باید بین ۱ تا {MAX_REMINDER_DAYS} باشد")
        days.add(day)

    if len(days) > MAX_REMINDERS_PER_CHECK:
        raise ValueError(f"حداکثر {MAX_REMINDERS_PER_CHECK} هشدار برای هر چک مجاز است")
    return sorted(days, reverse=True)


# The check form's amount floor, named once: the form paints its min from it
# and parse_amount_rials refuses below it, so the input can never invite a
# figure the server refuses.
CHECK_AMOUNT_MIN = 1


def parse_amount_rials(value: str | int | None) -> int:
    cleaned = to_english_digits(str(value or "")).replace(",", "").replace("٬", "").replace(" ", "")
    try:
        amount = int(cleaned)
    except (TypeError, ValueError) as error:
        raise ValueError("مبلغ چک معتبر نیست") from error
    if amount < CHECK_AMOUNT_MIN:
        raise ValueError("مبلغ چک باید بیشتر از صفر باشد")
    return amount


def parse_check_date(value: str | None) -> datetime | None:
    """Accept Persian Jalali dates and ISO Gregorian dates."""
    if not value:
        return None
    cleaned = to_english_digits(value.strip()).replace("/", "-")
    parts = cleaned.split("-")
    if len(parts) != 3:
        return None
    try:
        year = int(parts[0])
    except ValueError:
        return None
    if year >= 1900:
        try:
            return datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=timezone.utc)
        except ValueError:
            return None
    return parse_jalali_input(cleaned)


def reminder_days_from_record(check: CheckRecord) -> list[int]:
    try:
        import json
        value = json.loads(check.reminder_days or "[]")
    except (TypeError, ValueError):
        return []
    try:
        return normalize_reminder_days(value)
    except ValueError:
        return []


def add_reminders(db: Session, check: CheckRecord, reminder_days: Iterable[int]) -> None:
    import json

    days = normalize_reminder_days(reminder_days)
    now_at = now()
    db.query(CheckReminder).filter(
        CheckReminder.check_id == check.id,
        CheckReminder.status.in_(["pending", "triggered"]),
    ).update({"status": "dismissed", "dismissed_at": now_at}, synchronize_session=False)
    check.reminder_days = json.dumps(days, separators=(",", ":"))
    for day in days:
        db.add(CheckReminder(
            check_id=check.id,
            days_before=day,
            remind_at=check.due_at - timedelta(days=day),
            status="pending",
        ))


def get_default_reminder_days(db: Session) -> list[int]:
    setting = db.query(Settings).filter(Settings.key == "check_default_reminders").first()
    try:
        return normalize_reminder_days(setting.value if setting else None)
    except ValueError:
        return list(DEFAULT_REMINDER_DAYS)


def reminders_enabled(db: Session) -> bool:
    setting = db.query(Settings).filter(Settings.key == "check_reminders_enabled").first()
    return setting is None or setting.value not in {"0", "false", "False", "off"}


def trigger_due_reminders(db: Session, at: datetime | None = None) -> int:
    """Turn due pending reminders into durable in-app alarms."""
    if not reminders_enabled(db):
        return 0
    at = at or now()
    due = (
        db.query(CheckReminder)
        .join(CheckRecord, CheckRecord.id == CheckReminder.check_id)
        .filter(
            CheckReminder.status == "pending",
            CheckReminder.remind_at <= at,
            CheckRecord.status == "issued",
        )
        .all()
    )
    for reminder in due:
        reminder.status = "triggered"
        reminder.triggered_at = at
        append_event(
            db,
            "CheckReminderTriggered",
            "check",
            reminder.check_id,
            idempotency_key=f"check:{reminder.check_id}:reminder:{reminder.id}:triggered",
            actor_user_id=reminder.check.operator_user_id if reminder.check else None,
            payload={
                "reminder_id": reminder.id,
                "days_before": reminder.days_before,
                "remind_at": reminder.remind_at.isoformat(),
            },
            occurred_at=at,
        )
    return len(due)


def dismiss_reminders(db: Session, check_id: int) -> None:
    db.query(CheckReminder).filter(
        CheckReminder.check_id == check_id,
        CheckReminder.status.in_(["pending", "triggered"]),
    ).update({"status": "dismissed", "dismissed_at": now()}, synchronize_session=False)


def check_alert_summary(db: Session, at: datetime | None = None) -> dict[str, object]:
    at = at or now()
    triggered = (
        db.query(CheckReminder)
        .join(CheckRecord, CheckRecord.id == CheckReminder.check_id)
        .filter(CheckReminder.status == "triggered", CheckRecord.status == "issued")
        .order_by(CheckRecord.due_at.asc(), CheckReminder.days_before.asc())
        .all()
    )
    overdue = db.query(CheckRecord).filter(
        CheckRecord.status == "issued",
        CheckRecord.due_at < at,
    ).order_by(CheckRecord.due_at.asc()).all()
    upcoming = db.query(CheckRecord).filter(
        CheckRecord.status == "issued",
        CheckRecord.due_at >= at,
        CheckRecord.due_at <= at + timedelta(days=14),
    ).order_by(CheckRecord.due_at.asc()).all()
    return {
        "triggered": triggered,
        "overdue": overdue,
        "upcoming": upcoming,
        "triggered_count": len(triggered),
        "overdue_count": len(overdue),
        "upcoming_count": len(upcoming),
    }
