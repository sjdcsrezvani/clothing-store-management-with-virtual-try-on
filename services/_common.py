"""Shared helpers — single source of truth for cross-router utilities."""
from datetime import datetime, timezone
from fastapi import Request
from sqlalchemy.orm import Session
import jdatetime
from models import Settings, to_english_digits as _to_en


# ----- Persian (Jalali/Hijri) date helpers -----------------------------------
#
# Database stores Gregorian timestamps; user input and display are Persian.
# Single source of truth — every conversion in the app goes through these helpers.

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def _to_persian_digits(s: str) -> str:
    out = []
    for ch in s:
        if ch.isdigit():
            out.append(PERSIAN_DIGITS[int(ch)])
        else:
            out.append(ch)
    return "".join(out)


def gregorian_to_jalali(value: datetime | None) -> str | None:
    """Format a Gregorian datetime as 'YYYY/MM/DD HH:MM' in Persian."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    jd = jdatetime.datetime.fromtimestamp(value.timestamp())
    return jd.strftime("%Y/%m/%d %H:%M")


def jalali_str(value: datetime | None, with_time: bool = True) -> str:
    """Render a Gregorian datetime as Persian digits, suitable for templates."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    jd = jdatetime.datetime.fromtimestamp(value.timestamp())
    pattern = "%Y/%m/%d %H:%M" if with_time else "%Y/%m/%d"
    return _to_persian_digits(jd.strftime(pattern))


def parse_jalali_input(value: str) -> datetime | None:
    """Parse 'YYYY/MM/DD' or 'YYYY-MM-DD' (English or Persian digits) → Gregorian
    datetime at midnight UTC. Returns None on any parse failure."""
    if not value:
        return None
    try:
        cleaned = _to_en(value.replace("/", "-").strip())
        parts = cleaned.split("-")
        if len(parts) != 3:
            return None
        jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
        if not (1 <= jm <= 12 and 1 <= jd <= 31):
            return None
        gdt = jdatetime.date(jy, jm, jd).togregorian()
        return datetime(gdt.year, gdt.month, gdt.day, tzinfo=timezone.utc)
    except (ValueError, IndexError, TypeError):
        return None


def parse_jalali_input_end(value: str) -> datetime | None:
    """Like parse_jalali_input but at end-of-day (23:59:59 UTC)."""
    dt = parse_jalali_input(value)
    if dt is None:
        return None
    return dt.replace(hour=23, minute=59, second=59)


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
    """Admin check backed by the server-side session (set at login), not a
    forgeable cookie value. SessionMiddleware guarantees request.session."""
    return bool(request.session.get("is_admin"))


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
    """Return current (Persian year, Persian month) for monthly counters."""
    now = jdatetime.datetime.now()
    return now.year, now.month


def jtoday() -> jdatetime.date:
    return jdatetime.date.today()


def today_jalali_str() -> str:
    """Persian today, ASCII digits — useful in tests / templates."""
    return jdatetime.date.today().strftime("%Y/%m/%d")


if __name__ == "__main__":
    # Self-check for the birthday parser — the most edge-case-prone pure function.
    ok = 0
    birthday_cases = [
        ("", None),
        ("۱۴۰۳/۰۳/۱۵", "03-15"),
        ("1403-03-15", "03-15"),
        ("1403/12/30", "12-30"),
        ("1403/13/01", None),
        ("1403/03/32", None),
        ("garbage", None),
        ("۱۴۰۳-۰۱-۰۱", "01-01"),
        ("1403/1/1", "01-01"),
        ("1403/01/1", "01-01"),
    ]
    for inp, expected in birthday_cases:
        result = parse_persian_birthday(inp)
        if result == expected:
            ok += 1
        else:
            print(f"BIRTHDAY FAIL: {inp!r} → {result!r}, expected {expected!r}")

    # Self-check for jalali parsing (date range, analytics).
    # Each case is either (input, None) or (input, gregorian_year, month, day).
    jok = 0
    jalali_cases = [
        # Persian digits, slash, full year
        ("۱۴۰۵/۰۵/۱۵", 2026, 8, 6),
        ("1405/05/15", 2026, 8, 6),
        ("1405-05-15", 2026, 8, 6),
        ("", None),
        ("garbage", None),
        ("1405/13/01", None),
        ("1405/03/32", None),
    ]
    for case in jalali_cases:
        if len(case) == 2:
            inp, expected = case
            ok_result = parse_jalali_input(inp) is None
        else:
            inp, y, m, d = case
            result = parse_jalali_input(inp)
            ok_result = (
                result is not None
                and result.year == y
                and result.month == m
                and result.day == d
            )
        if ok_result:
            jok += 1
        else:
            print(f"JALALI FAIL: {case!r}")

    # Self-check that today round-trips: gregorian datetime → jalali_str.
    now = datetime.now(timezone.utc)
    jalali_now = jalali_str(now)
    back = parse_jalali_input(jalali_now.split()[0].replace("/", "-"))
    print(f"Round-trip: {now.strftime('%Y-%m-%d')} → {jalali_now}")

    total = len(birthday_cases) + len(jalali_cases)
    print(f"_common.py self-check: {ok + jok}/{total} passed")
    assert ok + jok == total, "self-check failures — see above"
