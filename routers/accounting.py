import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Customer, Expense, Payment, ProductVariant, Purchase, PurchaseItem,
    Sale, SaleItem, Settings, Supplier, StockMovement,
)
from services._common import fmt, check_admin, jalali_str
from services.accounting import (
    apply_customer_payment, debt_totals, get_cashbox, get_credit_limit,
    get_customer_debts, get_net_pl, get_opening_balance, get_payment_history,
    get_aged_receivables, reverse_payment,
)
from services.analytics import get_date_range
from services.security import log_action, require_html_role
from services.templating import templates
from services.inventory import record_stock_movement, restore_cost_after_purchase_reversal

router = APIRouter(prefix="/admin")
PAYMENT_LABELS = {"card": "💳 کارت", "cash": "💵 نقد", "credit": "📒 نسیه"}


def _csv_response(filename: str, rows: list[list]) -> Response:
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Accounting dashboard (net P&L) ───────────────────────────────────────────

@router.get("/accounting", response_class=HTMLResponse)
async def admin_accounting(
    request: Request,
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    start, end = get_date_range(period, start_date or None, end_date or None)
    pl = get_net_pl(db, start, end)
    debts = debt_totals(db)
    cashbox = get_cashbox(db, start, end, get_opening_balance(db))

    return templates.TemplateResponse(request, "admin/accounting.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "pl": pl,
        "debts": debts,
        "cashbox": cashbox,
        "payment_labels": PAYMENT_LABELS,
        "today_jalali": jalali_str(datetime.now(timezone.utc), with_time=False),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


# ── CSV exports ──────────────────────────────────────────────────────────────

@router.get("/accounting/export")
async def admin_accounting_export(
    request: Request,
    kind: str = "sales",
    start_date: str = "",
    end_date: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    start, end = get_date_range("custom" if (start_date and end_date) else "all",
                                start_date or None, end_date or None)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    if kind == "customers":
        rows = [["تلفن", "نام", "نام خانوادگی", "نام فرزند", "سطح", "امتیاز",
                 "تعداد خرید", "مجموع خرید", "بدهی نسیه", "کد معرفی", "تاریخ عضویت"]]
        for c in db.query(Customer).order_by(Customer.created_at.desc()).all():
            rows.append([
                c.phone, c.first_name or "", c.last_name or "", c.child_name or "",
                c.tier, c.total_points or 0, c.total_purchases or 0,
                c.total_spent or 0, c.total_debt or 0, c.referral_code,
                jalali_str(c.created_at, with_time=False),
            ])
        return _csv_response(f"customers_{today}.csv", rows)

    if kind == "purchases":
        rows = [["شماره", "تاریخ", "تأمین‌کننده", "مبلغ کل", "وضعیت", "توضیح"]]
        for p in db.query(Purchase).order_by(Purchase.created_at.desc()).all():
            rows.append([
                p.id, jalali_str(p.created_at, with_time=False),
                p.supplier.name if p.supplier else "—",
                p.total_cost or 0, "برگشت‌خورده" if p.is_reversed else "فعال", p.note or "",
            ])
        return _csv_response(f"purchases_{today}.csv", rows)

    if kind == "expenses":
        rows = [["شماره", "تاریخ", "دسته", "مبلغ", "توضیح"]]
        for e in db.query(Expense).order_by(Expense.created_at.desc()).all():
            rows.append([
                e.id, jalali_str(e.created_at, with_time=False),
                e.category or "—", e.amount, e.note or "",
            ])
        return _csv_response(f"expenses_{today}.csv", rows)

    # default: sales
    rows = [["شماره", "تاریخ", "مشتری", "اقلام", "جمع کل", "تخفیف",
             "مبلغ نهایی", "روش پرداخت", "ابطال‌شده"]]
    sales = db.query(Sale).filter(
        Sale.payment_confirmed == True,
        Sale.created_at.between(start, end),
    ).order_by(Sale.created_at.desc()).all()
    customer_cache: dict[int, str] = {}
    for s in sales:
        if s.customer_id and s.customer_id not in customer_cache:
            c = db.query(Customer).filter(Customer.id == s.customer_id).first()
            customer_cache[s.customer_id] = c.full_name if c else "—"
        items = db.query(SaleItem).filter(SaleItem.sale_id == s.id).all()
        items_text = "، ".join(
            f"{i.quantity}×{i.product.name[:20] if i.product else '—'}" for i in items
        )
        rows.append([
            s.id, jalali_str(s.created_at, with_time=False),
            customer_cache.get(s.customer_id, "—"),
            items_text, s.total_amount, s.discount_amount, s.final_amount,
            PAYMENT_LABELS.get(s.payment_method, s.payment_method),
            "بله" if s.is_refunded else "خیر",
        ])
    return _csv_response(f"sales_{today}.csv", rows)


# ── Credit sales (نسیه) ledger ───────────────────────────────────────────────

@router.get("/credit", response_class=HTMLResponse)
async def admin_credit(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    return templates.TemplateResponse(request, "admin/credit.html", {
        "debts": get_customer_debts(db),
        "total_debt": debt_totals(db)["total_debt"],
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/credit/{customer_id}", response_class=HTMLResponse)
async def admin_credit_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    unpaid = db.query(Sale).filter(
        Sale.customer_id == customer.id,
        Sale.payment_method == "credit",
        Sale.is_refunded == False,
        Sale.credit_settled == False,
    ).order_by(Sale.created_at.asc()).all()

    return templates.TemplateResponse(request, "admin/credit_customer.html", {
        "customer": customer,
        "debt": customer.total_debt or 0,
        "credit_limit": get_credit_limit(db, customer),
        "custom_limit": customer.credit_limit,
        "unpaid_sales": unpaid,
        "payments": get_payment_history(db, customer.id),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/credit/pay", response_class=HTMLResponse)
async def admin_credit_pay(
    request: Request,
    customer_id: int = Form(...),
    amount: str = Form("0"),
    method: str = Form("cash"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(url="/admin/credit?err=مشتری یافت نشد.", status_code=303)

    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        amount_int = 0

    debt = customer.total_debt or 0
    if amount_int <= 0:
        return RedirectResponse(url=f"/admin/credit/{customer.id}?err=مبلغ معتبر نیست.", status_code=303)

    applied = apply_customer_payment(
        db, customer, min(amount_int, debt), method=method, note=note,
    )
    db.commit()
    if applied > 0:
        log_action(db, "credit_payment", f"دریافت {applied:,} از {customer.phone}", request=request, target_type="customer", target_id=customer.id, after={"amount": applied, "method": method})
        return RedirectResponse(
            url=f"/admin/credit/{customer.id}?msg={applied:,} تومان ثبت شد.", status_code=303,
        )
    return RedirectResponse(url=f"/admin/credit/{customer.id}?err=بدهی‌ای برای تسویه وجود ندارد.", status_code=303)


@router.post("/credit/{customer_id}/limit", response_class=HTMLResponse)
async def admin_credit_limit(customer_id: int, request: Request, credit_limit: str = Form(""), db: Session = Depends(get_db)):
    """Set a per-customer credit limit (سقف اعتبار). Empty/0 resets to the
    store-wide default; the checkout blocks نسیه sales that exceed it."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(url="/admin/credit", status_code=303)
    try:
        limit = int(credit_limit or 0)
    except (TypeError, ValueError):
        limit = 0
    customer.credit_limit = max(0, limit) or None
    db.commit()
    log_action(db, "credit_limit", f"سقف اعتبار {customer.phone}: {customer.credit_limit or 'پیش‌فرض'}", request=request, target_type="customer", target_id=customer.id, after={"credit_limit": customer.credit_limit})
    return RedirectResponse(url=f"/admin/credit/{customer.id}?msg=سقف اعتبار ذخیره شد.", status_code=303)


@router.post("/payments/{payment_id}/delete", response_class=HTMLResponse)
async def admin_payment_delete(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Undo a نسیه payment. The customer's debt ledger is rebuilt from the
    remaining payments, so invoices and the cash box return to their prior state."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/admin/credit", status_code=303)
    customer_id = payment.customer_id
    reversed_amount = reverse_payment(db, payment)
    db.commit()
    log_action(db, "payment_delete", f"برگشت دریافت {reversed_amount:,}", request=request, target_type="payment", target_id=payment_id, after={"reversed_amount": reversed_amount})
    return RedirectResponse(
        url=f"/admin/credit/{customer_id}?msg={reversed_amount:,} تومان از دریافت‌ها حذف شد.",
        status_code=303,
    )


# ── Collections (aged receivables) ───────────────────────────────────────────

@router.get("/collections", response_class=HTMLResponse)
async def admin_collections(request: Request, db: Session = Depends(get_db)):
    """Aged-receivables dashboard: bucket each customer's outstanding نسیه
    balance by the age of their oldest unpaid invoice (≤30 / 31–60 / 61–90 / 90+).
    Helps the owner chase overdue credit before it goes bad."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    report = get_aged_receivables(db)
    return templates.TemplateResponse(request, "admin/collections.html", {
        "buckets": report["buckets"],
        "totals": report["totals"],
        "grand_total": report["grand_total"],
        "now": report["now"],
        "msg": request.query_params.get("msg", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


# ── Suppliers ────────────────────────────────────────────────────────────────

@router.get("/suppliers", response_class=HTMLResponse)
async def admin_suppliers(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    suppliers = db.query(Supplier).order_by(Supplier.created_at.desc()).all()
    totals = dict(
        db.query(Supplier.id, func.coalesce(func.sum(Purchase.total_cost), 0))
        .join(Purchase, (Purchase.supplier_id == Supplier.id) & (Purchase.is_reversed == False), isouter=True)
        .group_by(Supplier.id).all()
    )
    return templates.TemplateResponse(request, "admin/suppliers.html", {
        "suppliers": suppliers,
        "total_by_supplier": totals,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/suppliers/add", response_class=HTMLResponse)
async def admin_supplier_add(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    if not name.strip():
        return RedirectResponse(url="/admin/suppliers?err=نام تأمین‌کننده الزامی است.", status_code=303)
    db.add(Supplier(name=name.strip(), phone=phone.strip() or None, note=note.strip() or None))
    db.commit()
    log_action(db, "supplier_add", name.strip(), request=request, target_type="supplier", target_id=supplier.id, after={"name": name.strip()})
    return RedirectResponse(url="/admin/suppliers?msg=تأمین‌کننده اضافه شد.", status_code=303)


@router.post("/suppliers/{supplier_id}/delete", response_class=HTMLResponse)
async def admin_supplier_delete(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier:
        for p in db.query(Purchase).filter(Purchase.supplier_id == supplier.id).all():
            p.supplier_id = None
        db.delete(supplier)
        db.commit()
        log_action(db, "supplier_delete", supplier.name, request=request, target_type="supplier", target_id=supplier_id, before={"name": supplier.name})
    return RedirectResponse(url="/admin/suppliers", status_code=303)


# ── Purchases ────────────────────────────────────────────────────────────────

@router.get("/purchases", response_class=HTMLResponse)
async def admin_purchases(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    from models import Product
    products = db.query(Product).filter(Product.is_active == True) \
        .order_by(Product.name).all()
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    purchases = db.query(Purchase).order_by(Purchase.created_at.desc()).limit(50).all()

    return templates.TemplateResponse(request, "admin/purchases.html", {
        "products": products,
        "suppliers": suppliers,
        "purchases": purchases,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/purchases/add", response_class=HTMLResponse)
async def admin_purchase_add(
    request: Request,
    supplier_id: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    indices = set()
    for key in form.keys():
        if key.startswith("purchase_variant_"):
            try:
                indices.add(int(key.split("_")[-1]))
            except ValueError:
                pass

    items = []
    for idx in sorted(indices):
        try:
            variant_id = int(form.get(f"purchase_variant_{idx}", "") or 0)
            qty = int(form.get(f"purchase_qty_{idx}", "") or 0)
            unit_cost = int(form.get(f"purchase_cost_{idx}", "") or 0)
        except (TypeError, ValueError):
            continue
        if variant_id <= 0 or qty <= 0:
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue
        items.append((variant, qty, max(0, unit_cost)))

    if not items:
        return RedirectResponse(url="/admin/purchases?err=حداقل یک قلم خرید وارد کنید.", status_code=303)

    total_cost = sum(qty * cost for _, qty, cost in items)
    purchase = Purchase(
        supplier_id=int(supplier_id) if supplier_id.isdigit() and int(supplier_id) > 0 else None,
        total_cost=total_cost,
        note=note.strip() or None,
    )
    db.add(purchase)
    db.flush()

    for variant, qty, unit_cost in items:
        db.add(PurchaseItem(
            purchase_id=purchase.id,
            variant_id=variant.id,
            product_id=variant.product_id,
            quantity=qty,
            unit_cost=unit_cost,
            # Retained as legacy context; current cost is restored only from
            # surviving purchase movements during a safe reversal.
            prev_cost_price=variant.cost_price if unit_cost > 0 else None,
        ))
        record_stock_movement(
            db, variant, qty, "purchase",
            unit_cost=unit_cost if unit_cost > 0 else None,
            purchase_id=purchase.id,
            note=f"ورود خرید #{purchase.id}",
        )
        if unit_cost > 0:
            variant.cost_price = unit_cost  # refresh the cost basis

    db.commit()
    log_action(db, "purchase_add", f"خرید {total_cost:,} تومان", request=request, target_type="purchase", target_id=purchase.id, after={"total_cost": total_cost})
    return RedirectResponse(url="/admin/purchases?msg=خرید با موفقیت ثبت شد و موجودی به‌روز شد.", status_code=303)


@router.get("/inventory-movements", response_class=HTMLResponse)
async def admin_inventory_movements(request: Request, db: Session = Depends(get_db)):
    """Read-only audit view of the append-only inventory ledger."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    movements = db.query(StockMovement).order_by(
        StockMovement.created_at.desc(), StockMovement.id.desc()
    ).limit(200).all()
    return templates.TemplateResponse(request, "admin/inventory_movements.html", {
        "movements": movements,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/purchases/{purchase_id}/delete", response_class=HTMLResponse)
async def admin_purchase_delete(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    """Safely reverse a purchase without deleting its historical record.

    A purchase is locked once a later sale could have consumed its units. The
    ledger cannot infer lots retroactively, so refusing that reversal is safer
    than making stock or cost history negative.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/admin/purchases", status_code=303)
    if purchase.is_reversed:
        return RedirectResponse(url="/admin/purchases?err=این خرید قبلاً برگشت خورده است.", status_code=303)

    locked_items = []
    for item in purchase.items:
        if not item.variant_id:
            continue
        later_sale = db.query(StockMovement).filter(
            StockMovement.variant_id == item.variant_id,
            StockMovement.movement_type == "sale",
            StockMovement.created_at >= purchase.created_at,
        ).first()
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        if later_sale or not variant or (variant.stock_quantity or 0) < item.quantity:
            locked_items.append(item.variant_id)

    if locked_items:
        return RedirectResponse(
            url="/admin/purchases?err=این خرید قابل برگشت نیست؛ بخشی از موجودی آن پس از خرید فروخته یا مصرف شده است.",
            status_code=303,
        )

    # Mark first so cost recomputation ignores this purchase while preserving
    # the purchase and its items as immutable historical evidence.
    purchase.is_reversed = True
    purchase.reversed_at = datetime.now(timezone.utc)
    db.flush()

    for item in purchase.items:
        if not item.variant_id or not item.quantity:
            continue
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        if not variant:
            continue
        record_stock_movement(
            db, variant, -item.quantity, "purchase_reversal",
            unit_cost=item.unit_cost if item.unit_cost > 0 else None,
            purchase_id=purchase.id,
            note=f"برگشت خرید #{purchase.id}",
        )
        restore_cost_after_purchase_reversal(db, variant, item.prev_cost_price)

    db.commit()
    log_action(db, "purchase_reverse", f"برگشت خرید #{purchase_id}", request=request, target_type="purchase", target_id=purchase_id, after={"reversed": True})
    return RedirectResponse(url="/admin/purchases?msg=خرید با ثبت حرکت برگشت، معکوس شد.", status_code=303)


# ── Expenses ─────────────────────────────────────────────────────────────────

@router.get("/expenses", response_class=HTMLResponse)
async def admin_expenses(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    expenses = db.query(Expense).order_by(Expense.created_at.desc()).limit(100).all()
    total = sum(e.amount for e in db.query(Expense).all())
    return templates.TemplateResponse(request, "admin/expenses.html", {
        "expenses": expenses,
        "total": total,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/expenses/add", response_class=HTMLResponse)
async def admin_expense_add(
    request: Request,
    amount: str = Form(...),
    category: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        amount_int = 0
    if amount_int <= 0:
        return RedirectResponse(url="/admin/expenses?err=مبلغ معتبر نیست.", status_code=303)
    expense = Expense(
        amount=amount_int,
        category=category.strip() or None,
        note=note.strip() or None,
    )
    db.add(expense)
    db.flush()
    db.commit()
    log_action(db, "expense_add", f"{amount_int:,} تومان ({category or '—'})", request=request, target_type="expense", target_id=expense.id, after={"amount": amount_int, "category": category})
    return RedirectResponse(url="/admin/expenses?msg=هزینه ثبت شد.", status_code=303)


@router.post("/expenses/{expense_id}/delete", response_class=HTMLResponse)
async def admin_expense_delete(expense_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if expense:
        db.delete(expense)
        db.commit()
        log_action(db, "expense_delete", f"برگشت هزینه {expense.amount:,}", request=request, target_type="expense", target_id=expense_id, before={"amount": expense.amount})
    return RedirectResponse(url="/admin/expenses", status_code=303)


# ── Cash box ─────────────────────────────────────────────────────────────────

@router.get("/cashbox", response_class=HTMLResponse)
async def admin_cashbox(
    request: Request,
    period: str = "today",
    start_date: str = "",
    end_date: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    start, end = get_date_range(period, start_date or None, end_date or None)
    opening = get_opening_balance(db)
    register = get_cashbox(db, start, end, opening)
    return templates.TemplateResponse(request, "admin/cashbox.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "register": register,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/cashbox/opening", response_class=HTMLResponse)
async def admin_cashbox_opening(request: Request, opening: str = Form("0"), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        opening_int = int(opening)
    except (TypeError, ValueError):
        opening_int = 0
    row = db.query(Settings).filter(Settings.key == "cash_opening_balance").first()
    if row:
        row.value = str(opening_int)
    else:
        db.add(Settings(key="cash_opening_balance", value=str(opening_int)))
    db.commit()
    return RedirectResponse(url="/admin/cashbox?msg=موجودی صندوق ذخیره شد.", status_code=303)
