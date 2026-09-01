from sqlalchemy.orm import Session
from models import Customer, Settings
from datetime import datetime, timezone, timedelta
import jdatetime
from services._common import current_year_month, get_setting_int, jtoday


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
        "downgrade_amount": get_setting_int(db, "tier_downgrade_amount", 0),
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


def _days_until_jalali_birthday(child_birthday: str, today: jdatetime.date) -> int:
    """Days from today (Persian) to the next occurrence of (Persian) birthday."""
    # child_birthday stored as "MM-DD" (Persian MM-DD).
    m, d = (int(x) for x in child_birthday.split("-"))
    this_year_bday = jdatetime.date(today.year, m, d)
    if this_year_bday < today:
        # Next year's. jdatetime handles leap-year rollover for Persian months.
        try:
            next_year_bday = jdatetime.date(today.year + 1, m, d)
        except ValueError:
            # 30-Farvardin in some leap-year Persian years; bump by 1 day.
            next_year_bday = jdatetime.date(today.year + 1, m, d - 1)
        return (next_year_bday - today).days
    return (this_year_bday - today).days


def check_birthday_eligible(customer: Customer, config: dict) -> bool:
    """Check if customer is eligible for birthday discount (days are Persian)."""
    if not customer.child_birthday or customer.tier == "silver":
        return False
    try:
        days_until = _days_until_jalali_birthday(customer.child_birthday, jtoday())
    except (ValueError, AttributeError):
        return False
    return days_until <= config["birthday_sms_days_before"]


def check_tier_downgrade(customer: Customer, config: dict, db: Session) -> bool:
    """Check if customer should be downgraded. Returns True if downgraded."""
    if customer.tier == "silver":
        return False

    if config["downgrade_months"] <= 0 or config["downgrade_amount"] <= 0:
        return False

    # Downgrade is "no purchases for N months" — keep this on Gregorian
    # because last_purchase_date is stored as Gregorian timestamp; the
    # user-facing display goes through jalali_str() at the template.
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=config["downgrade_months"] * 30)

    # Check if customer has made enough purchases in the time period
    if customer.last_purchase_date and customer.last_purchase_date >= cutoff_date:
        if customer.total_spent >= config["downgrade_amount"]:
            return False

    # Downgrade one tier
    if customer.tier == "diamond":
        customer.tier = "gold"
    elif customer.tier == "gold":
        customer.tier = "silver"

    # Clear tier-up SMS marker so climbing back into this tier re-queues them.
    marker = db.query(Settings).filter(Settings.key == tier_up_marker_key(customer.id)).first()
    if marker:
        db.delete(marker)

    return True


# ── Tier-up SMS tracking ──
# A customer appears in the tier-up list while their current tier ranks higher
# than the tier the last tier-up SMS was sent for. Downgrades clear the marker,
# so a customer who is downgraded and then climbs back up is queued again.
TIER_RANK = {"silver": 0, "gold": 1, "diamond": 2}


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


def get_customers_for_birthday_check(db: Session, days_before: int = 3) -> list:
    """Get customers whose child's Persian birthday is within N days."""
    today = jtoday()
    customers_with_birthday = db.query(Customer).filter(
        Customer.child_birthday.isnot(None),
        Customer.tier != "silver"
    ).all()

    eligible = []
    for customer in customers_with_birthday:
        try:
            days_until = _days_until_jalali_birthday(customer.child_birthday, today)
        except (ValueError, AttributeError):
            continue
        if 0 < days_until <= days_before:
            eligible.append((customer, days_until))

    return eligible


def get_customers_for_downgrade_check(db: Session) -> list:
    """Get Gold/Diamond customers who might need downgrading."""
    config = get_tier_config(db)

    if config["downgrade_months"] <= 0 or config["downgrade_amount"] <= 0:
        return []

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=config["downgrade_months"] * 30)

    customers = db.query(Customer).filter(
        Customer.tier.in_(["gold", "diamond"]),
        Customer.last_purchase_date < cutoff_date
    ).all()

    return customers
