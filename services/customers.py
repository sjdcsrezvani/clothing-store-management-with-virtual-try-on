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
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from models import Customer, Referral, Sale
from services._common import (
    BIRTHDAY_SUBJECT_LABELS,
    BUYS_FOR_CHILD,
    BUYS_FOR_CHOICES,
    BUYS_FOR_LABELS,
    BUYS_FOR_SELF,
    birthday_form_value,
    birthday_display,
    birthday_subjects,
    child_profile_enabled,
    customer_birthday_subjects,
    days_until_jalali_birthday,
    default_buys_for,
    get_birthday_target,
    get_setting_int,
    is_archived_customer,
    jalali_age,
    jalali_month_start,
    jtoday,
    marketing_opt_in,
    normalise_buys_for,
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
            # A signup form asks the customer themselves: these are the two
            # options it offers and the one it pre-selects from the store's
            # target. The choice is stored per customer and decides whose
            # birthday the discount uses — the store setting is only the default
            # for someone who has never chosen.
            "default_buys_for": default_buys_for(db),
            "buys_for_choices": BUYS_FOR_CHOICES,
            "buys_for_labels": BUYS_FOR_LABELS,
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

STATUSES = ("all", "active", "inactive", "debtor", "birthday", "discount",
            "campaign", "archived")
STATUS_LABELS = {
    "all": "همه مشتریان",
    "active": "فعال (خرید ۳۰ روز اخیر)",
    "inactive": "کم‌فعال (بدون خرید ۹۰ روز)",
    "debtor": "بدهکار",
    "birthday": "تولد نزدیک",
    "discount": "تخفیف استفاده‌نشده",
    "campaign": "کمپیندار (تخفیف فعال)",
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


def effective_buys_for(db: Session, customer: Customer) -> str:
    """What this customer's profile form shows selected.

    Their own choice, or the store's default while they have never chosen — and
    never a stale 'child' in a shop that has since switched the child module off.
    """
    return normalise_buys_for(customer.buys_for, db) or default_buys_for(db)


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
    """Upcoming birthdays, each customer resolved on their own choice.

    Written as one SQL condition rather than a loop because the list, its KPI
    and the counts all filter on it in the database. Three groups: a customer
    who chose for themselves is due on their own birthday in every kind of shop;
    one who chose for a child is due on the child's while the module is on; and
    one who has never chosen follows the store's default target.
    """
    values = upcoming_month_days(days)
    store = birthday_subjects(db)
    child_on = child_profile_enabled(db)

    def due(column):
        return column.in_(values)

    conditions = [
        and_(Customer.buys_for == BUYS_FOR_SELF, due(Customer.birth_month_day)),
    ]
    if child_on:
        conditions.append(and_(Customer.buys_for == BUYS_FOR_CHILD, due(Customer.child_birthday)))
    else:
        # No child programme left: the wish falls back to the customer, exactly
        # as `customer_birthday_subjects` does for that row.
        conditions.append(and_(Customer.buys_for == BUYS_FOR_CHILD, due(Customer.birth_month_day)))

    legacy = []
    if "customer" in store:
        legacy.append(due(Customer.birth_month_day))
    if "child" in store:
        legacy.append(due(Customer.child_birthday))
    if legacy:
        conditions.append(and_(Customer.buys_for.is_(None), or_(*legacy)))
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
    elif status == "campaign":
        # «کمپیندار» means the counter would honour a campaign today: an open
        # invitation on a campaign that is active and inside its window.
        query = query.filter(
            Customer.id.in_(_live_campaign_customer_ids(db))
        )

    return query


def _live_campaign_customer_ids(db: Session) -> list[int]:
    """Customers holding a live, unspent campaign — the «کمپیندار» set."""
    from models import Campaign, CampaignAssignment

    now = datetime.now(timezone.utc)
    rows = db.query(CampaignAssignment.customer_id).join(
        Campaign, Campaign.id == CampaignAssignment.campaign_id,
    ).filter(
        CampaignAssignment.status == "invited",
        Campaign.is_active == True,  # noqa: E712
        or_(Campaign.start_date.is_(None), Campaign.start_date <= now),
        or_(Campaign.end_date.is_(None), Campaign.end_date >= now),
    ).all()
    return [row[0] for row in rows]


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
    campaign_ids = _live_campaign_customer_ids(db)
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
        "campaign_count": (
            db.query(Customer).filter(
                _not_archived(), Customer.id.in_(campaign_ids),
            ).count() if campaign_ids else 0
        ),
        # Every customer is counted on their own choice, so with the child
        # module on the list can be holding both kinds of birthday at once and
        # the heading stays neutral rather than claiming they are all children.
        "birthday_label": "تولدها" if child_profile_enabled(db) else "تولد مشتریان",
        "birthday_column_label": birthday_column_label(db),
        "child_profile": child_profile_enabled(db),
        "default_buys_for": default_buys_for(db),
    }


# ── birthdays ─────────────────────────────────────────────────────────────────


def customer_birthdays(db: Session, customer: Customer) -> list[dict]:
    """The birthday this customer is wished on, when one is on file.

    Resolved per customer rather than per store: a self-buyer shows their own
    birthday even in a children's shop, and a child-buyer shows the child's.
    """
    entries = []
    for subject in customer_birthday_subjects(db, customer):
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
    # One bulk lookup for the whole page, so a campaign badge never costs a
    # query per row.
    from services.campaigns import customer_campaign_map

    campaign_map = customer_campaign_map(db, [c.id for c in customers])
    for customer in customers:
        last = customer.last_purchase_date
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days_since = None if last is None else max(0, (now - last).days)
        archived = is_archived_customer(customer)
        campaign_entry = campaign_map.get(customer.id, {})
        rows.append({
            "customer": customer,
            "tags": parse_tags(customer.tags),
            "archived": archived,
            "birthdays": customer_birthdays(db, customer),
            "days_since_purchase": days_since,
            "campaign": campaign_entry.get("offered"),
            "campaigns_used": campaign_entry.get("used_count", 0),
            "status": ("archived" if archived else
                       "active" if days_since is not None and days_since <= ACTIVE_DAYS else
                       "inactive"),
        })
    return rows


def birthday_fields(customer: Customer, db: Session) -> dict:
    """The editable fields for the profile form (empty when unset).

    `buys_for` is the *effective* choice, so a customer who never chose shows the
    store's default selected instead of the form looking unanswered.
    """
    fields = {
        "buys_for": effective_buys_for(db, customer),
        "birth_date": birthday_form_value(customer.birth_month_day, customer.birth_year),
        "child_name": customer.child_name or "",
        "child_birthday": birthday_form_value(customer.child_birthday, customer.child_birth_year),
        "child_profile": child_profile_enabled(db),
    }
    return fields


# ── profile ───────────────────────────────────────────────────────────────────


def customer_profile(db: Session, customer: Customer) -> dict:
    """Everything the customer's own page shows, computed from source."""
    from services.campaigns import customer_campaign_history
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
        "campaign_history": customer_campaign_history(db, customer),
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


def signup_birthday_fields(db: Session, *, buys_for=None, birth_value="",
                           child_name="", child_birth_value="") -> dict:
    """The birthday columns a new customer gets, from the mode they chose.

    Shared by the registration form and the counter form so the two can't drift,
    and it is the *server's* answer: whichever fields the form happened to post,
    only the ones the chosen mode owns are stored. A self-buyer keeps no child
    details and a child-buyer keeps no customer birthday — and with the child
    module off the child option is not honoured at all.
    """
    mode = normalise_buys_for(buys_for, db) or default_buys_for(db)
    fields = {
        "buys_for": mode,
        "birth_month_day": None,
        "birth_year": None,
        "child_name": None,
        "child_birthday": None,
        "child_birth_year": None,
    }
    if mode == BUYS_FOR_SELF or not child_profile_enabled(db):
        fields["birth_month_day"], fields["birth_year"] = parse_persian_birthday_full(birth_value)
        return fields
    fields["child_name"] = (str(child_name).strip() or None)
    fields["child_birthday"], fields["child_birth_year"] = \
        parse_persian_birthday_full(child_birth_value)
    return fields


def update_customer_meta(db: Session, customer: Customer, *, notes=None, tags=None,
                         sms_opt_in=None, birth_value=None, child_name=None,
                         child_birth_value=None, buys_for=None) -> dict:
    """Save the profile card.

    The customer's «for whom» choice decides which birthday fields are written:
    a self-buyer's form only posts their own birthday and a child-buyer's only
    posts the child's, so a hand-crafted post cannot put a birthday into a
    profile that does not use it. The fields of the other side are left alone
    rather than cleared — switching back must never lose what was typed before.
    """
    if notes is not None:
        notes = str(notes).strip()
        customer.notes = notes or None

    if tags is not None:
        customer.tags = serialize_tags(tags)

    if sms_opt_in is not None:
        customer.sms_opt_in = bool(sms_opt_in)

    if buys_for is not None:
        mode = normalise_buys_for(buys_for, db)
        if mode is not None:
            customer.buys_for = mode

    if birth_value is not None or child_name is not None or child_birth_value is not None:
        if effective_buys_for(db, customer) == BUYS_FOR_SELF:
            if birth_value is not None:
                customer.birth_month_day, customer.birth_year = parse_persian_birthday_full(birth_value)
        elif child_profile_enabled(db):
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
        "buys_for": effective_buys_for(db, customer),
        "birth_month_day": customer.birth_month_day,
        "birth_year": customer.birth_year,
    }


def age_label(year: int | None, month_day: str | None) -> str:
    """«۳۵ ساله» — empty when the year was never recorded."""
    age = jalali_age(year, month_day)
    return f"{age} ساله" if age is not None else ""


def month_day_is_valid(value) -> bool:
    return parse_persian_month_day(value) is not None
