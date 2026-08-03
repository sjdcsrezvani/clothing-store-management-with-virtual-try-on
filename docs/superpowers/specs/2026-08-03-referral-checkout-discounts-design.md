# Checkout Referral &amp; Discount Improvements

Date: 2026-08-03
Status: Approved design

## Problem

The POS checkout flow (`/sales/new`) has four issues:

1. **Referral first-buy discount never applies.** When the cashier enters a
   referral code or phone at checkout, `confirm-sale` resolves the referrer and
   rewards *them*, but never sets `referred_discount` on the referred customer.
   `calculate_discounts` therefore has nothing to grant. The mobile API
   (`POST /api/customers`) does grant `referred_discount` on creation; the POS
   path is just missing that step. The user wants the discount to apply, gated
   by an admin-configurable minimum-purchase threshold.
2. **No opt-in on accumulated referral discounts.** A customer's
   `referrer_discount` balance (`active_referral_count` rewards, fixed Toman
   each) is always applied in full. The user wants the checkout to show how
   many are available and let the cashier choose to use them or not.
3. **No manual discount.** The user wants a custom discount field on checkout
   that feeds the final amount and receipt.
4. **Points floor** — already implemented and configurable (see below); needs
   a test, not a code change.

## Decisions (from clarifying questions)

- **Referral discount carry-over:** a referred customer's first-buy discount
  stays on their account until a purchase meets the threshold — it applies on
  the first *qualifying* purchase, not necessarily the literal first sale.
- **Multiple referral discounts:** show the count + a single
  **use-them checkbox** (default checked) that applies the whole balance; if
  unchecked, the balance is preserved.
- **Custom discount:** a Toman **amount** field and a **percent** field. Only
  one is applied: if amount &gt; 0 → amount; else if percent &gt; 0 → percent
  of basket. (Amount takes precedence when both happen to be filled.)
- **Points:** confirmed already correct; keep existing admin fields, add a unit
  test.

## Current behavior verified

- `calculate_points` (`services/tier.py:36`) =
  `(amount // points_per_toman) * points_per_amount` — already floors to the
  threshold (90,000→0, 100,000→10, 190,000→10, 250,000→20). Ratio is already
  admin-configurable (`tier_points_per_toman`, `tier_points_per_amount`).
- `services/discount.py:calculate_discounts` already gates the referred
  discount on `min_purchase_for_discount` (default 500,000).
- Referrer reward logic is **duplicated**: `apply_discounts_after_sale` and an
  inline block in `sales.py:confirm-sale` both add to
  `referrer.referrer_discount`. Enabling the referred discount would
  double-credit the referrer unless this is consolidated. (Note: the inline
  block currently sets `referred_discount=0` on the `Referral` row it creates.)

## Changes

### services/discount.py — `calculate_discounts`
New signature:
```python
def calculate_discounts(customer, total_amount, db,
                        is_first_purchase=True,
                        use_referrer_discount=True,
                        custom_amount=0, custom_percent=0) -> dict
```
- Referred discount eligibility: replace `is_first_purchase` term in the `if`
  with `not customer.has_used_referred_discount` (carry-over). Keep the
  `min_purchase` gate.
- Referrer discount: only apply if `use_referrer_discount` truthy.
- Custom discount: if `custom_amount &gt; 0` → `custom_amount`; elif
  `custom_percent &gt; 0` → `total_amount * custom_percent // 100`. Add detail
  line `"تخفیف ویژه: X تومان"`.
- Keep the final cap `min(total_amount, sum-of-discounts)`.

### services/discount.py — consolidate referrer reward
Move the `Referral` row creation into `apply_discounts_after_sale` (from the
inline block in `sales.py:confirm-sale`). It already marks the referred
discount used and resets the referrer balance when consumed. Guard so the
reward only runs `if referrer and not customer.referred_by`.

### routers/sales.py — `confirm-sale`
- Accept new fields: `use_referrer_discount` (checkbox,
  `"1"` if checked), `custom_discount_amount`, `custom_discount_percent`.
- Before `calculate_discounts`: if a referrer resolves **and**
  `not customer.referred_by` → set `customer.referred_by = referrer.id` and
  `customer.referred_discount = get_setting_int(db, "default_referred_discount", 30000)`.
- Remove the inline referrer-reward block (now consolidated).
- Pass the new args into `calculate_discounts`.
- `add-to-basket`, `remove-from-basket`, `_scan_step`, `sales_new`: thread the
  three new fields through hidden inputs (same pattern as
  `referrer_code`/`referrer_phone`) and pass a server-computed `discounts`
  dict for the preview.

### templates/sales/checkout.html — scan step only
- **Referral-balance card** (when `active_referral_count &gt; 0`): text
  `"🎟 X تخفیف معرفی فعال (Y تومان)"` + a checkbox `use_referrer_discount`
  default checked, wired through a hidden input on every round-trip.
- **Custom discount inputs**: amount (toman) + percent, wired through hidden
  inputs, preserved across add/remove.
- **Replace the hand-written summary math** with a loop over
  `discount/discounts.details`, plus subtotal / total-discount / final rows, so
  the on-page preview always matches the backend. The tier/referred/referrer
  rows the template currently hardcodes are replaced by this loop.

### templates/sales/invoice.html
Already renders `sale.discount_amount` and `discounts.details`; the custom
discount and the opt-in behavior surface through those. No change required
(verify visually).

### admin settings
No new settings needed. Reused:
- `min_purchase_for_discount` (existing, default 500,000)
- `default_referred_discount` (existing, default 30,000)
- `default_referrer_discount` (existing, default 50,000)
- `tier_points_per_toman` / `tier_points_per_amount` (existing)

### tests
Extend `tests/test_sales.py` (or add `tests/test_discounts.py`):
- referral discount is granted at confirm and applies / is gated by the
  minimum-purchase, and carries over when below threshold;
- referrer discount opt-out preserves the balance;
- custom discount: amount-only, percent-only, both → amount wins, capped at
  `total_amount` (final never below 0);
- `calculate_discounts` floor: 90,000→0, 100,000→10, 190,000→10, 250,000→20.

## Edge cases
- Custom discount fields empty string → treated as 0 (parse defensively).
- Checkbox unset (not in form) → `use_referrer_discount=False`.
- Custom + referral + tier + birthday discounts together → capped at total.
- `customer.referred_by` already set → don't re-grant / re-reward.
- `minimum purchase not met` for referred discount → `has_used_referred_discount`
  stays False so it can apply on a later qualifying purchase.

## Out of scope
- Mobile `/api` flow changes (already grants `referred_discount` on creation).
- Any schema/migration (no new columns needed).