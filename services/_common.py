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


# Jalali years never reach 1900, so a form value starting with one can only be a
# Gregorian ISO date. Without this check ``parse_jalali_input('2026-09-12')``
# happily reads 2026 as a Jalali year and returns the year 2647.
def _gregorian_prefix(value: str) -> datetime | None:
    cleaned = value.strip()
    if len(cleaned) < 10 or not _to_en(cleaned[:4]).isdigit():
        return None
    if int(_to_en(cleaned[:4])) < 1900:
        return None
    iso = _to_en(cleaned[:10]).replace("/", "-").replace(".", "-")
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_form_date(value) -> datetime | None:
    """Canonical form-date reader: Persian Jalali (۱۴۰۵/۰۶/۲۱) or ISO Gregorian."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return _gregorian_prefix(cleaned) or parse_jalali_input(cleaned)


def parse_form_date_end(value) -> datetime | None:
    """Like parse_form_date but at end-of-day (23:59:59 UTC)."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    gregorian = _gregorian_prefix(cleaned)
    if gregorian is not None:
        return gregorian.replace(hour=23, minute=59, second=59)
    return parse_jalali_input_end(cleaned)


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
    """Legacy boolean admin-session check kept for compatibility."""
    return bool(request.session.get("staff_user_id"))


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


def jalali_month_start(reference: datetime | None = None) -> datetime:
    """Midnight UTC on the first day of the current Persian month."""
    reference = reference or datetime.now(timezone.utc)
    jnow = jdatetime.date.fromgregorian(date=reference.astimezone(timezone.utc).date())
    start = jdatetime.date(jnow.year, jnow.month, 1).togregorian()
    return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)


def today_jalali_str() -> str:
    """Persian today, ASCII digits — useful in tests / templates."""
    return jdatetime.date.today().strftime("%Y/%m/%d")


# ----- Customer birthdays and ages -------------------------------------------
#
# A birthday is stored twice: the Persian MM-DD (so it can be matched every
# year) and the Persian year (so an age can be shown). The customer's own
# birthday and the child's use the same shape; which of them a store celebrates
# is a setting, because a children's shop and an adult clothing shop want
# different answers.

JALALI_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)

CHILD_PROFILE_KEY = "child_profile_enabled"
BIRTHDAY_TARGET_KEY = "birthday_target"
BIRTHDAY_TARGETS = ("customer", "child", "both")
BIRTHDAY_TARGET_LABELS = {
    "customer": "تولد خود مشتری",
    "child": "تولد فرزند",
    "both": "هر دو",
}
BIRTHDAY_SUBJECT_LABELS = {"customer": "مشتری", "child": "فرزند"}
# How a birthday reads in a sale's discount line and in an SMS (var2/var3), so a
# single pattern text can serve both kinds of shop.
BIRTHDAY_DISCOUNT_LABELS = {"customer": "تخفیف تولد شما", "child": "تخفیف تولد فرزند"}
BIRTHDAY_SMS_NAMES = {"customer": "شما", "child": "فرزند شما"}

# Who a customer buys for. This is the customer's own choice, asked once at
# signup and editable afterwards — not the store's setting, which only supplies
# the default for someone who has not chosen yet.
BUYS_FOR_SELF = "self"
BUYS_FOR_CHILD = "child"
BUYS_FOR_CHOICES = (BUYS_FOR_SELF, BUYS_FOR_CHILD)
BUYS_FOR_LABELS = {
    BUYS_FOR_SELF: "برای خودم",
    BUYS_FOR_CHILD: "برای فرزندم",
}
# The birthday each choice celebrates.
BUYS_FOR_SUBJECTS = {
    BUYS_FOR_SELF: ("customer",),
    BUYS_FOR_CHILD: ("child",),
}


def get_setting_bool(db: Session, key: str, default: bool = True) -> bool:
    """Read a Settings row as a boolean. Missing/empty keeps the default."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting is None or setting.value is None or str(setting.value) == "":
        return default
    return str(setting.value).strip().lower() not in ("0", "false", "no", "off")


def child_profile_enabled(db: Session) -> bool:
    """Whether this store collects and shows child details (a children's shop)."""
    return get_setting_bool(db, CHILD_PROFILE_KEY, True)


def get_birthday_target(db: Session) -> str:
    """Whose birthday drives the discount and the wish: customer, child or both."""
    setting = db.query(Settings).filter(Settings.key == BIRTHDAY_TARGET_KEY).first()
    value = (setting.value or "").strip() if setting is not None else ""
    return value if value in BIRTHDAY_TARGETS else "child"


def birthday_subjects(db: Session) -> tuple[str, ...]:
    """The store's *default* birthdays, in priority order.

    This is no longer the authority on whose birthday a given customer is
    wished: every customer carries their own `buys_for` choice, and
    `customer_birthday_subjects` resolves one customer against it. What is left
    here is the answer for a customer who has never chosen, plus the source of
    the default a signup form pre-selects.

    Child birthdays are only ever considered while the child module is on, and a
    child-only configuration with that module off falls back to the customer's
    own birthday rather than to no birthday at all.
    """
    target = get_birthday_target(db)
    child_on = child_profile_enabled(db)
    subjects = []
    if target in ("customer", "both"):
        subjects.append("customer")
    if target in ("child", "both") and child_on:
        subjects.append("child")
    return tuple(subjects) or ("customer",)


def default_buys_for(db: Session) -> str:
    """The choice a signup form pre-selects, derived from the store's target.

    A store with no child module can only ever be bought from for oneself, so
    the child option is not offered there at all.
    """
    if not child_profile_enabled(db):
        return BUYS_FOR_SELF
    return BUYS_FOR_CHILD if get_birthday_target(db) in ("child", "both") else BUYS_FOR_SELF


def normalise_buys_for(value, db: Session) -> str | None:
    """A posted choice, or None when it isn't one this store can honour.

    A store without the child module rejects the child option outright, so a
    hand-posted form cannot put a customer into a programme the shop doesn't run.
    """
    mode = (value or "").strip()
    if mode not in BUYS_FOR_CHOICES:
        return None
    if mode == BUYS_FOR_CHILD and not child_profile_enabled(db):
        return None
    return mode


def customer_birthday_subjects(db: Session, customer) -> tuple[str, ...]:
    """Which birthday *this* customer is wished on.

    The customer's own choice wins over the store's default — that is the point
    of asking, and it means a self-buyer is wished on their own birthday even in
    a children's shop. A row that never chose keeps following the store's target
    (which is why the column is nullable and the migration left it alone).
    A stored child choice in a shop that has since switched the child module off
    falls back to the customer's own birthday rather than to no birthday at all,
    matching what `birthday_subjects` does for the store as a whole.
    """
    mode = (getattr(customer, "buys_for", None) or "").strip()
    if mode == BUYS_FOR_SELF:
        return BUYS_FOR_SUBJECTS[BUYS_FOR_SELF]
    if mode == BUYS_FOR_CHILD:
        return (("child",) if child_profile_enabled(db) else ("customer",))
    return birthday_subjects(db)


def marketing_opt_in(customer) -> bool:
    """Only an explicit 0 opts out of marketing SMS; NULL means never asked."""
    return customer.sms_opt_in is None or bool(customer.sms_opt_in)


def is_archived_customer(customer) -> bool:
    return bool(customer.is_archived)


def parse_persian_month_day(value) -> tuple[int, int] | None:
    """'06-21' / '1405/06/21' → (6, 21). None when it isn't a month-day."""
    if not value:
        return None
    text = _to_en(str(value).replace("/", "-").strip())
    tail = text.split("-")[-2:]
    if len(tail) != 2:
        return None
    try:
        month, day = int(tail[0]), int(tail[1])
    except (ValueError, TypeError):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def parse_persian_birthday_full(value) -> tuple[str | None, int | None]:
    """A form birthday → (Persian MM-DD, Persian year). Either half may be None.

    Reads Jalali ('1405/06/21', Persian digits included) and, like every other
    date field in the app, an ISO Gregorian value, so hand-typed input can't be
    stored as a Jalali year.
    """
    if not value:
        return (None, None)
    text = _to_en(str(value).replace("/", "-").strip())
    parts = text.split("-")
    if len(parts) != 3:
        return (None, None)
    try:
        year, month, day = (int(part) for part in parts)
    except (ValueError, TypeError):
        return (None, None)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return (None, None)
    if year >= 1900:  # ISO Gregorian — convert instead of storing 2026 as a Jalali year
        try:
            jalali = jdatetime.date.fromgregorian(date=datetime(year, month, day).date())
        except (ValueError, OverflowError):
            return (None, None)
        return (f"{jalali.month:02d}-{jalali.day:02d}", jalali.year)
    if not (1200 <= year <= 1500):
        return (None, None)
    return (f"{month:02d}-{day:02d}", year)


def days_until_jalali_birthday(month_day: str | None, today: jdatetime.date | None = None) -> int | None:
    """Days from today to the next occurrence of a Persian MM-DD birthday."""
    parts = parse_persian_month_day(month_day)
    if not parts:
        return None
    month, day = parts
    today = today or jdatetime.date.today()
    try:
        this_year = jdatetime.date(today.year, month, day)
    except ValueError:
        return None
    if this_year >= today:
        return (this_year - today).days
    try:
        next_year = jdatetime.date(today.year + 1, month, day)
    except ValueError:
        # 30 Esfand in a year that isn't a leap year — the day before marks it.
        next_year = jdatetime.date(today.year + 1, month, max(1, day - 1))
    return (next_year - today).days


def jalali_age(year: int | None, month_day: str | None = None,
               today: jdatetime.date | None = None) -> int | None:
    """Age in Persian years, or None when the year was never recorded."""
    if not year:
        return None
    today = today or jdatetime.date.today()
    age = today.year - int(year)
    parts = parse_persian_month_day(month_day)
    if parts and (today.month, today.day) < parts:
        age -= 1  # this year's birthday hasn't happened yet
    return max(0, age)


def birthday_form_value(month_day: str | None, year: int | None) -> str:
    """Rebuild the field value: the full date when the year is known, else MM-DD.

    Rendered in Persian digits, like `jalali_str` renders every other date held in
    a form field — a picker re-emits Persian digits, so a Latin value here would
    change its own appearance the first time the calendar was used.
    """
    parts = parse_persian_month_day(month_day)
    if not parts:
        return ""
    month, day = parts
    if year:
        return _to_persian_digits(f"{int(year):04d}/{month:02d}/{day:02d}")
    return _to_persian_digits(f"{month:02d}-{day:02d}")


def birthday_display(month_day: str | None, year: int | None = None) -> str:
    """Readable birthday: «۲۱ شهریور ۱۳۸۰» when the year is known, else «۲۱ شهریور»."""
    parts = parse_persian_month_day(month_day)
    if not parts:
        return ""
    month, day = parts
    text = f"{day} {JALALI_MONTHS[month - 1]}"
    if year:
        text += f" {int(year)}"
    return _to_persian_digits(text)


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
