from sqlalchemy.orm import Session
from models import Customer, Referral
from services._common import (
    BIRTHDAY_DISCOUNT_LABELS,
    customer_birthday_subjects,
    current_year_month,
    get_setting_int,
)
from services.tier import (
    get_tier_config,
    get_tier_discount_percent,
    get_birthday_discount,
    birthday_occasion_due,
)

# The manual-discount clamp, named once: the till's مبلغ/درصد fields render
# their min/max from this (routers/sales.py passes it to the template) and
# the sanitize below enforces it — the form can never promise a bound the
# clamp does not keep.
CUSTOM_DISCOUNT_RULES = {
    "amount_min": 0,
    "percent_min": 0,
    "percent_max": 100,
}


def calculate_discounts(
    customer: Customer = None,
    total_amount: int = 0,
    db: Session = None,
    use_referrer_discount: bool = True,
    custom_amount: int = 0,
    custom_percent: int = 0,
    campaign=None,
) -> dict:
    """
    Calculate all applicable discounts for a customer.
    Anonymous sales (customer=None) get only the manual custom discount —
    no referral, tier, or birthday loyalty perks.

    ``campaign`` is a resolved :class:`models.Campaign` — either the one the
    customer holds or the one whose code was typed at the counter. It stacks
    with the loyalty discounts (the total stays capped at the basket) and, like
    every other discount, it is refused on نسیه by the checkout layer.
    """
    config = get_tier_config(db)
    min_purchase = get_setting_int(db, "min_purchase_for_discount", 500000)

    # Sanitize the owner-entered manual discount: never negative, never > 100%.
    # The till's discount fields paint these same bounds from CUSTOM_DISCOUNT_RULES
    # (routers/sales.py), so the input's promise and the clamp are one definition.
    custom_amount = max(CUSTOM_DISCOUNT_RULES["amount_min"], custom_amount or 0)
    custom_percent = min(max(CUSTOM_DISCOUNT_RULES["percent_min"], custom_percent or 0),
                         CUSTOM_DISCOUNT_RULES["percent_max"])

    discounts = {
        "referred_discount": 0,
        "referrer_discount": 0,
        "tier_discount": 0,
        "birthday_discount": 0,
        "campaign_discount": 0,
        "campaign": campaign,
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

        # 4. Birthday discount. Whose birthday counts is the *customer's* own
        #    choice — someone buying for themselves is wished on their own
        #    birthday even in a children's shop, and someone buying for a child
        #    on the child's. The line names the occasion rather than assuming
        #    either, and a customer who never chose follows the store's target.
        occasion = (birthday_occasion_due(
                        customer, config, customer_birthday_subjects(db, customer))
                    if db is not None else None)
        if occasion:
            birthday_disc = get_birthday_discount(customer.tier, config)
            if birthday_disc > 0:
                discounts["birthday_discount"] = birthday_disc
                discounts["details"].append(
                    f"{BIRTHDAY_DISCOUNT_LABELS.get(occasion, 'تخفیف تولد')}: {birthday_disc:,} تومان"
                )

    # 4b. Campaign discount — the code the cashier typed, or the campaign this
    #     customer already holds. Read from the campaign row, never from the
    #     client, and only while the campaign is live.
    if campaign is not None:
        from services.campaigns import (
            campaign_discount_amount,
            campaign_discount_line,
            campaign_is_live,
        )

        campaign_amount = 0
        if campaign_is_live(campaign):
            campaign_amount = campaign_discount_amount(campaign, total_amount)
        if campaign_amount > 0:
            discounts["campaign_discount"] = campaign_amount
            discounts["details"].append(
                campaign_discount_line(campaign, campaign_amount)
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
        discounts["campaign_discount"] +
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
