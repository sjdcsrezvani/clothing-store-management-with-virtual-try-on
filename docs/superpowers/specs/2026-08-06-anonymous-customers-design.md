# Anonymous (Walk-in) Sales — Design

**Date:** 2026-08-06
**Status:** Approved

## Problem

Walk-in sales (no phone / no customer info) currently create a real
`customers` row with `phone='johndoe(unknown)'`, `first_name='johndoe'`,
`last_name='(unknown)'`. This row accumulates loyalty stats (points, tier,
spend), shows up in the customer list / top customers / top spenders, and
prints a bogus name on receipts. The user wants anonymous sales to have
**no** customer identity.

## Approach

Represent anonymous sales as `Sale.customer_id = NULL`. Drop the fake
`johndoe(unknown)` row entirely. Thread a sentinel `customer_id = 0` through
the checkout forms; `0` / `None` means "anonymous" in every handler.

`Sale.customer_id` is already nullable. The sales-list template, invoice
template, and invoice text/PDF already guard on `if customer`, so a null
customer renders anonymity with no further changes to those.

## Behavior for anonymous sales

- No `Customer` row → absent from customer list, dashboard top customers,
  and top spenders automatically.
- No points, no tier changes, no referral row, no SMS.
- Manual "تخفیف ویژه" (custom discount) still allowed (till-level override).
- Sale still counts toward revenue and product-sales analytics (those query
  `Sale`/`SaleItem`, not `Customer`).
- Receipt (HTML, PDF, text) shows items + totals only; name/phone/tier skipped.

## Changes

### `routers/sales.py`
- Remove `UNKNOWN_CUSTOMER_PHONE` and the johndoe creation block in
  `skip-customer`. It now returns the scan step with `customer=None`,
  `customer_id=0`.
- `_render_scan(...)`: run `_grant_referred_discount` only `if customer`;
  expose `customer_id` to the template.
- `add-to-basket`, `remove-from-basket`, `apply-discount`, `confirm-sale`:
  accept `customer_id: int = Form(0)`; resolve to `None` when `0`.
- `confirm-sale`: `Sale(customer_id=customer.id if customer else None)`;
  call `update_customer_after_purchase` + `apply_discounts_after_sale` only
  `if customer`; otherwise `points_earned = 0`. Stock decrement and sale-item
  inserts always run (product/receipt/analysis preserved).
- Refund already guards on `sale.customer_id` — no change for anonymous.

### `services/discount.py`
- `calculate_discounts(customer=None, ...)`: wrap the referred, referrer,
  tier, and birthday discount blocks in `if customer`. Only the custom
  discount applies to anonymous.

### `templates/sales/checkout.html`
- Guard the customer-info card (render "مشتری ناشناس"), title, referral
  section, and active-referral block behind `{% if customer %}`.
- Replace all `value="{{ customer.id }}"` hidden fields with
  `value="{{ customer_id }}"`.

### `templates/admin/sales.html`
- Relabel the null-customer cell from `بدون مشتری` to `مشتری ناشناس`.

### `services/invoice.py` + `templates/sales/invoice.html`
- No change; already guard name/phone/tier behind `if customer`.

### `main.py`
- Add an idempotent one-time migration next to `_apply_missing_columns`:
  re-point sales attached to the `johndoe(unknown)` customer to
  `customer_id = NULL`, then delete that customer row. Preserves receipt
  history while dropping the name.

## Notes

- No schema change needed.
- Receipt-points banner already guarded by `{% if points_earned %}` (0 for
  anonymous), so it won't render.