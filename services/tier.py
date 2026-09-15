from sqlalchemy.orm import Session
from models import Customer, Settings
from datetime import datetime, timezone, timedelta
import jdatetime
from services._common import (
    _to_persian_digits as to_persian_digits,
    customer_birthday_subjects,
    current_year_month,
    days_until_jalali_birthday,
    get_setting_int,
    is_archived_customer,
    jtoday,
    marketing_opt_in,
)

# The shop's word for each level, and its floor-to-ceiling order. This module
# owns the vocabulary — the SMS and campaign modules import it from here rather
# than each keeping a copy that can drift.
TIER_LABELS = {"silver": "نقره‌ای", "gold": "طلایی", "diamond": "الماس"}
TIER_RANK = {"silver": 0, "gold": 1, "diamond": 2}

# What one downgrade does: a level down, never below silver.
DOWNGRADE_STEP = {"diamond": "gold", "gold": "silver"}


def get_setting(db: Session, key: str, default) -> str:
    """Get a setting value from database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if setting and setting.value:
        return setting.value
    return str(default)


def get_tier_config(db: Session) -> dict:
    """Get all tier configuration from database."""
    return {
        "points_per_amount": get_setting_int(db, "tier_points_per_amount", 10),
        "points_per_toman": get_setting_int(db, "tier_points_per_toman", 100000),
        "gold_threshold": get_setting_int(db, "tier_gold_threshold", 2000),
        "gold_discount_percent": get_setting_int(db, "tier_gold_discount_percent", 5),
        "gold_birthday_discount": get_setting_int(db, "tier_gold_birthday_discount", 50000),
        "diamond_threshold": get_setting_int(db, "tier_diamond_threshold", 5000),
        "diamond_discount_percent": get_setting_int(db, "tier_diamond_discount_percent", 10),
        "diamond_birthday_discount": get_setting_int(db, "tier_diamond_birthday_discount", 50000),
        # The downgrade rule reads only this. The old «حداقل مبلغ خرید» setting
        # is no longer read by anything: it compared a *lifetime* figure while
        # claiming to measure the period, which is a different rule from the one
        # the shop was told it was choosing.
        "downgrade_months": get_setting_int(db, "tier_downgrade_months", 6),
        "birthday_sms_days_before": get_setting_int(db, "birthday_sms_days_before", 7),
    }


def calculate_points(amount: int, config: dict) -> int:
    """Calculate points earned from a purchase amount."""
    if config["points_per_toman"] <= 0:
        return 0
    return (amount // config["points_per_toman"]) * config["points_per_amount"]


def check_tier_upgrade(customer: Customer, config: dict) -> str:
    """Check if customer should be upgraded to a new tier."""
    if customer.total_points >= config["diamond_threshold"]:
        return "diamond"
    elif customer.total_points >= config["gold_threshold"]:
        return "gold"
    return "silver"


def get_tier_discount_percent(tier: str, config: dict) -> int:
    """Get the permanent discount percentage for a tier."""
    if tier == "diamond":
        return config["diamond_discount_percent"]
    elif tier == "gold":
        return config["gold_discount_percent"]
    return 0


def get_birthday_discount(tier: str, config: dict) -> int:
    """Get the birthday discount amount for a tier."""
    if tier in ("diamond", "gold"):
        return config["gold_birthday_discount"]  # same for both tiers
    return 0


def birthday_on_file(customer: Customer, subject: str) -> str | None:
    """The stored MM-DD for one kind of birthday."""
    if subject == "customer":
        return customer.birth_month_day
    return customer.child_birthday


def birthday_occasion_due(customer: Customer, config: dict,
                          subjects=("child",), today: jdatetime.date | None = None) -> str | None:
    """Which birthday falls inside the store's notice window, if any.

    Returns "customer", "child" or None. The caller needs to know *whose* day
    it is: the sale line and the SMS both name it, and congratulating a parent in
    their child's name (or the reverse) is exactly what this avoids.
    """
    if customer.tier == "silver":
        return None  # birthday perks are a Gold/Diamond benefit
    today = today or jtoday()
    window = config["birthday_sms_days_before"]
    for subject in subjects:
        month_day = birthday_on_file(customer, subject)
        days_until = days_until_jalali_birthday(month_day, today)
        if days_until is not None and days_until <= window:
            return subject
    return None


def check_birthday_eligible(customer: Customer, config: dict,
                            subjects=("child",)) -> bool:
    """Birthday-discount eligibility. The default keeps the pre-setting behaviour."""
    return birthday_occasion_due(customer, config, subjects) is not None


# ── کاهش سطح (tier downgrade) ────────────────────────────────────────────────
# A customer keeps their tier while they keep buying. The rule is one sentence
# wide — «no purchase for N months» — because that is the only shape an owner
# can hold in their head, and because the page that applies it has to be able to
# tell every person on the list exactly why they are there.
#
# Nothing applies this rule on its own. There is no nightly sweep: a customer is
# demoted only when the owner opens «کاهش سطح», reads the names and confirms.
# That is what makes it acceptable for the rule to be this blunt.


def tier_downgrade_rule(db: Session) -> dict:
    """The window, and whether the rule is in use at all.

    The window *is* the switch: ``۰`` months means «do not offer me anybody».
    One place answers this, so the review page, the settings field and the
    dashboard card cannot disagree about whether the shop uses it.
    """
    months = get_tier_config(db)["downgrade_months"]
    return {"months": months, "enabled": months > 0,
            "months_label": to_persian_digits(str(months))}


def _purchase_age(customer: Customer, now: datetime) -> int | None:
    """Days since the last purchase, or ``None`` if there never was one."""
    stamp = customer.last_purchase_date
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0, (now - stamp).days)


def downgrade_candidates(db: Session, *, now: datetime | None = None) -> dict:
    """Everyone the rule would demote, each with the reason, in one query.

    Returns ``{"rows": [...], "skipped_archived": N, "months": N,
    "enabled": bool}``, where a row carries the customer, the level they are on,
    the level they would fall to, when they last bought, how long ago that was,
    what they have spent in total, and a sentence explaining why they qualify.

    The reason travels *with* the row rather than being reconstructed by the
    page, so the words the owner reads and the test that decided to include them
    cannot come apart. Archived customers are counted and left out: demoting
    somebody the shop has already filed away is noise, and hiding them would
    make the counts look wrong.
    """
    now = now or datetime.now(timezone.utc)
    rule = tier_downgrade_rule(db)
    if not rule["enabled"]:
        return {"rows": [], "skipped_archived": 0, **rule}

    window_days = rule["months"] * 30
    customers = (
        db.query(Customer)
        .filter(Customer.tier.in_(list(DOWNGRADE_STEP)))
        .order_by(Customer.id.asc())
        .all()
    )

    rows = []
    skipped_archived = 0
    for customer in customers:
        days = _purchase_age(customer, now)
        # A recent purchase protects the tier on its own — full stop. The old
        # rule made that protection conditional on lifetime spend, which is how
        # a customer who bought yesterday could still be demoted.
        if days is not None and days <= window_days:
            continue
        if is_archived_customer(customer):
            skipped_archived += 1
            continue
        rows.append({
            "customer": customer,
            "from_tier": customer.tier,
            "from_label": TIER_LABELS.get(customer.tier, customer.tier),
            "to_tier": DOWNGRADE_STEP[customer.tier],
            "to_label": TIER_LABELS.get(DOWNGRADE_STEP[customer.tier], ""),
            "last_purchase": customer.last_purchase_date,
            "days_since": days,
            "days_label": "—" if days is None else to_persian_digits(f"{days} روز"),
            "spent": customer.total_spent or 0,
            "never_bought": days is None,
            "reason": ("هیچ خریدی برایش ثبت نشده" if days is None
                       else f"بیش از {rule['months_label']} ماه است خرید نکرده"),
        })

    # Whoever has been quiet longest comes first, and somebody who never bought
    # is at the top of that list rather than hidden at the bottom of it.
    rows.sort(key=lambda row: (row["days_since"] is not None, -(row["days_since"] or 0)))
    return {"rows": rows, "skipped_archived": skipped_archived, **rule}


def apply_tier_downgrades(db: Session, customer_ids) -> dict:
    """Demote exactly the customers ticked, re-checked against the rule now.

    Nothing is trusted from the form except the ids. A page can sit open while
    the shop keeps trading, so somebody on the list may have bought in between —
    and that purchase is precisely what the rule says protects their tier. An id
    that no longer qualifies is refused with a reason rather than demoted anyway.
    """
    wanted = {int(value) for value in customer_ids}
    candidates = {row["customer"].id: row
                  for row in downgrade_candidates(db)["rows"]}

    demoted = []
    refused = 0
    for customer_id in sorted(wanted):
        row = candidates.get(customer_id)
        if row is None:
            refused += 1
            continue
        customer = row["customer"]
        customer.tier = row["to_tier"]
        # Clear the tier-up marker so climbing back into this tier queues them
        # for a fresh welcome, exactly as the old sweep did.
        marker = db.query(Settings).filter(
            Settings.key == tier_up_marker_key(customer.id)).first()
        if marker:
            db.delete(marker)
        demoted.append({"customer": customer, "to_tier": row["to_tier"],
                        "to_label": row["to_label"]})

    db.commit()
    return {"demoted": demoted, "demoted_count": len(demoted), "refused": refused}


# ── Tier-up SMS tracking ──
# A customer appears in the tier-up list while their current tier ranks higher
# than the tier the last tier-up SMS was sent for. Downgrades clear the marker,
# so a customer who is downgraded and then climbs back up is queued again.


def tier_up_marker_key(customer_id: int) -> str:
    return f"tier_up_sms_{customer_id}"


def tier_up_sent_rank(db: Session, customer: Customer) -> int:
    marker = db.query(Settings).filter(Settings.key == tier_up_marker_key(customer.id)).first()
    return TIER_RANK.get(marker.value if marker else None, 0)


def tier_up_candidates(db: Session) -> list:
    """Gold/Diamond customers who haven't had a tier-up SMS for their current tier."""
    return [
        c for c in db.query(Customer).all()
        if TIER_RANK.get(c.tier, 0) > tier_up_sent_rank(db, c)
    ]


def update_customer_after_purchase(customer: Customer, amount: int, db: Session):
    """Update customer points, tier, and purchase stats after a successful sale."""
    config = get_tier_config(db)

    # Add points
    points_earned = calculate_points(amount, config)
    customer.total_points += points_earned
    customer.total_purchases += 1
    customer.total_spent += amount
    customer.last_purchase_date = datetime.now(timezone.utc)

    # Check for tier upgrade
    new_tier = check_tier_upgrade(customer, config)
    if new_tier != customer.tier:
        customer.tier = new_tier

    return points_earned


def get_customers_for_birthday_check(db: Session, days_before: int = 3) -> dict:
    """Who should be wished in the next N days, and who was skipped.

    Returns ``{"eligible": [(customer, days_until, occasion), ...], "blocked": N}``.
    "eligible" holds one entry per customer — a parent and child whose birthdays
    fall in the same window get one message, the customer's own taking priority —
    and "blocked" counts birthdays that were due but withheld because the
    customer opted out of marketing SMS or is archived.
    """
    today = jtoday()
    eligible = []
    blocked = 0

    for customer in db.query(Customer).filter(Customer.tier != "silver").all():
        # Per customer: the wish follows their own «for whom» choice, not the
        # store's default, so one shop can wish some parents about a child and
        # other customers about themselves.
        subjects = customer_birthday_subjects(db, customer)
        best = None
        for rank, subject in enumerate(subjects):
            days_until = days_until_jalali_birthday(birthday_on_file(customer, subject), today)
            if days_until is None or not (0 < days_until <= days_before):
                continue
            key = (days_until, rank)
            if best is None or key < best[0]:
                best = (key, subject, days_until)
        if best is None:
            continue
        if is_archived_customer(customer) or not marketing_opt_in(customer):
            blocked += 1
            continue
        eligible.append((customer, best[2], best[1]))

    eligible.sort(key=lambda row: row[1])
    return {"eligible": eligible, "blocked": blocked}
