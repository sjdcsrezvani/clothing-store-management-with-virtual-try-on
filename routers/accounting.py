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
    Sale, SaleItem, Settings, Supplier, StockMovement, CashSession, SupplierPayment,
    FinancialEntry, CheckRecord, CheckReminder,
)
from services._common import fmt, check_admin, jalali_str
from services.accounting import (
    apply_customer_payment, debt_totals, get_cashbox, get_credit_limit,
    get_customer_debts, get_net_pl, get_opening_balance, get_payment_history,
    get_aged_receivables, reverse_payment, get_supplier_balances,
)
from services.analytics import get_date_range
from services.security import log_action, require_html_role
from services.templating import templates
from services.inventory import record_stock_movement, restore_cost_after_purchase_reversal
from services.reporting import canonical_report, reconciliation_checks
from services.checks import (
    add_reminders,
    check_alert_summary,
    dismiss_reminders,
    get_default_reminder_days,
    normalize_reminder_days,
    parse_amount_rials,
    parse_check_date,
    trigger_due_reminders,
)
from services.events import append_event

PAYMENT_LABELS = {"card": "💳 کارت", "cash": "💵 نقد", "credit": "📒 نسیه"}
EXPENSE_TYPE_LABELS = {"one_time": "یک‌باره", "monthly": "ماهانه"}
EXPENSE_TYPE_LABELS = {"one_time": "یک‌باره", "monthly": "ماهانه"}


def _csv_response(filename: str, rows: list[list]) -> Response:
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


router = APIRouter(prefix="/admin")


# ── Issued checks ────────────────────────────────────────────────────────────

@router.get("/checks", response_class=HTMLResponse)
async def admin_checks(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    triggered_count = trigger_due_reminders(db)
    if triggered_count:
        db.commit()
    summary = check_alert_summary(db)
    checks = db.query(CheckRecord).order_by(CheckRecord.due_at.asc(), CheckRecord.id.asc()).all()
    suppliers = db.query(Supplier).order_by(Supplier.name.asc()).all()
    reminders_setting = db.query(Settings).filter(Settings.key == "check_reminders_enabled").first()
    return templates.TemplateResponse(request, "admin/checks.html", {
        "checks": checks,
        "suppliers": suppliers,
        "summary": summary,
        "default_reminder_days": get_default_reminder_days(db),
        "reminders_enabled": reminders_setting is None or reminders_setting.value not in {"0", "false", "False", "off"},
        "now": datetime.now(timezone.utc).replace(tzinfo=None),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/checks/add", response_class=HTMLResponse)
async def admin_check_add(
    request: Request,
    provider_name: str = Form(""),
    supplier_id: str = Form(""),
    check_number: str = Form(""),
    amount_rials: str = Form("0"),
    issue_date: str = Form(""),
    due_date: str = Form(""),
    bank_name: str = Form(""),
    account_reference: str = Form(""),
    reminder_days: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    try:
        amount = parse_amount_rials(amount_rials)
        issue_at = parse_check_date(issue_date) or datetime.now(timezone.utc)
        due_at = parse_check_date(due_date)
        if due_at is None:
            raise ValueError("تاریخ سررسید چک معتبر نیست")
        if due_at <= issue_at:
            raise ValueError("تاریخ سررسید باید بعد از تاریخ صدور باشد")
        days = normalize_reminder_days(reminder_days or get_default_reminder_days(db))
    except ValueError as error:
        return RedirectResponse(url=f"/admin/checks?err={error}", status_code=303)

    provider_name = provider_name.strip()
    if not provider_name:
        return RedirectResponse(url="/admin/checks?err=نام دریافت‌کننده چک الزامی است.", status_code=303)
    supplier = None
    if supplier_id.isdigit():
        supplier = db.query(Supplier).filter(Supplier.id == int(supplier_id)).first()

    check = CheckRecord(
        supplier_id=supplier.id if supplier else None,
        provider_name=provider_name[:200],
        check_number=check_number.strip()[:100] or None,
        amount_rials=amount,
        issue_at=issue_at,
        due_at=due_at,
        bank_name=bank_name.strip()[:120] or None,
        account_reference=account_reference.strip()[:120] or None,
        note=note.strip() or None,
        operator_user_id=guard.id,
    )
    db.add(check)
    db.flush()
    add_reminders(db, check, days)
    append_event(
        db,
        "CheckIssued",
        "check",
        check.id,
        idempotency_key=f"check:{check.id}:issued",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={"provider_name": check.provider_name, "amount_rials": check.amount_rials, "due_at": check.due_at.isoformat(), "reminder_days": days},
        occurred_at=check.created_at,
    )
    db.commit()
    log_action(db, "check_add", f"ثبت چک برای {provider_name}", request=request, target_type="check", target_id=check.id, after={"amount_rials": amount, "due_at": check.due_at.isoformat()})
    return RedirectResponse(url="/admin/checks?msg=چک ثبت شد.", status_code=303)


@router.post("/checks/settings", response_class=HTMLResponse)
async def admin_check_settings(
    request: Request,
    reminder_days: str = Form(""),
    enabled: str = Form("1"),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    try:
        days = normalize_reminder_days(reminder_days or get_default_reminder_days(db))
    except ValueError as error:
        return RedirectResponse(url=f"/admin/checks?err={error}", status_code=303)
    values = {
        "check_default_reminders": ",".join(str(day) for day in days),
        "check_reminders_enabled": "1" if enabled == "1" else "0",
    }
    for key, value in values.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    log_action(db, "check_settings", "تنظیم هشدار چک‌ها", request=request, target_type="settings", after=values)
    return RedirectResponse(url="/admin/checks?msg=تنظیمات هشدار ذخیره شد.", status_code=303)


@router.post("/checks/{check_id}/reminders", response_class=HTMLResponse)
async def admin_check_reminders(check_id: int, request: Request, reminder_days: str = Form(""), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    check = db.query(CheckRecord).filter(CheckRecord.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="چک یافت نشد")
    if check.status != "issued":
        return RedirectResponse(url="/admin/checks?err=برای چک پرداخت‌شده یا لغوشده نمی‌توان هشدار جدید ساخت.", status_code=303)
    try:
        days = normalize_reminder_days(reminder_days)
    except ValueError as error:
        return RedirectResponse(url=f"/admin/checks?err={error}", status_code=303)
    db.query(CheckReminder).filter(CheckReminder.check_id == check.id, CheckReminder.status == "pending").update({"status": "dismissed", "dismissed_at": datetime.now(timezone.utc)}, synchronize_session=False)
    add_reminders(db, check, days)
    db.commit()
    return RedirectResponse(url="/admin/checks?msg=هشدارهای چک به‌روزرسانی شد.", status_code=303)


@router.post("/checks/{check_id}/status", response_class=HTMLResponse)
async def admin_check_status(check_id: int, request: Request, status: str = Form(""), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    check = db.query(CheckRecord).filter(CheckRecord.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="چک یافت نشد")
    if status not in {"paid", "cancelled", "bounced"}:
        return RedirectResponse(url="/admin/checks?err=وضعیت چک نامعتبر است.", status_code=303)
    if check.status != "issued":
        return RedirectResponse(url="/admin/checks?err=این چک قبلاً تعیین تکلیف شده است.", status_code=303)
    check.status = status
    if status == "paid":
        check.paid_at = datetime.now(timezone.utc)
    dismiss_reminders(db, check.id)
    event_type = {"paid": "CheckPaid", "cancelled": "CheckCancelled", "bounced": "CheckBounced"}[status]
    append_event(db, event_type, "check", check.id, idempotency_key=f"check:{check.id}:{status}", actor_user_id=guard.id, request_id=request.headers.get("X-Request-ID"), payload={"amount_rials": check.amount_rials})
    db.commit()
    log_action(db, "check_status", f"تغییر وضعیت چک #{check.id}", request=request, target_type="check", target_id=check.id, after={"status": status})
    return RedirectResponse(url="/admin/checks?msg=وضعیت چک به‌روزرسانی شد.", status_code=303)


@router.post("/checks/reminders/{reminder_id}/dismiss", response_class=HTMLResponse)
async def admin_check_reminder_dismiss(reminder_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    reminder = db.query(CheckReminder).filter(CheckReminder.id == reminder_id).first()
    if reminder:
        reminder.status = "dismissed"
        reminder.dismissed_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse(url="/admin/checks?msg=هشدار بسته شد.", status_code=303)


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
    report = canonical_report(db, start, end)
    checks = reconciliation_checks(db, start, end)
    pl = {
        "revenue": report["net_sales"],
        "cogs": report["cogs"],
        "gross": report["gross_profit"],
        "gross_margin": report["gross_margin"],
        "expenses": report["operating_expenses"],
        "one_time_expenses": report["one_time_expenses"],
        "monthly_expenses": report["monthly_expenses"],
        "net": report["net_profit"],
        "net_margin": round(report["net_profit"] / report["net_sales"] * 100, 1) if report["net_sales"] else 0,
        "invoice_count": report["sale_count"],
        "expense_cats": [],
    }
    debts = debt_totals(db)
    cashbox = get_cashbox(db, start, end, get_opening_balance(db))

    return templates.TemplateResponse(request, "admin/accounting.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "pl": pl,
        "report": report,
        "reconciliation": checks,
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
        rows = [["شماره", "تاریخ", "نوع هزینه", "دسته", "مبلغ", "توضیح"]]
        for e in db.query(Expense).order_by(Expense.created_at.desc()).all():
            rows.append([
                e.id, jalali_str(e.created_at, with_time=False),
                EXPENSE_TYPE_LABELS.get(e.expense_type, EXPENSE_TYPE_LABELS["one_time"]),
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
        db,
        customer,
        min(amount_int, debt),
        method=method,
        note=note,
        operator_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
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


@router.post("/payments/{payment_id}/reverse", response_class=HTMLResponse)
async def admin_payment_reverse(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Record a reversal while retaining the original payment record."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/admin/credit", status_code=303)
    customer_id = payment.customer_id
    reversed_amount = reverse_payment(
        db,
        payment,
        operator_id=guard.id,
        reason="Payment reversal",
        request_id=request.headers.get("X-Request-ID"),
    )
    db.commit()
    db.expire_all()
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    log_action(db, "payment_reverse", f"برگشت دریافت {reversed_amount:,}", request=request, target_type="payment", target_id=payment_id, after={"reversed_amount": reversed_amount, "operator_user_id": guard.id})
    return RedirectResponse(
        url=f"/admin/credit/{customer_id}?msg={reversed_amount:,} تومان برگشت ثبت شد.",
        status_code=303,
    )


@router.post("/payments/{payment_id}/delete", response_class=HTMLResponse)
async def admin_payment_delete_compat(payment_id: int, request: Request, db: Session = Depends(get_db)):
    return await admin_payment_reverse(payment_id, request, db)


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
    balances = {row["supplier"].id: row for row in get_supplier_balances(db)}
    return templates.TemplateResponse(request, "admin/suppliers.html", {
        "suppliers": suppliers,
        "total_by_supplier": totals,
        "balances": balances,
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
    supplier = Supplier(name=name.strip(), phone=phone.strip() or None, note=note.strip() or None)
    db.add(supplier)
    db.flush()
    db.commit()
    log_action(db, "supplier_add", name.strip(), request=request, target_type="supplier", target_id=supplier.id, after={"name": name.strip()})
    return RedirectResponse(url="/admin/suppliers?msg=تأمین‌کننده اضافه شد.", status_code=303)


@router.post("/suppliers/{supplier_id}/payment", response_class=HTMLResponse)
async def admin_supplier_payment(supplier_id: int, request: Request, amount: str = Form("0"), purchase_id: str = Form(""), note: str = Form(""), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        amount_int = 0
    purchase = None
    if purchase_id.isdigit():
        purchase = db.query(Purchase).filter(Purchase.id == int(purchase_id), Purchase.supplier_id == supplier.id, Purchase.is_reversed == False).first()
    supplier_owed = get_supplier_balances(db)
    balance = next((row["owed"] for row in supplier_owed if row["supplier"].id == supplier.id), 0)
    if not supplier or amount_int <= 0 or amount_int > balance:
        return RedirectResponse(url="/admin/suppliers?err=پرداخت تأمین‌کننده از بدهی بیشتر است یا نامعتبر است.", status_code=303)
    open_session = db.query(CashSession).filter(CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
    supplier_payment = SupplierPayment(
        supplier_id=supplier.id,
        purchase_id=purchase.id if purchase else None,
        amount=amount_int,
        operator_user_id=guard.id,
        note=note.strip() or None,
        method="cash",
        cash_session_id=open_session.id if open_session else None,
    )
    db.add(supplier_payment)
    db.flush()
    append_event(
        db,
        "SupplierPaymentRecorded",
        "supplier_payment",
        supplier_payment.id,
        idempotency_key=f"supplier-payment:{supplier_payment.id}:recorded",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "supplier_id": supplier_payment.supplier_id,
            "purchase_id": supplier_payment.purchase_id,
            "amount": supplier_payment.amount,
            "method": supplier_payment.method,
            "cash_session_id": supplier_payment.cash_session_id,
        },
    )
    db.commit()
    log_action(db, "supplier_payment", f"پرداخت به {supplier.name}", request=request, target_type="supplier", target_id=supplier.id, after={"amount": amount_int})
    return RedirectResponse(url="/admin/suppliers?msg=پرداخت تأمین‌کننده ثبت شد.", status_code=303)


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
            db,
            variant,
            qty,
            "purchase",
            unit_cost=unit_cost if unit_cost > 0 else None,
            purchase_id=purchase.id,
            note=f"ورود خرید #{purchase.id}",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
        )
        if unit_cost > 0:
            variant.cost_price = unit_cost  # refresh the cost basis

    append_event(
        db,
        "PurchaseRecorded",
        "purchase",
        purchase.id,
        idempotency_key=f"purchase:{purchase.id}:recorded",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={"total_cost": purchase.total_cost, "supplier_id": purchase.supplier_id},
    )
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
            db,
            variant,
            -item.quantity,
            "purchase_reversal",
            unit_cost=item.unit_cost if item.unit_cost > 0 else None,
            purchase_id=purchase.id,
            note=f"برگشت خرید #{purchase.id}",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
        )
        restore_cost_after_purchase_reversal(db, variant, item.prev_cost_price)

    append_event(
        db,
        "PurchaseReversed",
        "purchase",
        purchase.id,
        idempotency_key=f"purchase:{purchase.id}:reversed",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={"total_cost": purchase.total_cost},
        occurred_at=purchase.reversed_at,
    )
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
    all_active_expenses = db.query(Expense).filter(Expense.reversed_at.is_(None)).all()
    total = sum(expense.amount for expense in all_active_expenses)
    expense_type_totals = {"one_time": 0, "monthly": 0}
    for expense in all_active_expenses:
        expense_type = expense.expense_type if expense.expense_type in EXPENSE_TYPE_LABELS else "one_time"
        expense_type_totals[expense_type] += expense.amount
    return templates.TemplateResponse(request, "admin/expenses.html", {
        "expenses": expenses,
        "total": total,
        "expense_type_totals": expense_type_totals,
        "expense_type_labels": EXPENSE_TYPE_LABELS,
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
    expense_type: str = Form("one_time"),
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
    if expense_type not in EXPENSE_TYPE_LABELS:
        return RedirectResponse(url="/admin/expenses?err=نوع هزینه نامعتبر است.", status_code=303)
    open_session = db.query(CashSession).filter(CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
    expense = Expense(
        amount=amount_int,
        category=category.strip() or None,
        expense_type=expense_type,
        payment_method="cash",
        cash_session_id=open_session.id if open_session else None,
        note=note.strip() or None,
    )
    db.add(expense)
    db.flush()
    append_event(
        db,
        "ExpenseRecorded",
        "expense",
        expense.id,
        idempotency_key=f"expense:{expense.id}:recorded",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "amount": expense.amount,
            "category": expense.category,
            "expense_type": expense.expense_type,
            "payment_method": expense.payment_method,
            "cash_session_id": expense.cash_session_id,
        },
    )
    db.commit()
    log_action(db, "expense_add", f"{amount_int:,} تومان ({category or '—'}, {EXPENSE_TYPE_LABELS[expense_type]})", request=request, target_type="expense", target_id=expense.id, after={"amount": amount_int, "category": category, "expense_type": expense_type})
    return RedirectResponse(url="/admin/expenses?msg=هزینه ثبت شد.", status_code=303)


@router.post("/expenses/{expense_id}/delete", response_class=HTMLResponse)
async def admin_expense_delete(expense_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if expense:
        from services.ledger import reverse_expense_immutably
        reverse_expense_immutably(
            db,
            expense,
            guard.id,
            "Expense reversal",
            request_id=request.headers.get("X-Request-ID"),
        )
        db.commit()
        log_action(db, "expense_reverse", f"برگشت هزینه {expense.amount:,}", request=request, target_type="expense", target_id=expense_id, after={"reversed": True, "operator_user_id": guard.id})
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
    active_session = db.query(CashSession).filter(CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
    register = get_cashbox(db, start, end, active_session.opening_balance if active_session else opening, active_session.id if active_session else None)
    return templates.TemplateResponse(request, "admin/cashbox.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "register": register,
        "active_session": active_session,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/cashbox/open", response_class=HTMLResponse)
async def admin_cashbox_open(request: Request, opening: str = Form("0"), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        opening_int = int(opening)
    except (TypeError, ValueError):
        opening_int = -1
    if opening_int < 0 or db.query(CashSession).filter(CashSession.status == "open").first():
        return RedirectResponse(url="/admin/cashbox?err=موجودی اولیه نامعتبر است یا صندوق دیگری باز است.", status_code=303)
    session = CashSession(cashier_user_id=guard.id, opening_balance=opening_int)
    db.add(session)
    db.flush()
    append_event(
        db,
        "CashSessionOpened",
        "cash_session",
        session.id,
        idempotency_key=f"cash-session:{session.id}:opened",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={"opening_balance": session.opening_balance},
        occurred_at=session.opened_at,
    )
    db.commit()
    log_action(db, "cash_session_open", "باز کردن صندوق", request=request, target_type="cash_session", target_id=session.id, after={"opening_balance": opening_int})
    return RedirectResponse(url="/admin/cashbox?msg=صندوق باز شد.", status_code=303)


@router.post("/cashbox/close", response_class=HTMLResponse)
async def admin_cashbox_close(request: Request, counted: str = Form("0"), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    session = db.query(CashSession).filter(CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
    try:
        counted_int = int(counted)
    except (TypeError, ValueError):
        counted_int = -1
    if not session or counted_int < 0:
        return RedirectResponse(url="/admin/cashbox?err=صندوق باز یا مبلغ شمارش‌شده معتبر نیست.", status_code=303)
    start = session.opened_at
    end = datetime.now(timezone.utc)
    expected = get_cashbox(db, start, end, session.opening_balance, session.id)["closing"]
    session.counted_closing_balance = counted_int
    session.variance = counted_int - expected
    session.closed_at = end
    session.manager_user_id = guard.id
    session.status = "closed"
    append_event(
        db,
        "CashSessionClosed",
        "cash_session",
        session.id,
        idempotency_key=f"cash-session:{session.id}:closed",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "expected_closing_balance": expected,
            "counted_closing_balance": counted_int,
            "variance": session.variance,
        },
        occurred_at=session.closed_at,
    )
    db.commit()
    log_action(db, "cash_session_close", "بستن صندوق", request=request, target_type="cash_session", target_id=session.id, after={"expected": expected, "counted": counted_int, "variance": session.variance})
    return RedirectResponse(url="/admin/cashbox?msg=صندوق بسته شد.", status_code=303)


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
