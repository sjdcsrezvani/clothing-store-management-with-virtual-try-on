from sqlalchemy.orm import Session
from models import Customer, Referral
from services._common import get_setting_int, current_year_month
from services.tier import (
    get_tier_config,
    get_tier_discount_percent,
    get_birthday_discount,
    check_birthday_eligible,
)


def calculate_discounts(
    customer: Customer = None,
    total_amount: int = 0,
    db: Session = None,
    use_referrer_discount: bool = True,
    custom_amount: int = 0,
    custom_percent: int = 0,
) -> dict:
    """
    Calculate all applicable discounts for a customer.
    Anonymous sales (customer=None) get only the manual custom discount —
    no referral, tier, or birthday loyalty perks.
    """
    config = get_tier_config(db)
    min_purchase = get_setting_int(db, "min_purchase_for_discount", 500000)

    # Sanitize the owner-entered manual discount: never negative, never > 100%.
    custom_amount = max(0, custom_amount or 0)
    custom_percent = min(max(0, custom_percent or 0), 100)

    discounts = {
        "referred_discount": 0,
        "referrer_discount": 0,
        "tier_discount": 0,
        "birthday_discount": 0,
        "custom_discount": 0,
        "total_amount": total_amount,
        "total_discount": 0,
        "details": [],
    }

    if customer:
        # 1. Referred discount (first qualifying purchase, must meet min purchase).
        #    Guard is `has_used_referred_discount`, NOT the count: carries over
        #    until a purchase meets the threshold.
        if (not customer.has_used_referred_discount and
            customer.referred_discount > 0 and
            total_amount >= min_purchase):
            discounts["referred_discount"] = customer.referred_discount
            discounts["details"].append(
                f"تخفیف معرفی: {customer.referred_discount:,} تومان"
            )

        # 2. Referrer discount (accumulated from referring others) — opt-in.
        if use_referrer_discount and customer.referrer_discount > 0:
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

    # 5. Custom discount — amount wins; else percent of total.
    if custom_amount > 0:
        discounts["custom_discount"] = custom_amount
        discounts["details"].append(
            f"تخفیف ویژه: {custom_amount:,} تومان"
        )
    elif custom_percent > 0:
        custom_disc = int(total_amount * custom_percent / 100)
        discounts["custom_discount"] = custom_disc
        discounts["details"].append(
            f"تخفیف ویژه ({custom_percent}٪): {custom_disc:,} تومان"
        )

    # Total discount cannot exceed total amount.
    discounts["total_discount"] = min(
        total_amount,
        discounts["referred_discount"] +
        discounts["referrer_discount"] +
        discounts["tier_discount"] +
        discounts["birthday_discount"] +
        discounts["custom_discount"],
    )

    return discounts


def apply_discounts_after_sale(
    customer: Customer,
    discounts: dict,
    db: Session,
    referrer: Customer = None,
):
    """Apply/mark discounts as used and settle the referral after a successful sale."""

    # Mark referred discount as used
    if discounts["referred_discount"] > 0:
        customer.has_used_referred_discount = True

    # Reset referrer discount if used
    if discounts["referrer_discount"] > 0:
        customer.referrer_discount = 0
        customer.active_referral_count = 0

    # Establish a first-time referral and reward the referrer exactly once.
    # Only counts when the referred discount actually applied (purchase met the
    # min_purchase_for_discount threshold).
    # Self-referral (referrer == customer) never rewards or creates a row.
    if (referrer and not customer.referred_by and referrer.id != customer.id
            and discounts["referred_discount"] > 0):
        customer.referred_by = referrer.id
        referrer_discount = get_setting_int(db, "default_referrer_discount", 50000)
        referrer.referrer_discount += referrer_discount
        referrer.active_referral_count += 1

        year, month = current_year_month()
        if referrer.monthly_referral_year != year or referrer.monthly_referral_month != month:
            referrer.monthly_referral_count = 0
            referrer.monthly_referral_year = year
            referrer.monthly_referral_month = month
        referrer.monthly_referral_count += 1

        db.add(Referral(
            referrer_id=referrer.id,
            referred_id=customer.id,
            referrer_discount=referrer_discount,
            referred_discount=customer.referred_discount,
        ))
