"""Customer list, profile and lifecycle.

The record belongs to the customer: their own birthday, their notes, their tags.
Child details are a children's-shop module — every read or write of them goes
through `child_profile_enabled`, so a shop that sells to adults never sees the
fields and never has them stored.

Stored counters (`total_spent`, `total_purchases`, `total_points`, `tier`) are
incremented in place at checkout and decremented on refund, so they can drift.
The profile shows them beside totals computed from the sales themselves and
flags a disagreement rather than quietly trusting the counter — the same honesty
the inventory ledger uses for balances.
"""
from datetime import datetime, timedelta, timezone

import jdatetime
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from models import Customer, Referral, Sale
from services._common import (
    BIRTHDAY_SUBJECT_LABELS,
    birthday_form_value,
    birthday_display,
    birthday_subjects,
    child_profile_enabled,
    days_until_jalali_birthday,
    get_birthday_target,
    get_setting_int,
    is_archived_customer,
    jalali_age,
    jalali_month_start,
    jtoday,
    marketing_opt_in,
    parse_persian_birthday_full,
    parse_persian_month_day,
)
from services.tier import get_tier_config, get_tier_discount_percent

PER_PAGE = 25
PROFILE_SALES = 20  # purchase history rows shown on the profile

# The child-profile and birthday-target flags are read on nearly every render,
# so they are cached in memory exactly like the store profile and invalidated
# when settings are saved.
_FLAGS_TTL_SECONDS = 60
_flags_cache = {"at": 0.0, "data": None}


def _load_flags(db=None) -> dict:
    from database import SessionLocal

    close = db is None
    if db is None:
        db = SessionLocal()
    try:
        return {
            "child_profile": child_profile_enabled(db),
            "birthday_target": get_birthday_target(db),
        }
    finally:
        if close:
            db.close()


def get_customer_flags(db=None) -> dict:
    """Store-wide customer flags, cached for 60 seconds."""
    import time

    now = time.time()
    if _flags_cache["data"] is None or now - _flags_cache["at"] > _FLAGS_TTL_SECONDS:
        _flags_cache["data"] = _load_flags(db)
        _flags_cache["at"] = now
    return _flags_cache["data"]


def invalidate_customer_cache() -> None:
    _flags_cache["data"] = None
    _flags_cache["at"] = 0.0


def customer_context_processor(request) -> dict:
    """Expose the customer flags to every template without per-route plumbing."""
    return get_customer_flags()

# ── tags ──────────────────────────────────────────────────────────────────────
# A small fixed palette kept as a CSV column: a join table for nine customers
# would need its own management screen for no gain. Matches are done against the
# delimiter-wrapped value so «vip» can never match a longer label.

TAG_PALETTE = (
    ("vip", "ویژه (VIP)"),
    ("wholesale", "عمده"),
    ("followup", "پیگیری"),
    ("blocked", "بلاک"),
)
TAG_KEYS = tuple(key for key, _ in TAG_PALETTE)
TAG_LABELS = dict(TAG_PALETTE)


def parse_tags(value) -> list[str]:
    """Stored CSV → known keys, in palette order, de-duplicated."""
    raw = {part.strip().lower() for part in str(value or "").split(",") if part.strip()}
    return [key for key in TAG_KEYS if key in raw]


def serialize_tags(keys) -> str:
    """Form values → stored CSV, dropping anything outside the palette."""
    joined = ",".join(str(key) for key in (keys or []))
    return ",".join(parse_tags(joined))


def tag_label(key: str) -> str:
    return TAG_LABELS.get(key, key)


def _matches_tag(tag: str):
    """Exact-tag match inside the CSV column (bound parameter, never interpolated)."""
    return text("(',' || coalesce(customers.tags, '') || ',') LIKE :tag_like").bindparams(
        tag_like=f"%,{tag},%"
    )


# ── filters ───────────────────────────────────────────────────────────────────

STATUSES = ("all", "active", "inactive", "debtor", "birthday", "discount", "archived")
STATUS_LABELS = {
    "all": "همه مشتریان",
    "active": "فعال (خرید ۳۰ روز اخیر)",
    "inactive": "کم‌فعال (بدون خرید ۹۰ روز)",
    "debtor": "بدهکار",
    "birthday": "تولد نزدیک",
    "discount": "تخفیف استفاده‌نشده",
    "archived": "بایگانی‌شده",
}

SORTS = (
    "date", "oldest", "tier", "purchase_desc", "purchase_asc",
    "count_desc", "points", "debt", "last_purchase", "name",
)
SORT_LABELS = {
    "date": "جدیدترین",
    "oldest": "قدیمی‌ترین",
    "tier": "سطح",
    "purchase_desc": "بیشترین خرید",
    "purchase_asc": "کمترین خرید",
    "count_desc": "بیشترین تعداد خرید",
    "points": "بیشترین امتیاز",
    "debt": "بیشترین بدهی",
    "last_purchase": "بدون خرید (قدیمی‌ترین)",
    "name": "نام",
}

ACTIVE_DAYS = 30
INACTIVE_DAYS = 90


def _not_archived():
    return or_(Customer.is_archived.is_(None), Customer.is_archived == False)  # noqa: E712


def display_subjects(db: Session) -> tuple[str, ...]:
    """Birthdays the screens may show: the customer's own, plus the child's when
    the store keeps child profiles.

    Deliberately wider than `birthday_subjects`, which decides whose birthday is
    *celebrated*. A store can put a child's birthday on file without wanting it to
    drive the discount, and a birthday that is stored should never be invisible.
    """
    subjects = ["customer"]
    if child_profile_enabled(db):
        subjects.append("child")
    return tuple(subjects)


def birthday_label(subjects, *, column: bool = False) -> str:
    """A heading that reads correctly whichever birthdays this store celebrates."""
    subjects = tuple(subjects)
    if subjects == ("customer",):
        return "تولد مشتری" if column else "تولد مشتریان"
    if subjects == ("child",):
        return "تولد فرزند" if column else "تولد فرزندان"
    return "تولد" if column else "تولدها"


def birthday_column_label(db: Session) -> str:
    """The «تولد» column can hold either birthday, so it says so honestly."""
    return "تولد" if child_profile_enabled(db) else "تولد مشتری"


def birthday_window_days(db: Session) -> int:
    """How far ahead the store counts upcoming birthdays (the SMS lead time)."""
    return max(0, get_setting_int(db, "birthday_sms_days_before", 7))


def upcoming_month_days(days: int) -> list[str]:
    """The MM-DD values falling in the next `days` days, today included.

    Padded and unpadded forms are both listed so a legacy row stored as `7-10`
    still matches.
    """
    today = jtoday()
    values = []
    for offset in range(0, days + 1):
        day = today + jdatetime.timedelta(days=offset)
        values.append(f"{day.month:02d}-{day.day:02d}")
        values.append(f"{day.month}-{day.day}")
    return values


def _birthday_condition(db: Session, days: int):
    """Upcoming birthdays for whichever birthday this store celebrates."""
    subjects = birthday_subjects(db)
    values = upcoming_month_days(days)
    conditions = []
    if "customer" in subjects:
        conditions.append(Customer.birth_month_day.in_(values))
    if "child" in subjects:
        conditions.append(Customer.child_birthday.in_(values))
    if not conditions:
        return None
    return or_(*conditions)


def _filtered_query(db: Session, search: str = "", tier: str = "", status: str = "all",
                    tag: str = ""):
    query = db.query(Customer)

    # Archived customers leave the list and the counts unless asked for by name.
    if status == "archived":
        query = query.filter(Customer.is_archived == True)  # noqa: E712
    else:
        query = query.filter(_not_archived())

    search = (search or "").strip()
    if search:
        conditions = [
            Customer.phone.contains(search),
            Customer.first_name.contains(search),
            Customer.last_name.contains(search),
            Customer.referral_code.contains(search),
        ]
        # A child's name is only searchable where child profiles exist at all.
        if child_profile_enabled(db):
            conditions.append(Customer.child_name.contains(search))
        query = query.filter(or_(*conditions))

    if tier in ("silver", "gold", "diamond"):
        query = query.filter(Customer.tier == tier)

    if tag in TAG_KEYS:
        query = query.filter(_matches_tag(tag))

    now = datetime.now(timezone.utc)
    if status == "active":
        query = query.filter(Customer.last_purchase_date >= now - timedelta(days=ACTIVE_DAYS))
    elif status == "inactive":
        query = query.filter(or_(
            Customer.last_purchase_date.is_(None),
            Customer.last_purchase_date < now - timedelta(days=INACTIVE_DAYS),
        ))
    elif status == "debtor":
        query = query.filter(Customer.total_debt > 0)
    elif status == "discount":
        query = query.filter(or_(
            (Customer.referred_discount > 0) & (Customer.has_used_referred_discount == False),  # noqa: E712
            Customer.referrer_discount > 0,
        ))
    elif status == "birthday":
        condition = _birthday_condition(db, birthday_window_days(db))
        if condition is not None:
            query = query.filter(condition)

    return query


def _sorted(query, sort: str):
    """Order the list. Keys kept from the previous page so old links still work."""
    if sort == "tier":
        from sqlalchemy import case
        tier_order = case(
            (Customer.tier == "diamond", 1),
            (Customer.tier == "gold", 2),
            (Customer.tier == "silver", 3),
            else_=4,
        )
        return query.order_by(tier_order, Customer.total_points.desc())
    if sort == "name":
        return query.order_by(Customer.first_name.asc(), Customer.last_name.asc())
    if sort in ("purchase_desc", "purchase_asc"):
        direction = Customer.total_spent.desc() if sort == "purchase_desc" else Customer.total_spent.asc()
        return query.order_by(direction)
    if sort == "count_desc":
        return query.order_by(Customer.total_purchases.desc())
    if sort == "points":
        return query.order_by(Customer.total_points.desc())
    if sort == "debt":
        return query.order_by(Customer.total_debt.desc())
    if sort == "last_purchase":
        return query.order_by(Customer.last_purchase_date.asc().nullsfirst())
    if sort == "oldest":
        return query.order_by(Customer.created_at.asc())
    return query.order_by(Customer.created_at.desc())  # date (default)


def list_customers(db: Session, *, search: str = "", tier: str = "", status: str = "all",
                   tag: str = "", sort: str = "date", page: int = 1) -> dict:
    """One page of customers plus the totals the page needs to describe itself."""
    status = status if status in STATUSES else "all"
    sort = sort if sort in SORTS else "date"
    page = max(1, int(page or 1))

    query = _filtered_query(db, search=search, tier=tier, status=status, tag=tag)
    total = query.count()
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    rows = _sorted(query, sort).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()

    return {
        "customers": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": PER_PAGE,
        "search": search,
        "tier": tier,
        "status": status,
        "tag": tag,
        "sort": sort,
        "has_filters": bool(search or tier or tag or status != "all" or sort != "date"),
    }


def customer_overview(db: Session) -> dict:
    """Headline numbers for the list page, each one matching a filter on it."""
    now = datetime.now(timezone.utc)
    window = birthday_window_days(db)
    birthday_condition = _birthday_condition(db, window)

    active = db.query(Customer).filter(_not_archived())
    return {
        "total": active.count(),
        "new_this_month": db.query(Customer).filter(
            _not_archived(), Customer.created_at >= jalali_month_start(now)
        ).count(),
        "active": db.query(Customer).filter(
            _not_archived(), Customer.last_purchase_date >= now - timedelta(days=ACTIVE_DAYS)
        ).count(),
        "debtor_count": db.query(Customer).filter(_not_archived(), Customer.total_debt > 0).count(),
        "total_debt": int(db.query(func.coalesce(func.sum(Customer.total_debt), 0))
                          .filter(_not_archived()).scalar() or 0),
        "birthday_window": window,
        "birthday_count": (
            db.query(Customer).filter(_not_archived(), birthday_condition).count()
            if birthday_condition is not None else 0
        ),
        "archived_count": db.query(Customer).filter(Customer.is_archived == True).count(),  # noqa: E712
        "birthday_subjects": birthday_subjects(db),
        "birthday_label": birthday_label(birthday_subjects(db)),
        "birthday_column_label": birthday_column_label(db),
        "child_profile": child_profile_enabled(db),
    }


# ── birthdays ─────────────────────────────────────────────────────────────────


def customer_birthdays(db: Session, customer: Customer) -> list[dict]:
    """The birthdays on file for this customer, in celebration priority order.

    Only the subjects the store actually celebrates are returned, so an adult
    clothing shop sees one row and a children's shop sees two.
    """
    entries = []
    for subject in display_subjects(db):
        if subject == "customer":
            month_day, year = customer.birth_month_day, customer.birth_year
        else:
            month_day, year = customer.child_birthday, customer.child_birth_year
        if not month_day:
            continue
        entries.append({
            "subject": subject,
            "label": BIRTHDAY_SUBJECT_LABELS.get(subject, ""),
            "month_day": month_day,
            "year": year,
            "text": birthday_display(month_day, year),
            "age": jalali_age(year, month_day),
            "days_until": days_until_jalali_birthday(month_day),
        })
    return entries


def build_customer_rows(db: Session, customers: list) -> list[dict]:
    """List rows: the customer plus the handful of derived bits the table shows.

    Built here rather than in the template so the status rule and the staleness
    maths are testable on their own.
    """
    now = datetime.now(timezone.utc)
    rows = []
    for customer in customers:
        last = customer.last_purchase_date
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days_since = None if last is None else max(0, (now - last).days)
        archived = is_archived_customer(customer)
        rows.append({
            "customer": customer,
            "tags": parse_tags(customer.tags),
            "archived": archived,
            "birthdays": customer_birthdays(db, customer),
            "days_since_purchase": days_since,
            "status": ("archived" if archived else
                       "active" if days_since is not None and days_since <= ACTIVE_DAYS else
                       "inactive"),
        })
    return rows


def birthday_fields(customer: Customer, db: Session) -> dict:
    """The editable birthday fields for the profile form (empty when unset)."""
    fields = {
        "birth_date": birthday_form_value(customer.birth_month_day, customer.birth_year),
        "child_name": customer.child_name or "",
        "child_birth_date": birthday_form_value(customer.child_birthday, customer.child_birth_year),
        "child_profile": child_profile_enabled(db),
    }
    return fields


# ── profile ───────────────────────────────────────────────────────────────────


def customer_profile(db: Session, customer: Customer) -> dict:
    """Everything the customer's own page shows, computed from source."""
    sales_query = db.query(Sale).filter(Sale.customer_id == customer.id)

    counted_sales = sales_query.filter(
        Sale.payment_confirmed == True,  # noqa: E712
        Sale.is_refunded == False,  # noqa: E712
    )
    computed_row = counted_sales.with_entities(
        func.coalesce(func.sum(Sale.final_amount), 0),
        func.count(Sale.id),
    ).one()
    computed_spent = int(computed_row[0] or 0)
    computed_count = int(computed_row[1] or 0)

    stored_spent = int(customer.total_spent or 0)
    stored_count = int(customer.total_purchases or 0)

    unpaid = db.query(Sale).filter(
        Sale.customer_id == customer.id,
        Sale.payment_method == "credit",
        Sale.credit_settled == False,  # noqa: E712
        Sale.is_refunded == False,  # noqa: E712
    ).order_by(Sale.created_at.asc()).all()

    referrals = db.query(Referral).filter(Referral.referrer_id == customer.id) \
        .order_by(Referral.created_at.desc()).all()

    tier_config = get_tier_config(db)
    return {
        "sales": sales_query.order_by(Sale.created_at.desc()).limit(PROFILE_SALES).all(),
        "sale_count": sales_query.count(),
        "history_limit": PROFILE_SALES,
        "computed_spent": computed_spent,
        "computed_count": computed_count,
        "stored_spent": stored_spent,
        "stored_count": stored_count,
        # A counter that disagrees with the sales is shown, not silently fixed.
        "spent_mismatch": stored_spent != computed_spent,
        "count_mismatch": stored_count != computed_count,
        "unpaid_credit": unpaid,
        "unpaid_credit_total": sum(int(sale.final_amount or 0) for sale in unpaid),
        "referrals": referrals,
        "referred_by": customer.referrer,
        "tier_percent": get_tier_discount_percent(customer.tier, tier_config),
        "tags": parse_tags(customer.tags),
        "birthdays": customer_birthdays(db, customer),
        "child_profile": child_profile_enabled(db),
    }


# ── lifecycle ─────────────────────────────────────────────────────────────────


def customer_sale_count(db: Session, customer_id: int) -> int:
    return db.query(Sale).filter(Sale.customer_id == customer_id).count()


def can_delete_customer(db: Session, customer: Customer) -> tuple[bool, int]:
    """A customer with recorded sales is archived, never deleted.

    Deleting one nulls `Sale.customer_id`, which silently detaches the purchase
    history the reports are built on.
    """
    sales = customer_sale_count(db, customer.id)
    return (sales == 0, sales)


def delete_customer(db: Session, customer: Customer) -> None:
    """Remove a customer who has no sales, along with their referral links."""
    db.query(Referral).filter(
        (Referral.referrer_id == customer.id) | (Referral.referred_id == customer.id)
    ).delete()
    db.delete(customer)


def archive_customer(db: Session, customer: Customer, archived: bool = True) -> bool:
    """Hide a customer from the list, the counts and the marketing sends."""
    customer.is_archived = bool(archived)
    return customer.is_archived


def update_customer_meta(db: Session, customer: Customer, *, notes=None, tags=None,
                         sms_opt_in=None, birth_value=None, child_name=None,
                         child_birth_value=None) -> dict:
    """Save the profile card.

    Child fields are only written when the store has child profiles enabled, so
    a hand-crafted post can't put child data into an adult clothing shop.
    """
    if notes is not None:
        notes = str(notes).strip()
        customer.notes = notes or None

    if tags is not None:
        customer.tags = serialize_tags(tags)

    if sms_opt_in is not None:
        customer.sms_opt_in = bool(sms_opt_in)

    if birth_value is not None:
        month_day, year = parse_persian_birthday_full(birth_value)
        customer.birth_month_day = month_day
        customer.birth_year = year

    if child_profile_enabled(db):
        if child_name is not None:
            child_name = str(child_name).strip()
            customer.child_name = child_name or None
        if child_birth_value is not None:
            month_day, year = parse_persian_birthday_full(child_birth_value)
            customer.child_birthday = month_day
            customer.child_birth_year = year
            if not month_day and not year:
                # A cleared field clears both halves; otherwise MM-DD and the
                # year would disagree about whether a birthday is on file.
                customer.child_birthday = None
                customer.child_birth_year = None
        if not customer.child_name and customer.child_birthday is None:
            customer.child_birth_year = None

    return {
        "tags": parse_tags(customer.tags),
        "sms_opt_in": marketing_opt_in(customer),
        "birth_month_day": customer.birth_month_day,
        "birth_year": customer.birth_year,
    }


def age_label(year: int | None, month_day: str | None) -> str:
    """«۳۵ ساله» — empty when the year was never recorded."""
    age = jalali_age(year, month_day)
    return f"{age} ساله" if age is not None else ""


def month_day_is_valid(value) -> bool:
    return parse_persian_month_day(value) is not None
