from sqlalchemy.orm import Session
from models import Customer
from services._common import get_setting_int, current_year_month
from services.tier import (
    get_tier_config,
    get_tier_discount_percent,
    get_birthday_discount,
    check_birthday_eligible,
)


def calculate_discounts(
    customer: Customer,
    total_amount: int,
    db: Session,
    is_first_purchase: bool = True,
) -> dict:
    """
    Calculate all applicable discounts for a customer.
    Returns dict with discount breakdown.
    """
    config = get_tier_config(db)
    min_purchase = get_setting_int(db, "min_purchase_for_discount", 500000)
    
    discounts = {
        "referred_discount": 0,
        "referrer_discount": 0,
        "tier_discount": 0,
        "birthday_discount": 0,
        "total_discount": 0,
        "details": [],
    }
    
    # 1. Referred discount (first purchase only, must meet min purchase)
    if (is_first_purchase and 
        not customer.has_used_referred_discount and 
        customer.referred_discount > 0 and
        total_amount >= min_purchase):
        discounts["referred_discount"] = customer.referred_discount
        discounts["details"].append(
            f"تخفیف معرفی: {customer.referred_discount:,} تومان"
        )
    
    # 2. Referrer discount (accumulated from referring others)
    if customer.referrer_discount > 0:
        discounts["referrer_discount"] = customer.referrer_discount
        discounts["details"].append(
            f"تخفیف معرفی دیگران: {customer.referrer_discount:,} تومان"
        )
    
    # 3. Tier permanent discount
    tier_percent = get_tier_discount_percent(customer.tier, config)
    if tier_percent > 0:
        tier_discount = int(total_amount * tier_percent / 100)
        discounts["tier_discount"] = tier_discount
        discounts["details"].append(
            f"تخفیف {customer.tier} ({tier_percent}%): {tier_discount:,} تومان"
        )
    
    # 4. Birthday discount
    if check_birthday_eligible(customer, config):
        birthday_disc = get_birthday_discount(customer.tier, config)
        if birthday_disc > 0:
            discounts["birthday_discount"] = birthday_disc
            discounts["details"].append(
                f"تخفیف تولد فرزند: {birthday_disc:,} تومان"
            )
    
    # Calculate total discount (cannot exceed total amount)
    discounts["total_discount"] = min(
        total_amount,
        discounts["referred_discount"] +
        discounts["referrer_discount"] +
        discounts["tier_discount"] +
        discounts["birthday_discount"]
    )
    
    return discounts


def apply_discounts_after_sale(
    customer: Customer,
    discounts: dict,
    db: Session,
    referrer: Customer = None,
):
    """Apply/mark discounts as used after a successful sale."""
    
    # Mark referred discount as used
    if discounts["referred_discount"] > 0:
        customer.has_used_referred_discount = True
    
    # Reset referrer discount if used
    if discounts["referrer_discount"] > 0:
        customer.referrer_discount = 0
        customer.active_referral_count = 0
    
    # Handle referrer rewards (if customer was referred)
    if referrer and discounts["referred_discount"] > 0:
        referrer_discount_amount = get_setting_int(db, "default_referrer_discount", 50000)
        referrer.referrer_discount += referrer_discount_amount
        referrer.active_referral_count += 1

        year, month = current_year_month()
        if referrer.monthly_referral_year != year or referrer.monthly_referral_month != month:
            referrer.monthly_referral_count = 0
            referrer.monthly_referral_year = year
            referrer.monthly_referral_month = month
        referrer.monthly_referral_count += 1
