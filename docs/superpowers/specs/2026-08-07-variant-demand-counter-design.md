# Variant Demand Counter — Design

**Date:** 2026-08-07
**Status:** Approved

## Problem

When a size/color of a product is sold out and a customer asks for that
exact size+color, there is currently no way to record that interest. The shop
reorders blind. The user wants, per variant, a counter that they can nudge by
one each time a customer wants the missing size/color, so the next purchase
order is driven by real counted demand.

The variant **is** the size+color combo (`ProductVariant.size`,
`ProductVariant.color`), so a per-variant counter captures "exact size and
colour" with no extra modeling.

## Approach

Add one `demand_count` integer column to `ProductVariant` (default 0). The
product edit page shows it in the existing variants table with a **+1** and a
**reset** button per row. Chosen over an event log: the goal is "know what to
buy next," and a plain count fully answers that; timestamps add nothing here
(YAGNI).

## Behavior

- Counter starts at 0 for every variant (existing and new).
- **+1** increments by one. Tapped when a customer requests that out-of-stock
  size/colour.
- **Reset** sets it back to 0. Tapped when the backlog is filled. The count
  is deliberately **not** auto-reset on restock — the user fills the backlog
  on their own schedule and resets manually.
- The count is shown **only** on the product edit page (user's choice; no
  products-list view, no restock-report page).
- When a variant is out of stock **and** has demand, the count renders
  visually prominent so it stands out while browsing.

## Changes

### `models.py`
Add to `ProductVariant`:
```python
demand_count = Column(Integer, default=0)
```
SQLAlchemy creates the column on next run against the existing SQLite DB; no
migration tooling needed.

### `routers/products.py`
Two POST routes, both `check_admin`-guarded, both redirect to
`/admin/products/{product_id}`:

- `POST /admin/variants/{id}/demand` — increment:
  ```python
  variant.demand_count = (variant.demand_count or 0) + 1
  ```
- `POST /admin/variants/{id}/demand/reset` — set to 0.

Guard on 404 if the variant is missing.

### `templates/admin/product_form.html`
In the edit-mode variants table (lines ~59-90), add a **«درخواست»** column
with, per row:
- the current `demand_count`,
- a ➕ **(+1)** form-button posting to `/admin/variants/{id}/demand`,
- a 🔄 **(reset)** form-button posting to `/admin/variants/{id}/demand/reset`
  (confirm on submit),
- the count shown in a warning badge when
  `variant.stock_quantity == 0 and variant.demand_count > 0`.

## Error handling
- Count never goes negative: +1 only adds, reset only sets 0.
- Missing variant → 404 (consistent with existing handlers).

## Out of scope
- No products-list surfacing, no restock-report page, no demand history log.
- The stray `ce` binary, `_repro.db`, and `referral.db` dirt in the repo are
  left untouched.

## Testing
A small unit check that +1 then reset returns to 0 (SQLAlchemy model-level),
stored under `tests/`.