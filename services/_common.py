"""Shared helpers — single source of truth for cross-router utilities."""
from datetime import datetime, timezone
from fastapi import Request
from sqlalchemy.orm import Session
from models import Settings, to_english_digits as _to_en


def fmt(amount: int) -> str:
    return f"{amount:,}"


def get_setting_int(db: Session, key: str, default: int) -> int:
    """Read a Settings row by key, return int or default. Used by every router."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting and setting.value:
        try:
            return int(setting.value)
        except (ValueError, TypeError):
            return default
    return default


def check_admin(request: Request) -> bool:
    return request.cookies.get("is_admin") == "true"


def parse_persian_birthday(value: str) -> str | None:
    """Parse YYYY/MM/DD or YYYY-MM-DD to MM-DD. Returns None on failure or empty input."""
    return _parse_birthday(value)


def _parse_birthday(value: str) -> str | None:
    if not value:
        return None
    try:
        cleaned = _to_en(value.replace("/", "-").strip())
        parts = cleaned.split("-")
        if len(parts) == 3:
            m, d = int(parts[1]), int(parts[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return f"{m:02d}-{d:02d}"
    except (ValueError, IndexError):
        pass
    return None


def current_year_month() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    return now.year, now.month


if __name__ == "__main__":
    # Self-check for the birthday parser — the most edge-case-prone pure function.
    # This ran during development with a bug that lost the month-day split.
    ok = 0
    cases = [
        ("", None),
        ("۱۴۰۳/۰۳/۱۵", "03-15"),
        ("1403-03-15", "03-15"),
        ("1403/12/30", "12-30"),
        ("1403/13/01", None),   # invalid month
        ("1403/03/32", None),   # invalid day
        ("garbage", None),
        ("۱۴۰۳-۰۱-۰۱", "01-01"),
        ("1403/1/1", "01-01"),
        ("1403/01/1", "01-01"),
    ]
    for inp, expected in cases:
        result = parse_persian_birthday(inp)
        if result == expected:
            ok += 1
        else:
            print(f"FAIL: {inp!r} → {result!r}, expected {expected!r}")
    total = len(cases)
    print(f"_common.py self-check: {ok}/{total} passed")
    assert ok == total, f"{total - ok} failures"
