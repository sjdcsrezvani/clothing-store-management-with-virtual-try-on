# Checkout Referral & Discount Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the POS checkout so a referred customer's first-buy discount actually applies (gated by a configurable minimum purchase, carrying over until it qualifies), add an opt-in checkbox for the accumulated referral-discount balance, add a manual custom-discount field (Toman amount + percent), and lock in the points floor behavior with tests.

**Architecture:** Extend the existing `services/discount.py` `calculate_discounts` / `apply_discounts_after_sale` rather than restructure. `confirm-sale` grants `referred_discount` to a newly-referred customer before calculating, then all discount types (referral first-buy, accumulated-opt-in, tier, birthday, custom) collapse into one capped total. The checkout preview renders the same server-computed breakdown instead of the current hand-written template math. Points floor-division is unchanged; a test locks it in.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2 templates, SQLite, pytest.

## Global Constraints

- Discount translation strings stay in Persian (labels like `تخفیف معرفی`).
- All discount code is absolute Toman integers; never floats.
- `total_discount` is always `min(total_amount, sum_of_discounts)` — final amount never below 0.
- No new DB columns, no migrations. Reuse existing settings keys listed in the spec.
- Keep the existing `calculate_discounts(customer, total_amount, db, ...)` return contract: a `dict` with keys `referred_discount`, `referrer_discount`, `tier_discount`, `birthday_discount`, `total_discount`, `details` (list of Persian strings). Add a new key `custom_discount` (default 0).
- Do not alter the `services/tier.py` `calculate_points` function — only add a test for it.

---

### Task 1: Discount engine — carry-over, opt-in, custom discount

**Files:**
- Modify: `services/discount.py:12-78` (`calculate_discounts`)
- Test: `tests/test_sales.py` (add functions + imports inside functions)

**Interfaces:**
- Consumes: existing `Customer`, `Settings`, `db: Session`, `get_setting_int(db, key, default)`, `get_tier_config(db)`, `get_tier_discount_percent`, `get_birthday_effort`.
- Produces: `calculate_discounts(customer: Customer, total_amount: int, db: Session, use_referrer_discount: bool = True, custom_amount: int = 0, custom_percent: int = 0) -> dict`. The `is_first_purchase` parameter is removed. The returned dict adds `custom_discount: int` and `details` gains a custom-discount line.

- [ ] **Step 1: Write the failing tests**

Add these to `tests/test_sales.py`:

```python
def test_discount_engine_referred_carry_over_and_gates():
    """Referred discount: below min -> no apply; at/above min -> applies; and it is
    not tied to the literal first purchase (has_used_referred_discount is the guard)."""
    from services.discount import calculate_discounts
    from services._common import get_setting_int
    db = TestSession()
    db.add(Settings(key="min_purchase_for_discount", value="100000"))
    c = Customer(phone="09000000011", referral_code="AAA111", referred_discount=30000)
    db.add(c)
    db.commit()
    # territory below threshold -> 0
    below = calculate_discounts(c, 90000, db)
    assert below["referred_discount"] == 0, "must not apply below min purchase"
    # territory at/above threshold -> applied
    at = calculate_discounts(c, 100000, db)
    assert at["referred_discount"] == 30000
    db.close()


def test_discount_engine_referrer_opt_in():
    from services.discount import calculate_discounts
    db = TestSession()
    c = Customer(phone="09000001122", referral_code="ABB112",
                 referrer_discount=150000, active_referral_count=3)
    db.add(c)
    db.commit()
    off = calculate_discounts(c, 200000, db, use_referrer_discount=False)
    assert off["referrer_discount"] == 0, "opt-out must zero the referrer discount"
    on = calculate_discounts(c, 200000, db, use_referrer_discount=True)
    assert on["referrer_discount"] == 150000
    db.close()


def test_discount_engine_custom_amount_percent_precedence_and_cap():
    from services.discount import calculate_discounts
    db = TestSession()
    c = Customer(phone="09000008888", referral_code="AAA888")
    db.add(c)
    db.commit()
    amt = calculate_discounts(c, 200000, db, custom_amount=30000, custom_percent=0)
    assert amt["custom_discount"] == 30000, "amount-only applies the amount"
    pct = calculate_discounts(c, 200000, db, custom_amount=0, custom_percent=10)
    assert pct["custom_discount"] == 20000, "percent-only applies percent of total"
    both = calculate_discounts(c, 200000, db, custom_amount=5000, custom_percent=10)
    assert both["custom_discount"] == 5000, "amount takes precedence"
    capped = calculate_discounts(c, 10000, db, custom_amount=99999)
    assert capped["total_discount"] == 10000, "total discount capped at total amount"
    db.close()


def test_points_floor_to_threshold():
    """Points already floor-divide; lock it in. 90,000/100k -> 0; 100k -> 10; 190k -> 10; 250k -> 20."""
    from services.tier import calculate_points
    cfg = {"points_per_amount": 10, "points_per_toman": 100000}
    assert calculate_points(90000, cfg) == 0
    assert calculate_points(100000, cfg) == 10
    assert calculate_points(190000, cfg) == 10
    assert calculate_points(250000, cfg) == 20
```

> Note: `tests/test_sales.py` already sets up `TestSession` and `from models import ...` via local imports at the top of each function. If `Settings` is not imported inside those test funcs, add the needed local imports (`from models import Settings, Customer`). Follow the file's existing local-import style.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_sales.py -k "discount_engine or points_tier" -v`
Expected: collection/runtime errors — `calculate_discounts` called with the new kwargs raises `TypeError`; `referrer_discount` not zeroed when `custom_*` absent (some asserts may pass, most fail).

- [ ] **Step 3: Implement `calculate_discounts`**

Replace the whole function in `services/discount.py`:

```python
def calculate_discounts(
    customer: Customer,
    total_amount: int,
    db: Session,
    use_referrer_discount: bool = True,
    custom_amount: int = 0,
    custom_percent: int = 0,
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
        "custom_discount": 0,
        "total_amount": total_amount,
        "total_discount": 0,
        "details": [],
    }

    # 1. Referred discount (first qualifying purchase, must meet min purchase).
    #    Guard is `has_used_referred_discount`, NOT first-purchase: carries over
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_sales.py -k "discount_engine or points_tier" -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/discount.py tests/test_sales.py
git commit -m "feat(discounts): carry-over referred, opt-in referrer, manual custom discount"
```

---

### Task 2: Consolidate referrer reward — `Referral` row + no double credit

**Files:**
- Modify: `services/discount.py` (`apply_discounts_after_sale`, add imports)
- Test: `tests/test_sales.py` (add function)

**Interfaces:**
- Consumes: `apply_discounts_after_sale(customer, discounts: dict, db, referrer: Customer|None)` (existing signature).
- Produces: now also creates a `Referral` row and rewards the referrer exactly once, guarded by `referrer and not customer.referred_by`. Import `Referral` from `models` (already imports `Customer`); `current_year_month` is already imported.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sales.py`:

```python
def test_apply_discounts_creates_referral_row_and_rewards_once():
    from models import Referral
    from services.discount import apply_discounts_after_sale
    db = TestSession()
    referrer = Customer(phone="09000002111", referral_code="RRR111")
    referred = Customer(phone="09000002222", referral_code="SSS222",
                        referred_discount=30000)
    db.add_all([referrer, referred])
    db.commit()
    referred.referred_by = referrer.id

    discounts = {"referred_discount": 30000, "referrer_discount": 0}
    apply_discounts_after_sale(referred, discounts, db, referrer)
    db.commit()

    assert referred.has_used_referred_discount is True
    assert referrer.referrer_discount == 50000, "referrer rewarded once"
    row = db.query(Referral).filter(Referral.referred_id == referred.id).first()
    assert row is not None and row.referrer_id == referrer.id, "Referral row created"
    assert row.referred_discount == 30000
    db.close()
```

- [ ] **Step 2: Run the tail test to verify it fails**

Run: `python3 -m pytest tests/test_sales.py::test_apply_discounts_creates_referral_row_and_rewards_once -v`
Expected: FAIL — no `Referral` row exists.

- [ ] **Step 3: Implement**

Change the import at the top of `services/discount.py`:
```python
from models import Customer, Referral
```

Replace `apply_discounts_after_sale` with:

```python
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
    if referrer and not customer.referred_by:
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
```

> Note: the old body also had a `referrer and discounts["referred_discount"] > 0` block that rewarded the referrer but did not set `referred_by` / create a `Retained`. That is replaced by the unified block above. `current_year_month` stays imported.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_sales.py::test_apply_discounts_creates_referral_row_and_rewards_once -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/discount.py tests/test_sales.py
git commit -m "fix(discounts): consolidate referrer reward, create Referral row once"
```

---

### Task 3: `confirm-sale` — accept new fields, grant referred discount, drop inline block

**Files:**
- Modify: `routers/sales.py:259-341` (`sales_confirm`)
- Test: `tests/test_sales.py` (HTTP tests via `client`)

**Interfaces:**
- Consumes: `calculate_discounts` new signature; `apply_discounts_after_sale` new `Referral` behavior.
- Produces: `POST /sales/confirm-sale` now reads optional `use_referrer_discount`, `custom_discount_amount`, `custom_discount_percent`; grants `referred_discount` before calculating; no longer sets `customer.referred_by` / reward inline; no longer references `is_first_purchase`.

- [ ] **Step 1: Write the failing HTTP test**

Add to `tests/test_sales.py`:

```python
def test_confirm_sale_applies_referred_and_custom_discount():
    import json
    from models import Product, Settings
    db = TestSession()
    db.add(Settings(key="min_purchase_for_discount", value="0"))
    db.add(Settings(key="default_referred_discount", value="30000"))
    referrer = Customer(phone="09000003333", first_name="REF", last_name="REF",
                        referral_code="REFAAA")
    db.add(referrer)
    prod = Product(barcode="999001", name="Test shirt", price=200000,
                   cost_price=0, stock_quantity=10)
    db.add(prod)
    db.commit()
    ref_id, pid = referrer.id, prod.id

    cust = Customer(phone="09000004444", first_name="NEW", last_name="NEW",
                    referral_code="NNN444")
    db.add(cust)
    db.commit()
    cid = cust.id

    basket = json.dumps([{
        "product_id": pid, "name": "Test shirt", "size": None, "color": None,
        "unit_price": 200000, "quantity": 1, "total_price": 200000, "image_path": None,
    }])
    # Referral discount (30000) + custom amount (25000) => 55000 total discount
    resp = client.post("/sales/confirm-sale", data={
        "customer_id": cid, "basket_json": basket, "payment_method": "card",
        "referrer_code": "REFAAA", "referrer_phone": "",
        "use_referrer_discount": "", "custom_discount_amount": "25000",
        "custom_discount_percent": "",
    })
    assert resp.status_code == 200
    body = resp.text
    assert "30000" in body, "referred discount shown on receipt"
    assert "25000" in body, "custom discount shown on receipt"
    assert "55000" in body, "total discount shown on receipt"

    # Referral settled in the DB
    from models import Referral
    db2 = TestSession()
    c = db2.query(Customer).filter_by(id=cid).first()
    assert c.has_used_referred_discount is True
    assert c.referred_discount == 30000, "granted value retained on the customer"
    assert c.referred_by == ref_id
    row = db2.query(Referral).filter(Referral.referred_id == cid).first()
    assert row is not None
    db2.close()
    db.close()
```

> Adjustment note: `c.referred_discount` retains the granted value (30000) after being marked used — assert it equals 30000. `c.total_spent` becomes 145000 (200000 − 55000).

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_sales.py::test_confirm_sale_applies_referral_and_custom_discount -v`
Expected: FAIL — `confirm-sale` doesn't accept the new form fields (FastAPI `422`) and never grants `referred_discount`.

- [ ] **Step 3: Implement**

Change the `sales_confirm` signature:

```python
@router.post("/confirm-sale", response_class=HTMLResponse)
async def sales_confirm(
    request: Request,
    customer_id: int = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    payment_method: str = Form("card"),
    use_referrer_discount: str = Form(""),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
```

Replace the referrer-resolution + discount block. Remove:
- `is_first_purchase = customer.total_purchases == 0`
- the inline `if referrer and not customer.referred_by:` block that mutated the referrer directly.

Add before `calculate_discounts`:

```python
    # Grant the first-buy referred discount the moment a referrer is entered,
    # so it applies to THIS purchase (gated by min_purchase + carry-over).
    # NOTE: do NOT set customer.referred_by here — apply_discounts_after_sale
    # sets it and the Referral row, and its `not customer.referred_by` guard
    # must still see it as unset so the reward fires exactly once.
    if referrer and not customer.referred_by:
        customer.referred_discount = get_discount_setting(db, "default_referred_discount", 30000)

    discounts = calculate_discounts(
        customer,
        total_amount,
        db,
        use_referrer_discount=(use_referrer_discount == "1"),
        custom_amount=int(custom_discount_amount or 0),
        custom_percent=int(custom_discount_percent or 0),
    )
```

> Note: `sales.py` already imports `get_discount_setting` (`from services._common import ... get_setting_int as get_discount_setting`). Keep the sale SALE creation, SaleItem loop, `update_customer_after_purchase`, and `apply_discounts_after_sale` calls unchanged — `apply_discounts_after_sale(customer, discounts, db, referrer)` now handles everything else. The line `if referrer and discounts["referred_discount"] > 0:` block that previously granted the referrer reward inline is now owned by `apply_discounts_after_sale`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_sales.py::test_confirm_sale_applies_referral_and_custom_discount -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routers/sales.py tests/test_sales.py
git commit -m "fix(sales): grant referred discount at confirm, wire custom fields through"
```

---

### Task 4: Checkout UI — referral balance card, custom discount inputs, server-driven preview

**Files:**
- Modify: `routers/sales.py` (`sales_new`, `sales_lookuo_customer`, `sales_create_customer`, `sales_add_to_basket`, `sales_remove_from_basket`, add `_render_scan` helper)
- Modify: `templates/sales/checkout.html`
- No automated test here; verify manually (template rendering).

**Interfaces:**
- Produces: module helper `_render_scan(request, customer, basket, total_amount, db, *, referrer_code="", referrer_phone="", use_referrer_discount="", custom_amount=0, custom_percent=0, error=None, success=None)` returning a `TemplateResponse` in `step="scan"`; template vars: `use_referrer_discount`, `custom_discount_amount`, `custom_discount_percent`, `discounts`.

- [ ] **Step 1: Add `_render_scan` helper in `routers/sales.py`**

Above the routes (after imports), or near the handler, add:

```python
def _render_scan(request, customer, basket, total_amount, db,
                 referrer_code="", referrer_phone="",
                 use_referrer_discount="1", custom_discount_amount=0,
                 custom_discount_percent=0, error=None, success=None):
    discounts = calculate_discounts(
        customer, total_amount, db,
        use_referrer_discount=(use_referrer_discount == "1"),
        custom_amount=custom_discount_amount,
        custom_percent=custom_discount_percent,
    )
    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "scan",
        "customer": customer,
        "basket": basket,
        "basket_json": json.dumps(basket),
        "total_amount": total_amount,
        "tier_config": get_tier_config(db),
        "referrer_code": referrer_code,
        "referrer_phone": referrer_phone,
        "use_referrer_discount": use_referrer_discount,
        "custom_discount_amount": custom_discount_amount,
        "custom_discount_percent": custom_discount_percent,
        "discounts": discounts,
        "error": error,
        "success": success,
        "fmt": fmt,
    })
```

- [ ] **Step 2: Route handoff_scan(s) through `_render_scan`**

Replace the manual `templates.TemplateResponse(..., step="scan", ...)` dicts in:
- `sales_lookup_customer` (existing customer branch) → `return _render_scan(request, customer, [], 0, db)`
- `sales_create_customer` (both `existing` branch and new-customer branch) → `return _render_scan(request, existing, [], 0, db)` (and for the new customer).
- `sales_add_to_basket`: read the 3 new `Form` fields, replace the local `def _scan_step(...)` body to call `_render_scan(...)` with the current `total_amount`, and pass `use_referrer_discount`, `custom_discount_amount`, `custom_discount_percent` through. Keep `error`/`success`.
- `sales_remove_from_basket`: read the 3 new `Form` fields and `return _render_scan(request, customer, basket, total_amount, db, referrer_code=referrer_code, referrer_phone=referrer_phone, use_referrer_discount=use_referrer_discount, custom_discount_amount=custom_discount_amount, custom_discount_percent=custom_discount_percent)`.
- It is safe to remove the local `_scan_step` inner closure now that `_render_scan` exists.

- [ ] **Step 3: Add the new form fields to the three forms in `templates/sales/checkout.html`**

Inside the `scan` step:

1. **scan-form** (add-to-basket), after the existing `referrer_phone` hidden input, add the three hidden inputs so scanning preserves them:
```html
<input type="hidden" name="use_referrer_discount" value="{{ use_referrer_discount|default('', true) }}">
<input type="hidden" name="custom_discount_amount" value="{{ custom_discount_amount|default(0) }}">
<input type="hidden" name="custom_discount_percent" value="{{ custom_discount_percent|default(0) }}">
```
2. The **`per-item` remove form** — after its `referrer_phone` hidden input — add the same three hidden inputs.
3. The **confirm-form**, after the `referrer_phone` hidden input, add the same three hidden inputs.

- [ ] **Step 4: Replace the basket summary math with the server-driven loop**

In the scan step, replace the block from `{# ─── Basket ─── #}`'s `.basket-summary` (currently hardcoding `tier_pct`, `tier_disc`, `has_any_discount`, and the typed-tier summary rows) with:

```html
<div class="basket-summary">
    <div class="summary-row"><span>📦 جمع کل</span><span class="tnum">{{ fmt(total_amount) }} تومان</span></div>
    {% for line in discounts.details %}
    <div class="summary-row discount"><span>{{ line }}</span></div>
    {% endfor %}
    {% set final = (total_amount - discounts.total_discount) if total_amount >= discounts.total_discount else 0 %}
    <div class="summary-row total"><span>💰 مبلغ نهایی</span><span class="tnum">{{ fmt(final) }} تومان</span></div>
    {% if discounts.total_discount > 0 %}
    <div class="summary-row discount"><span>جمع تخفیف</span><span class="tnum">-{{ fmt(discounts.total_discount) }} تومان</span></div>
    {% endif %}
</div>
```

- [ ] **Step 5: Add the referral-balance checkbox + the two custom-discount fields**

Below the existing referral `.card` (the one with ref-code/ref-phone inputs), add:

```html
{% if customer.active_referral_count > 0 %}
<div class="card">
    <h4>🎟 تخفیف معرفی فعال ({{ customer.active_referral_count }} عدد)</h4>
    <p>جمعاً {{ fmt(customer.referrer_discount) }} تومان — با این خرید استفاده شود؟</p>
    <label style="display:flex; gap:0.5rem; align-items:center;">
        <input type="checkbox" id="use-ref-checkbox" {% if use_referrer_discount == '1' %}checked{% endif %}
               onchange="setAll('use_referrer_discount', this.checked ? '1' : '')">
        بله، از تخفیف معرفی در این فاکتور استفاده کن
    </label>
</div>
{% endif %}
```

And a custom-discount card (below it):
```html
<div class="card">
    <h4>🏷️ تخفیف ویژه</h4>
    <div class="form-row">
        <div class="form-group">
            <label>مبلغ (تومان)</label>
            <input type="number" min="0" value="{{ custom_discount_amount|default(0) }}"
                   oninput="setAll('custom_discount_amount', this.value)">
        </div>
        <div class="form-group">
            <label>درصد</label>
            <input type="number" min="0" max="100" value="{{ custom_discount_percent|default(0) }}"
                   oninput="setAll('custom_discount_percent', this.value)">
        </div>
    </div>
</div>
```

Finally, in the inline `<script>` at the bottom of the scan block, add a helper and mirror existing behavior:
```html
<script>
function setAll(name, value) {
    document.querySelectorAll('input[name="' + name + '"]').forEach(function (el) { el.value = value; });
}
// preserve the existing barcode-Enter behavior
document.getElementById('barcode-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); document.getElementById('scan-form').submit(); }
});
</script>
```

> Note: `has_any_discount` no longer existed after Step 5 — remove any leftover references. The `querySelectorAll`-based `setAll` syncs every hidden input of a given name (scan-form, remove-form, confirm-form) so the value survives whichever form is next submitted.

- [ ] **Step 6: Manual verification**

Start the app (`python3 main.py`) and walk a sale:

1. `POST /sales/lookup-customer` with a phone that exists; or create a customer.
2. On the scan step, set a referral discount via the new card and add items by barcode → the summary shows the declining final amount and the itemized `discounts.details`.
3. Enter a custom amount and clear the percent, submit, check the invoice shows both the referral and custom lines and the correct final amount.
4. Uncheck the referral checkbox → the invoice's total leaves the referral-discount balance untouched for next time.
5. Checkpoints for points: after a confirm, back-door earnings are floor-derived (already verified by Task 1's unit test).

- [ ] **Step 7: Commit**

```bash
git add routers/sales.py templates/sales/checkout.html
git commit -m "feat(ui): checkout referral opt-in, custom discount, server-driven preview"
```

---

## Self-review notes

- Coverage vs spec: (1) referral grant + min-purchase + carry-over → Task 1 & 3; (2) opt-in ✓ Task 2 & 4; (3) custom discount (amount+percent, amount wins, receipt) ✓ Task 1 & 3 & 4; (4) points floor ✓ Task 1 `test_points_tier_to_threshold`. No spec item is unmatched.
- The old `if referrer and discounts["referred_discount"] > 0:` referrer-reward path in `apply_discounts_after_sale` was replaced by the unified `if referrer and not customer.referred_by:` block — the `has_used` guard + `referred_discount` grant live in confirm-sale, so the reward still fires for a genuinely new referral, once.
- Type consistency: `calculate_discounts` returns `custom_discount`, `details`, `total_discount`; the template uses exactly those keys. `apply_discounts_after_sale` keeps its signature and now uses `current_year_month` and `Referral`.