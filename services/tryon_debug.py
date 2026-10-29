"""In-memory log of recent try-on provider attempts.

The model sweep needs the exact body fields, HTTP status, provider error code
and timings for each generation. A ring buffer keeps that out of the database:
the log is diagnostic, not a record of the shop, and restarts may drop it.
"""
from collections import deque
from datetime import datetime
from threading import Lock

MAX_ATTEMPTS = 40

_attempts: deque = deque(maxlen=MAX_ATTEMPTS)
_lock = Lock()


def record(**fields) -> dict:
    fields.setdefault("at", datetime.now())
    with _lock:
        _attempts.append(fields)
    return fields


def recent() -> list[dict]:
    """Newest first, so the panel opens on the last attempt."""
    with _lock:
        return list(reversed(_attempts))


def latest_for_job(job_id) -> dict | None:
    if not job_id:
        return None
    with _lock:
        for attempt in reversed(_attempts):
            if attempt.get("job_id") == job_id:
                return attempt
    return None


def clear() -> None:
    with _lock:
        _attempts.clear()
