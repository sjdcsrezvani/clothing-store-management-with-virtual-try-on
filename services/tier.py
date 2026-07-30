from sqlalchemy.orm import Session
from models import Customer, Settings
from datetime import datetime, timezone, timedelta
from services._common import current_year_month, get_setting_int


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


def check_birthday_eligible(customer: Customer, config: dict) -> bool:
    """Check if customer is eligible for birthday discount."""
    if not customer.child_birthday or customer.tier == "silver":
        return False
    
    today = datetime.now(timezone.utc).date()
    try:
        month, day = map(int, customer.child_birthday.split("-"))
        birthday_this_year = today.replace(month=month, day=day)
    except (ValueError, AttributeError):
        return False
    
    # If birthday has passed this year, check next year's birthday
    if birthday_this_year < today:
        try:
            birthday_next_year = today.replace(year=today.year + 1, month=month, day=day)
            days_until = (birthday_next_year - today).days
        except ValueError:
            return False
    else:
        days_until = (birthday_this_year - today).days
    
    return days_until <= config["birthday_sms_days_before"]


def check_tier_downgrade(customer: Customer, config: dict, db: Session) -> bool:
    """Check if customer should be downgraded. Returns True if downgraded."""
    if customer.tier == "silver":
        return False
    
    if config["downgrade_months"] <= 0 or config["downgrade_amount"] <= 0:
        return False
    
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
    
    return True


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
    """Get customers whose child's birthday is within N days."""
    today = datetime.now(timezone.utc).date()
    customers_with_birthday = db.query(Customer).filter(
        Customer.child_birthday.isnot(None),
        Customer.tier != "silver"
    ).all()
    
    eligible = []
    for customer in customers_with_birthday:
        try:
            month, day = map(int, customer.child_birthday.split("-"))
            birthday_this_year = today.replace(month=month, day=day)
            
            if birthday_this_year < today:
                birthday_next = today.replace(year=today.year + 1, month=month, day=day)
                days_until = (birthday_next - today).days
            else:
                days_until = (birthday_this_year - today).days
            
            if 0 < days_until <= days_before:
                eligible.append((customer, days_until))
        except (ValueError, AttributeError):
            continue
    
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
