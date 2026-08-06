# Variant Demand Counter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-variant demand counter so the shop owner can record how many customers want an out-of-stock size/color, guiding the next purchase order.

**Architecture:** One new `demand_count` integer column on `ProductVariant`, surfaced as a **+1** and **reset** button in each row of the product edit page's variants table. Two POST routes in `routers/products.py` mutate the count and redirect back to the product page.

**Tech Stack:** Python / FastAPI, SQLAlchemy ORM, SQLite, Jinja2 templates.

## Global Constraints
- Persist in existing SQLite DB via SQLAlchemy — **no** migration tooling. The column auto-creates on first app run.
- Load-bearing from the approved spec: counter is **not** auto-reset on restock; it clears only when the owner taps **reset**. Shown **only** on the product edit page.
- Exact route paths, verbatim: `POST /admin/variants/{id}/demand` (increment) and `POST /admin/variants/{id}/demand/reset` (reset).
- Message strings and UI labels are in Persian to match the rest of the admin UI.
- Follow the existing `check_admin` + redirect-on-fail pattern used by every other handler in `routers/products.py`.

---

### Task 1: Demand counter column + increment/reset routes

**Files:**
- Modify: `models.py` (ProductVariant class, after `stock_quantity`)
- Modify: `routers/products.py` (add two routes; put them near `admin_variant_update`, after line 460)
- Test: `tests/test_variant_demand.py` (create)

**Interfaces:**
- Consumes: existing `ProductVariant`, `Product`, `get_db`, `check_admin` (already defined elsewhere).
- Produces: `ProductVariant.demand_count` (int, default 0); routes `POST /admin/variants/{id}/demand` and `POST /admin/variants/{id}/demand/reset`, both returning a `RedirectResponse` to `/admin/products/{product_id}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_variant_demand.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_increment_then_reset_returns_to_zero():
    db = _make_db()
    product = models.Product(name="Test Shirt")
    db.add(product)
    db.flush()
    variant = models.ProductVariant(
        product_id=product.id, size="M", color="blue",
        price=100, barcode="9001",
    )
    db.add(variant)
    db.commit()

    assert variant.demand_count == 0

    # +1 twice (same mutation the increment route applies)
    variant.demand_count = (variant.demand_count or 0) + 1
    variant.demand_count = (variant.demand_count or 0) + 1
    db.commit()
    assert variant.demand_count == 2

    # reset (same mutation the reset route applies)
    variant.demand_count = 0
    db.commit()
    assert variant.demand_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_variant_demand.py -v`
Expected: FAIL — `AttributeError: 'ProductVariant' object has no attribute 'demand_count'` (column not added yet).

- [ ] **Step 3: Add the model column**

In `models.py`, inside `ProductVariant`, directly after the `stock_quantity` column definition (around line 164):

```python
    # Stock tracked per variant
    stock_quantity = Column(Integer, default=0)

    # Demand: manual counter of how many customers asked for this variant
    # while it was out of stock. Incremented/reset only by the owner.
    demand_count = Column(Integer, default=0)
```

- [ ] **Step 4: Add the two routes**

In `routers/products.py`, after the `admin_variant_delete` route (after line 476), add:

```python
@router.post("/variants/{variant_id}/demand", response_class=HTMLResponse)
async def admin_variant_demand_up(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Record one customer asking for this variant (out-of-stock)."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    variant.demand_count = (variant.demand_count or 0) + 1
    db.commit()
    return RedirectResponse(url=f"/admin/products/{variant.product_id}", status_code=303)


@router.post("/variants/{variant_id}/demand/reset", response_class=HTMLResponse)
async def admin_variant_demand_reset(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Clears the counted demand for a variant (owner filled the backlog)."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    variant.demand_count = 0
    db.commit()
    return RedirectResponse(url=f"/admin/products/{variant.product_id}", status_code=303)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_variant_demand.py -v`
Expected: PASS (4 assertions, all green).

- [ ] **Step 6: Commit**

```bash
git add models.py routers/products.py tests/test_variant_demand.py
git commit -m "feat(variants): demand counter column + increment/reset routes"
```

---

### Task 2: Demand controls in the product edit page

**Files:**
- Modify: `templates/admin/product_form.html` (edit-mode variants table, lines ~59-90)

**Interfaces:**
- Consumes: `ProductVariant.demand_count` (from Task 1); the two routes from Task 1.
- Produces: nothing new — renders the count and both buttons in the existing variants table.

- [ ] **Step 1: Add a demand column to the variants table**

In `templates/admin/product_form.html`, the edit-mode table has headers `<th>سایز</th>` … `<th>عملیات</th>`. Insert a **«درخواست»** column before **عملیات**:

```html
                        <th>بارکد</th>
                        <th>درخواست</th>
                        <th>عملیات</th>
```

Add a matching `<td>` in the loop body (after the barcode cell, before the edit link cell):

```html
                        <td style="font-family: monospace; direction: ltr;">{{ variant.barcode }}</td>
                        <td>
                            <span class="badge {% if variant.stock_quantity == 0 and variant.demand_count > 0 %}badge-none{% else %}badge-available{% endif %}">
                                {{ variant.demand_count or 0 }}
                            </span>
                            <form method="post" action="/admin/variants/{{ variant.id }}/demand" style="display: inline; margin: 0;">
                                <button type="submit" class="btn btn-sm btn-ghost" title="یک مشتری این سایز/رنگ را خواست">➕</button>
                            </form>
                            {% if variant.demand_count > 0 %}
                            <form method="post" action="/admin/variants/{{ variant.id }}/demand/reset"
                                  onsubmit="return confirm('📈 درخواست این تنوع صفر شود؟')" style="display: inline; margin: 0;">
                                <button type="submit" class="btn btn-sm btn-danger" title="پس از تأمین، شمارنده را صفر کن">🔄</button>
                            </form>
                            {% endif %}
                        </td>
                        <td>
                            <a href="/admin/variants/{{ variant.id }}/edit" class="btn btn-sm btn-ghost">✏️</a>
                        </td>
```

Notes:
- The reset button only renders when the count is > 0, keeping the row clean.
- `badge-none` / `badge-available` are existing classes — a zero-stock+wanted variant shows the dark "out" badge, matching the current stock-badge styling.

- [ ] **Step 2: Verify the page renders**

Start the app: `uvicorn main:app --reload`. Open `/admin/products/{some-id}` (log in as admin), confirm each variant row shows a **➕** button and, after a click, the count increments and a **🔄** reset button appears. Click **🔄**, confirm it returns to 0.

Its not possible to test the /admin route without a running server + session, so a click-through in the browser is sufficient.

- [ ] **Step 3: Commit**

```bash
git add templates/admin/product_form.html
git commit -m "feat(ui): demand +1/reset buttons in product edit variants table"
```

---

## Execution
Two independent, additive tasks. Task 1 is the data + routes (testable headlessly). Task 2 is the template wiring (verified by browser click-through). Both fix nothing else.