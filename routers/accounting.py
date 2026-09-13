import csv
import io
import json
from datetime import datetime, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import String, case, cast, func, literal, or_
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (
    BusinessEvent, Customer, Expense, Payment, ProductVariant, Product, Purchase,
    PurchaseItem, Sale, SaleItem, Settings, StaffUser, Supplier, StockMovement,
    CashSession, SupplierPayment, FinancialEntry, CheckRecord, CheckReminder,
    PaymentReversal, to_english_digits,
)
from services._common import (
    fmt, check_admin, get_setting_int, jalali_str, parse_form_date, parse_form_date_end,
    parse_jalali_input, parse_jalali_input_end,
)
from services.accounting import (
    AGE_BUCKET_LABELS,
    AGE_BUCKETS,
    DEBT_SORTS,
    DEBT_STATUS_LABELS,
    PER_PAGE as DEBTS_PER_PAGE,
    apply_customer_payment, apply_purchase_cost_basis, assign_due_dates,
    credit_due_date_for, credit_reminder_allowed, credit_reminder_cooldown_hours,
    credit_reminder_vars, credit_terms_days, debt_totals, debt_drift, get_cashbox,
    age_bucket, as_utc, build_debt_rows, days_past_due, due_effective_at,
    get_credit_limit, get_net_pl, get_opening_balance, get_payment_history,
    last_credit_reminder, list_debts, list_open_invoices,
    mark_credit_reminder_sent, reverse_payment, sale_remaining,
    unpaid_credit_sales,
    get_supplier_balances, purchase_effective_at, purchase_effective_column,
    purchase_item_totals, purchase_landed_unit_cost, purchase_overview,
    purchase_paid_amount, purchase_paid_from_rollup, purchase_payment_rollup,
    purchase_settlement, refresh_purchase_amount_paid,
)
from services.sms import queue_credit_reminder_sms
from services.analytics import get_date_range
from services.security import log_action, require_html_role
from services.templating import templates
from services.inventory import (
    LEGACY_MOVEMENT_TYPES,
    MOVEMENT_LABELS,
    MOVEMENT_TYPES,
    ledger_mismatched_variants,
    ledger_missing_variants,
    ledger_snapshot,
    movement_direction,
    movement_type_label,
    record_cost_adjustment,
    record_ledger_opening,
    restore_cost_after_purchase_reversal,
)
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

MOVEMENT_PAGE_SIZE = 25
MOVEMENT_DIRECTIONS = {"all": "همه حرکت‌ها", "in": "فقط ورودی", "out": "فقط خروجی"}
RECONCILE_VIEWS = {
    "missing": "تنوع‌های بدون سابقه در دفتر",
    "mismatch": "تنوع‌های نامطابق با دفتر",
}


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
            if p.is_draft:
                purchase_state = "پیش‌نویس"
            elif p.is_reversed:
                purchase_state = "برگشت‌خورده"
            else:
                purchase_state = "فعال"
            rows.append([
                p.id, jalali_str(p.created_at, with_time=False),
                p.supplier.name if p.supplier else "—",
                p.total_cost or 0, purchase_state, p.note or "",
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

def _clean(value, allowed, default=""):
    """A query param is honoured only when it is one of the values we know."""
    value = (value or "").strip()
    return value if value in allowed else default


@router.get("/credit", response_class=HTMLResponse)
async def admin_credit(
    request: Request,
    search: str = "",
    status: str = "all",
    bucket: str = "",
    sort: str = "debt",
    view: str = "customers",
    page: int = 1,
    db: Session = Depends(get_db),
):
    """نسیه, one page: who owes what, how late it is, and how to collect it.

    Absorbs the old aged-receivables dashboard, which could only show one row per
    customer and had no filters — the ageing is now the KPI row and the invoice
    view here, and the page the shop actually works from.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    status = _clean(status, DEBT_STATUS_LABELS, "all")
    sort = _clean(sort, DEBT_SORTS, "debt")
    bucket = _clean(bucket, AGE_BUCKETS, "")
    view = _clean(view, ("customers", "invoices"), "customers")

    listing = list_debts(db, search=search, status=status, bucket=bucket,
                         sort=sort, page=page, per_page=DEBTS_PER_PAGE)
    invoices = (list_open_invoices(db, search=search, status=status, bucket=bucket,
                                   page=page, per_page=DEBTS_PER_PAGE)
                if view == "invoices" else None)
    return templates.TemplateResponse(request, "admin/credit.html", {
        "overview": listing["overview"],
        "rows": listing["rows"],
        "total": listing["total"],
        "page": listing["page"],
        "total_pages": listing["total_pages"],
        "has_filters": listing["has_filters"],
        "invoice_page": invoices,
        "view": view,
        "search": search,
        "status": status,
        "sort": sort,
        "bucket": bucket,
        "status_labels": DEBT_STATUS_LABELS,
        "sort_labels": DEBT_SORTS,
        "bucket_labels": AGE_BUCKET_LABELS,
        "due_soon_days": listing["overview"]["due_soon_days"],
        "terms_days": credit_terms_days(db),
        "reminder_cooldown": credit_reminder_cooldown_hours(db),
        "has_reminder_pattern": bool(_setting_value(db, "sms_pattern_credit_reminder")),
        "today": jalali_str(datetime.now(timezone.utc), with_time=False),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "sent": request.query_params.get("sent", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


def _setting_value(db, key: str) -> str:
    row = db.query(Settings).filter(Settings.key == key).first()
    return (row.value or "") if row else ""


@router.get("/collections", response_class=HTMLResponse)
async def admin_collections(request: Request, db: Session = Depends(get_db)):
    """The aged-receivables dashboard now lives inside /admin/credit.

    Kept as a redirect so old links, bookmarks and the dashboard card keep
    working — one نسیه page to learn instead of two half-pages.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    return RedirectResponse(url="/admin/credit?status=overdue", status_code=303)


@router.get("/credit/{customer_id}", response_class=HTMLResponse)
async def admin_credit_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    unpaid = unpaid_credit_sales(db, customer.id)
    drift = debt_drift(db, customer)
    allowed, reason = credit_reminder_allowed(db, customer)
    # One ageing rule for the page: the same function the list and the report
    # use, so an invoice cannot read «جاری» here and be late over there.
    invoice_status = {}
    overdue_amount = 0
    oldest_overdue = None
    for sale in unpaid:
        key = age_bucket(sale)
        late = days_past_due(sale)
        invoice_status[sale.id] = {
            "bucket": key,
            "label": AGE_BUCKET_LABELS[key],
            "days_late": max(0, late),
            "due": as_utc(sale.credit_due_date),
            "effective": due_effective_at(sale),
        }
        if key != "current":
            overdue_amount += sale_remaining(sale)
            effective = due_effective_at(sale)
            if effective and (oldest_overdue is None or effective < oldest_overdue):
                oldest_overdue = effective

    payments = get_payment_history(db, customer.id)
    reversal_reasons = {}
    payment_ids = [payment.id for payment in payments]
    if payment_ids:
        for reversal in db.query(PaymentReversal).filter(
            PaymentReversal.payment_id.in_(payment_ids)
        ).all():
            reversal_reasons[reversal.payment_id] = reversal

    return templates.TemplateResponse(request, "admin/credit_customer.html", {
        "customer": customer,
        "debt": customer.total_debt or 0,
        "credit_limit": get_credit_limit(db, customer),
        "custom_limit": customer.credit_limit,
        "unpaid_sales": unpaid,
        "payments": payments,
        "reversal_reasons": reversal_reasons,
        "invoice_status": invoice_status,
        "overdue_amount": overdue_amount,
        "oldest_overdue": oldest_overdue,
        "drift": drift,
        "bucket_labels": AGE_BUCKET_LABELS,
        "terms_days": credit_terms_days(db),
        "reminder_ready": allowed,
        "reminder_reason": reason,
        "last_reminder": last_credit_reminder(db, customer.id),
        "has_reminder_pattern": bool(_setting_value(db, "sms_pattern_credit_reminder")),
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
    sale_id: str = Form(""),
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

    target_id = int(sale_id) if str(sale_id or "").strip().isdigit() else None
    debt = customer.total_debt or 0
    if amount_int <= 0:
        return RedirectResponse(url=f"/admin/credit/{customer.id}?err=مبلغ معتبر نیست.", status_code=303)

    invoice = None
    if target_id:
        invoice = db.query(Sale).filter(
            Sale.id == target_id, Sale.customer_id == customer.id
        ).first()
        if invoice is None:
            return RedirectResponse(url=f"/admin/credit/{customer.id}?err=فاکتور پیدا نشد.", status_code=303)
        owed = sale_remaining(invoice)
        if owed <= 0:
            return RedirectResponse(url=f"/admin/credit/{customer.id}?err=این فاکتور تسویه شده است.", status_code=303)
        if amount_int > owed:
            return RedirectResponse(
                url=(f"/admin/credit/{customer.id}?err="
                     "مبلغ بیشتر از باقی‌مانده این فاکتور است؛ برای پرداخت گردشده از فرم دریافت کلی استفاده کنید."),
                status_code=303,
            )

    applied = apply_customer_payment(
        db,
        customer,
        amount_int if target_id else min(amount_int, debt),
        method=method,
        note=note,
        sale_id=target_id,
        operator_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
    )
    db.commit()
    if applied > 0:
        target_note = f" (فاکتور #{target_id})" if target_id else ""
        log_action(db, "credit_payment", f"دریافت {applied:,} از {customer.phone}{target_note}", request=request, target_type="customer", target_id=customer.id, after={"amount": applied, "method": method, "sale_id": target_id})
        return RedirectResponse(
            url=f"/admin/credit/{customer.id}?msg={applied:,} تومان ثبت شد.", status_code=303,
        )
    return RedirectResponse(url=f"/admin/credit/{customer.id}?err=بدهی‌ای برای تسویه وجود ندارد.", status_code=303)


@router.post("/credit/{customer_id}/remind", response_class=HTMLResponse)
async def admin_credit_remind(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Send the owner's reminder pattern to one debtor, by hand.

    A debt reminder is transactional — it is about the customer's own balance —
    so it does not depend on marketing consent, but it is never automatic: the
    cool-down refuses a second one too soon, and every send is logged.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(url="/admin/credit?err=مشتری یافت نشد.", status_code=303)
    if not customer.phone:
        return RedirectResponse(url=f"/admin/credit/{customer.id}?err=شماره موبایل ثبت نشده است.", status_code=303)

    allowed, reason = credit_reminder_allowed(db, customer)
    if not allowed:
        return RedirectResponse(url=f"/admin/credit/{customer.id}?err={quote_plus(reason)}", status_code=303)

    # Back to the customer, not the list: this was one deliberate send and the
    # cool-down state is now part of their page.
    return await _send_credit_reminders(
        request, db, guard, [customer], back=f"/admin/credit/{customer.id}",
    )


@router.post("/credit/remind-overdue", response_class=HTMLResponse)
async def admin_credit_remind_overdue(request: Request, db: Session = Depends(get_db)):
    """Remind every overdue debtor, in one deliberate action.

    Capped like a campaign so a click can never turn into a blast, and each
    customer still has to pass their own cool-down.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    rows = build_debt_rows(db)
    candidates = [row["customer"] for row in rows if row["overdue_amount"] > 0]
    allowed_customers = []
    skipped = 0
    for customer in candidates:
        allowed, _reason = credit_reminder_allowed(db, customer)
        if allowed and customer.phone:
            allowed_customers.append(customer)
        else:
            skipped += 1

    limit = max(1, get_setting_int(db, "campaign_sms_limit", 100))
    queued = allowed_customers[:limit]
    not_sent = len(allowed_customers) - len(queued) + skipped
    return await _send_credit_reminders(
        request, db, guard, queued,
        extra=f"&skipped={not_sent}" if not_sent else "",
    )


async def _send_credit_reminders(request: Request, db, guard, customers: list,
                                 extra: str = "", back: str = "/admin/credit"):
    """Shared send path for the single and bulk reminder actions."""
    sent = 0
    failed = 0
    for customer in customers:
        invoices = unpaid_credit_sales(db, customer.id)
        amount = sum(sale_remaining(sale) for sale in invoices)
        due = None
        for sale in invoices:
            candidate = sale.credit_due_date
            if candidate is None:
                continue
            if due is None or candidate < due:
                due = candidate
        attributes = credit_reminder_vars(customer, amount, due)
        job = await queue_credit_reminder_sms(customer.phone, attributes, db, customer=customer)
        if job is not None:
            mark_credit_reminder_sent(db, customer.id)
            sent += 1
        else:
            failed += 1
    db.commit()

    message = f"یادآوری برای {sent} مشتری در صف ارسال قرار گرفت."
    if failed:
        message += " متن پیامک یادآوری تنظیم نشده است."
    log_action(
        db, "credit_reminder", message, request=request, target_type="customer",
        after={"sent": sent, "failed": failed},
    )
    return RedirectResponse(
        url=f"{back}?msg={quote_plus(message)}{extra}", status_code=303,
    )


@router.post("/credit/assign-due-dates", response_class=HTMLResponse)
async def admin_assign_due_dates(request: Request, db: Session = Depends(get_db)):
    """Opt-in: give the open invoices a سررسید from the store's terms.

    Existing invoices are deliberately left untouched by the migration, so
    ageing only changes when the owner asks for it — here.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    terms = credit_terms_days(db)
    if terms <= 0:
        return RedirectResponse(
            url="/admin/credit?err=مهلت پرداخت صفر است؛ ابتدا در تنظیمات یک مهلت تعیین کنید.",
            status_code=303,
        )
    updated = assign_due_dates(db, terms)
    db.commit()
    log_action(db, "credit_due_dates", f"ثبت سررسید برای {updated} فاکتور باز", request=request,
               after={"invoices": updated, "terms_days": terms})
    return RedirectResponse(
        url=f"/admin/credit?msg={quote_plus(f'سررسید برای {updated} فاکتور باز ثبت شد.')}",
        status_code=303,
    )


@router.get("/credit/{customer_id}/statement", response_class=HTMLResponse)
async def admin_credit_statement(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """A printable صورتحساب: invoices, receipts and reversals, one running balance."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    # The canonical reader, not parse_jalali_input: an ISO range (`2026-09-12`)
    # would otherwise be read as a Jalali year and land the window in 2647.
    start = parse_form_date(request.query_params.get("start_date", ""))
    end = parse_form_date_end(request.query_params.get("end_date", ""))

    sales = db.query(Sale).filter(
        Sale.customer_id == customer.id,
        Sale.payment_method == "credit",
    ).order_by(Sale.created_at.asc(), Sale.id.asc()).all()
    payments = db.query(Payment).filter(
        Payment.customer_id == customer.id,
    ).order_by(Payment.created_at.asc(), Payment.id.asc()).all()

    entries = []
    for sale in sales:
        entries.append({
            "kind": "invoice",
            "at": sale.created_at,
            "reference": f"#{sale.id}",
            "detail": "فاکتور نسیه",
            "debit": sale.final_amount or 0,
            "credit": 0,
            "link": f"/sales/invoice/{sale.id}",
        })
    for payment in payments:
        reversed_later = payment.reversed_at is not None
        entries.append({
            "kind": "reversal" if reversed_later else "receipt",
            "at": payment.reversed_at or payment.created_at,
            "reference": f"#{payment.id}",
            "detail": ("برگشت دریافت" if reversed_later else "دریافت") +
                      (f" — {payment.note}" if payment.note else ""),
            "debit": 0,
            "credit": payment.amount or 0,
            "link": None,
        })
    entries.sort(key=lambda entry: as_utc(entry["at"]) or datetime.now(timezone.utc))

    opening = 0
    rows = []
    balance = 0
    for entry in entries:
        # SQLite hands datetimes back naive; the range bounds are aware.
        at = as_utc(entry["at"])
        after = bool(end and at and at > end)
        before = bool(start and at and at < start)
        delta = entry["debit"] - entry["credit"]
        if before:
            opening += delta
            continue
        if after:
            continue
        balance += delta
        rows.append({**entry, "balance": balance})

    in_range_debit = sum(row["debit"] for row in rows)
    in_range_credit = sum(row["credit"] for row in rows)
    return templates.TemplateResponse(request, "admin/credit_statement.html", {
        "customer": customer,
        "rows": rows,
        "opening": opening,
        "closing": opening + in_range_debit - in_range_credit,
        "total_debit": in_range_debit,
        "total_credit": in_range_credit,
        "debt": customer.total_debt or 0,
        "start": request.query_params.get("start_date", ""),
        "end": request.query_params.get("end_date", ""),
        "today": jalali_str(datetime.now(timezone.utc), with_time=False),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


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
async def admin_payment_reverse(
    payment_id: int,
    request: Request,
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    """Record a reversal while retaining the original payment record.

    The reason is required and lands in `payment_reversals.reason`, which is an
    immutable audit row — it used to be filled with the English constant
    "Payment reversal" because nothing asked.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/admin/credit", status_code=303)
    customer_id = payment.customer_id
    reason = (reason or "").strip()
    if not reason:
        return RedirectResponse(
            url=f"/admin/credit/{customer_id}?err=" + quote_plus("علت برگشت را بنویسید؛ این دلیل در دفتر تغییرات می‌ماند."),
            status_code=303,
        )
    reversed_amount = reverse_payment(
        db,
        payment,
        operator_id=guard.id,
        reason=reason,
        request_id=request.headers.get("X-Request-ID"),
    )
    db.commit()
    db.expire_all()
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    log_action(db, "payment_reverse", f"برگشت دریافت {reversed_amount:,}", request=request, target_type="payment", target_id=payment_id, after={"reversed_amount": reversed_amount, "operator_user_id": guard.id, "reason": reason})
    return RedirectResponse(
        url=f"/admin/credit/{customer_id}?msg={reversed_amount:,} تومان برگشت ثبت شد.",
        status_code=303,
    )


@router.post("/payments/{payment_id}/delete", response_class=HTMLResponse)
async def admin_payment_delete_compat(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """The old path, kept working: a client that posts no reason still reverses,
    and the audit row says which route it came from."""
    return await admin_payment_reverse(
        payment_id, request, reason="برگشت دریافت (مسیر قدیمی)", db=db,
    )


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

PURCHASE_PAGE_SIZE = 20
PURCHASE_STATUSES = {
    "all": "همه",
    "draft": "پیش‌نویس",
    "unpaid": "پرداخت‌نشده",
    "partial": "پرداخت جزئی",
    "paid": "تسویه‌شده",
    "overdue": "سررسیدگذشته",
    "reversed": "برگشت‌خورده",
}



def _purchase_form_date(value) -> datetime | None:
    """Persian (۱۴۰۵/۰۶/۲۱) or ISO date from a form → aware UTC datetime."""
    value = str(value or "").strip()
    if not value:
        return None
    if len(value) >= 10 and value[:4].isdigit() and int(value[:4]) >= 1900:
        try:
            return datetime.fromisoformat(value[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return parse_jalali_input(value)


def _purchase_form_date_end(value) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    if len(value) >= 10 and value[:4].isdigit() and int(value[:4]) >= 1900:
        try:
            return datetime.fromisoformat(value[:10]).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        except ValueError:
            return None
    return parse_jalali_input_end(value)


def _purchase_money(value) -> int:
    """Tolerant money reader: Persian digits and thousands separators allowed."""
    try:
        cleaned = to_english_digits(str(value or "").replace(",", "").strip() or "0")
        return max(0, int(float(cleaned)))
    except (TypeError, ValueError):
        return 0


def _record_purchase_payment(db, purchase, amount: int, note: str, guard, request: Request):
    """Write one supplier payment against a purchase and refresh its paid cache.

    Money paid to a supplier always leaves the business the same way, so these
    are recorded as ``cash`` (the only value the cash register counts) and tied
    to the open cash session. Cash-vs-card is a distinction that only matters
    for the shop's own checks, not for paying a wholesaler.
    """
    open_session = db.query(CashSession).filter(CashSession.status == "open") \
        .order_by(CashSession.opened_at.desc()).first()
    payment = SupplierPayment(
        supplier_id=purchase.supplier_id,
        purchase_id=purchase.id,
        amount=amount,
        due_date=purchase.due_date,
        operator_user_id=guard.id,
        note=(note or "").strip() or None,
        method="cash",
        cash_session_id=open_session.id if open_session else None,
    )
    db.add(payment)
    db.flush()
    append_event(
        db,
        "SupplierPaymentRecorded",
        "supplier_payment",
        payment.id,
        idempotency_key=f"supplier-payment:{payment.id}:recorded",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "supplier_id": payment.supplier_id,
            "purchase_id": payment.purchase_id,
            "amount": payment.amount,
            "method": payment.method,
            "cash_session_id": payment.cash_session_id,
        },
    )
    refresh_purchase_amount_paid(db, purchase)
    return payment


def _purchase_items_from_form(form, db, supplier_pk: int | None):
    """Read an invoice's product lines out of a posted form.

    Quantity and unit cost are deliberately optional: the chosen product already
    knows both, so a line with no numbers means "one unit at its current cost
    basis". Returns ``(items, error)`` where each item is a
    ``(variant, quantity, unit_cost)`` triple.
    """
    indices = set()
    for key in form.keys():
        if key.startswith("purchase_variant_"):
            try:
                indices.add(int(key.split("_")[-1]))
            except ValueError:
                pass

    items = []
    merged = {}
    for idx in sorted(indices):
        try:
            variant_id = int(str(form.get(f"purchase_variant_{idx}", "") or ""))
        except (TypeError, ValueError):
            continue
        if variant_id <= 0:
            continue
        if variant_id in merged:
            # The same product on two rows is one line: the quantities add up.
            try:
                extra = int(to_english_digits(
                    str(form.get(f"purchase_qty_{idx}", "") or "").replace(",", "").strip() or "1"
                ))
            except ValueError:
                extra = 1
            merged[variant_id][1] += max(1, extra)
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue
        # One invoice covers one supplier's goods. Products with no supplier
        # keep working so pre-existing catalogue data is not blocked.
        product = variant.product
        if product is not None and supplier_pk and product.supplier_id \
                and product.supplier_id != supplier_pk:
            return None, f"کالای {product.name} به تأمین‌کننده دیگری تعلق دارد."
        raw_qty = str(form.get(f"purchase_qty_{idx}", "") or "").strip()
        raw_cost = str(form.get(f"purchase_cost_{idx}", "") or "").strip()
        quantity = (_purchase_money(raw_qty) or 1) if raw_qty else 1
        unit_cost = _purchase_money(raw_cost) if raw_cost else (variant.cost_price or 0)
        entry = [variant, max(1, quantity), max(0, unit_cost)]
        merged[variant_id] = entry
        items.append(entry)
    return [(variant, quantity, unit_cost) for variant, quantity, unit_cost in items], None


def _purchase_items_subtotal(items) -> int:
    return sum(quantity * unit_cost for _, quantity, unit_cost in items)


def _replace_purchase_items(db, purchase, items) -> None:
    """Rewrite a purchase's lines (only ever called on a draft)."""
    db.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id) \
        .delete(synchronize_session=False)
    db.flush()
    for variant, quantity, unit_cost in items:
        db.add(PurchaseItem(
            purchase_id=purchase.id,
            variant_id=variant.id,
            product_id=variant.product_id,
            quantity=quantity,
            unit_cost=unit_cost,
        ))
    db.flush()


def _purchase_draft_lines(purchase) -> list[dict]:
    """The draft form's rows, so reopening it shows what is already on it."""
    lines = []
    for item in purchase.items:
        variant = item.variant
        lines.append({
            "variant_id": item.variant_id,
            "label": variant.display_name if variant else "کالای حذف‌شده",
            "barcode": (variant.barcode or "") if variant else "",
            "supplier_id": (variant.product.supplier_id or 0) if variant and variant.product else 0,
            "cost": (variant.cost_price or 0) if variant else 0,
            "stock": (variant.stock_quantity or 0) if variant else 0,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
        })
    return lines


def _purchase_paid_subquery(db):
    """Live settled total per purchase, plus whether any payment row exists."""
    live_sum = func.coalesce(func.sum(
        case((SupplierPayment.reversed_at.is_(None), SupplierPayment.amount), else_=0)
    ), 0)
    sub = db.query(
        SupplierPayment.purchase_id.label("purchase_id"),
        live_sum.label("paid"),
        func.count(SupplierPayment.id).label("payment_rows"),
    ).filter(SupplierPayment.purchase_id.isnot(None)) \
        .group_by(SupplierPayment.purchase_id).subquery()
    return sub


def _purchase_with_lines(db, purchase_id: int):
    """One purchase with the lines, products and supplier the detail pages need."""
    return db.query(Purchase).options(
        joinedload(Purchase.supplier),
        joinedload(Purchase.items).joinedload(PurchaseItem.variant),
        joinedload(Purchase.items).joinedload(PurchaseItem.product),
    ).filter(Purchase.id == purchase_id).first()


def _purchase_lines(purchase) -> list[dict]:
    """Per-line amounts including each line's share of the shipping cost."""
    items = purchase.items or []
    items_subtotal = sum((item.unit_cost or 0) * item.quantity for item in items)
    extra_cost = purchase.extra_cost or 0
    lines = []
    for item in items:
        unit_cost = item.unit_cost or 0
        landed = purchase_landed_unit_cost(
            unit_cost, item.quantity, items_subtotal, extra_cost, purchase.extra_cost_in_landed,
        )
        lines.append({
            "item": item,
            "unit_cost": unit_cost,
            "landed_unit_cost": landed,
            "line_total": unit_cost * item.quantity,
            "landed_line_total": landed * item.quantity,
            "extra_share": (landed - unit_cost) * item.quantity,
        })
    return lines


@router.get("/purchases", response_class=HTMLResponse)
async def admin_purchases(
    request: Request,
    supplier_id: str = "",
    status: str = "all",
    q: str = "",
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    status = status if status in PURCHASE_STATUSES else "all"
    page = max(1, page)
    now = datetime.now(timezone.utc)

    paid_sq = _purchase_paid_subquery(db)
    paid_expr = func.coalesce(paid_sq.c.paid, 0)
    total_expr = func.coalesce(Purchase.total_cost, 0)
    remaining_expr = total_expr - paid_expr

    query = db.query(Purchase, paid_expr.label("paid"), paid_sq.c.payment_rows) \
        .outerjoin(paid_sq, paid_sq.c.purchase_id == Purchase.id)

    search = (q or "").strip()
    if supplier_id.isdigit():
        query = query.filter(Purchase.supplier_id == int(supplier_id))
    if search:
        query = query.outerjoin(Supplier, Purchase.supplier_id == Supplier.id)
        digits = search.lstrip("#").strip()
        if digits.isdigit():
            query = query.filter(Purchase.id == int(digits))
        else:
            query = query.filter(or_(
                Purchase.note.ilike(f"%{search}%"),
                Supplier.name.ilike(f"%{search}%"),
            ))
    start = _purchase_form_date(start_date)
    end = _purchase_form_date_end(end_date)
    if start:
        query = query.filter(purchase_effective_column() >= start)
    if end:
        query = query.filter(purchase_effective_column() <= end)

    if status == "draft":
        query = query.filter(Purchase.is_draft == True)
    elif status == "reversed":
        query = query.filter(Purchase.is_reversed == True, Purchase.is_draft == False)
    elif status == "overdue":
        query = query.filter(
            Purchase.is_reversed == False,
            Purchase.is_draft == False,
            Purchase.due_date.isnot(None),
            Purchase.due_date < now,
            remaining_expr > 0,
        )
    elif status == "paid":
        query = query.filter(
            Purchase.is_reversed == False, Purchase.is_draft == False, remaining_expr <= 0,
        )
    elif status == "partial":
        query = query.filter(
            Purchase.is_reversed == False, Purchase.is_draft == False,
            paid_expr > 0, remaining_expr > 0,
        )
    elif status == "unpaid":
        query = query.filter(
            Purchase.is_reversed == False, Purchase.is_draft == False, paid_expr == 0,
        )

    total_count = query.count()
    total_pages = max(1, -(-total_count // PURCHASE_PAGE_SIZE))
    page = min(page, total_pages)
    rows = query.order_by(purchase_effective_column().desc(), Purchase.id.desc()) \
        .offset((page - 1) * PURCHASE_PAGE_SIZE).limit(PURCHASE_PAGE_SIZE).all()

    item_totals = purchase_item_totals(db, [purchase.id for purchase, _, _ in rows])
    purchases = []
    for purchase, paid, payment_rows in rows:
        settled = paid if payment_rows else max(0, purchase.amount_paid or 0)
        counts = item_totals.get(purchase.id, {})
        purchases.append({
            "purchase": purchase,
            "settlement": purchase_settlement(db, purchase, paid=settled, now=now),
            "lines": counts.get("lines", 0),
            "units": counts.get("units", 0),
        })

    month_start, month_end = get_date_range("month")
    products = db.query(Product).filter(Product.is_active == True).order_by(Product.name).all()
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    draft_count = db.query(func.count(Purchase.id)).filter(
        Purchase.is_draft == True, Purchase.is_reversed == False,
    ).scalar() or 0

    return templates.TemplateResponse(request, "admin/purchases.html", {
        "products": products,
        "suppliers": suppliers,
        "purchases": purchases,
        "draft_count": draft_count,
        "edit_purchase": None,
        "edit_lines": [],
        "overview": purchase_overview(db, month_start, month_end, now=now),
        "status": status,
        "statuses": PURCHASE_STATUSES,
        "supplier_filter": supplier_id,
        "search": search,
        "start_date_filter": start_date,
        "end_date_filter": end_date,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "has_filters": bool(search or supplier_id or start_date or end_date or status != "all"),
        "today_jalali": jalali_str(now, with_time=False),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/purchases/add", response_class=HTMLResponse)
async def admin_purchase_add(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    supplier_id = str(form.get("supplier_id", "") or "")
    note = str(form.get("note", "") or "")[:1000]
    extra_cost = _purchase_money(form.get("extra_cost"))
    # The form posts the checkbox plus a "0" companion, so accept whichever
    # truthy value arrives; a client that sends nothing keeps landed costing on.
    landed_values = [str(value).strip().lower() for value in form.getlist("extra_cost_in_landed")]
    extra_in_landed = (
        any(value in {"on", "1", "true", "yes"} for value in landed_values)
        if landed_values else True
    )
    purchase_date = _purchase_form_date(form.get("purchase_date"))
    due_date = _purchase_form_date(form.get("due_date"))
    supplier_pk = int(supplier_id) if supplier_id.isdigit() and int(supplier_id) > 0 else None
    if not supplier_pk:
        return RedirectResponse(
            url="/admin/purchases?err=برای ثبت فاکتور ابتدا تأمین‌کننده را انتخاب کنید.",
            status_code=303,
        )

    items, item_error = _purchase_items_from_form(form, db, supplier_pk)
    if item_error:
        return RedirectResponse(
            url=f"/admin/purchases?err={quote_plus(item_error)}", status_code=303,
        )
    if not items:
        return RedirectResponse(
            url="/admin/purchases?err=حداقل یک محصول را به فاکتور اضافه کنید.", status_code=303,
        )

    purchase = Purchase(
        supplier_id=supplier_pk,
        total_cost=_purchase_items_subtotal(items) + extra_cost,
        note=note.strip() or None,
        extra_cost=extra_cost,
        extra_cost_in_landed=extra_in_landed,
        purchase_date=purchase_date,
        due_date=due_date,
        # Assembly stage: no cost basis, no payable and no cash movement yet.
        is_draft=True,
    )
    db.add(purchase)
    db.flush()
    _replace_purchase_items(db, purchase, items)
    db.commit()
    log_action(
        db, "purchase_draft_add", f"پیش‌نویس فاکتور #{purchase.id}", request=request,
        target_type="purchase", target_id=purchase.id,
        after={"total_cost": purchase.total_cost, "lines": len(items)},
    )
    return RedirectResponse(
        url=(
            f"/admin/purchases/{purchase.id}?msg=پیش‌نویس فاکتور ثبت شد."
            " بازبینی کنید و سپس نهایی‌سازی کنید تا بهای تمام‌شده و بدهی تأمین‌کننده ثبت شود."
        ),
        status_code=303,
    )


@router.get("/purchases/{purchase_id}/edit", response_class=HTMLResponse)
async def admin_purchase_edit(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    """Reopen a draft invoice in the very form that created it."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = _purchase_with_lines(db, purchase_id)
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)
    if not purchase.is_draft:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=فقط پیش‌نویس فاکتور قابل ویرایش است.",
            status_code=303,
        )

    now = datetime.now(timezone.utc)
    month_start, month_end = get_date_range("month")
    return templates.TemplateResponse(request, "admin/purchases.html", {
        "products": db.query(Product).filter(Product.is_active == True)
            .order_by(Product.name).all(),
        "suppliers": db.query(Supplier).order_by(Supplier.name).all(),
        "purchases": [],
        "draft_count": 0,
        "edit_purchase": purchase,
        "edit_lines": _purchase_draft_lines(purchase),
        "overview": purchase_overview(db, month_start, month_end, now=now),
        "status": "all",
        "statuses": PURCHASE_STATUSES,
        "supplier_filter": "",
        "search": "",
        "start_date_filter": "",
        "end_date_filter": "",
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "has_filters": False,
        "today_jalali": jalali_str(now, with_time=False),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/purchases/{purchase_id}/update", response_class=HTMLResponse)
async def admin_purchase_update(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    """Rewrite a draft invoice: its supplier, its details and its products."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)
    if not purchase.is_draft or purchase.is_reversed:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=فقط پیش‌نویس نهایی‌نشده قابل ویرایش است.",
            status_code=303,
        )

    form = await request.form()
    supplier_id = str(form.get("supplier_id", "") or "")
    supplier_pk = int(supplier_id) if supplier_id.isdigit() and int(supplier_id) > 0 else None
    if not supplier_pk:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}/edit?err=برای ثبت فاکتور ابتدا تأمین‌کننده را انتخاب کنید.",
            status_code=303,
        )

    items, item_error = _purchase_items_from_form(form, db, supplier_pk)
    if item_error:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}/edit?err={quote_plus(item_error)}",
            status_code=303,
        )
    if not items:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}/edit?err=حداقل یک محصول را به فاکتور اضافه کنید.",
            status_code=303,
        )

    landed_values = [str(value).strip().lower() for value in form.getlist("extra_cost_in_landed")]
    purchase.supplier_id = supplier_pk
    purchase.note = str(form.get("note", "") or "")[:1000].strip() or None
    purchase.extra_cost = _purchase_money(form.get("extra_cost"))
    purchase.extra_cost_in_landed = (
        any(value in {"on", "1", "true", "yes"} for value in landed_values)
        if landed_values else True
    )
    purchase.purchase_date = _purchase_form_date(form.get("purchase_date"))
    purchase.due_date = _purchase_form_date(form.get("due_date"))
    purchase.total_cost = _purchase_items_subtotal(items) + purchase.extra_cost
    _replace_purchase_items(db, purchase, items)
    db.commit()
    log_action(
        db, "purchase_draft_update", f"ویرایش پیش‌نویس فاکتور #{purchase.id}", request=request,
        target_type="purchase", target_id=purchase.id,
        after={"total_cost": purchase.total_cost, "lines": len(items)},
    )
    return RedirectResponse(
        url=f"/admin/purchases/{purchase.id}?msg=پیش‌نویس فاکتور به‌روز شد.", status_code=303,
    )


@router.post("/purchases/{purchase_id}/finalize", response_class=HTMLResponse)
async def admin_purchase_finalize(
    purchase_id: int,
    request: Request,
    payment_amount: str = Form("0"),
    payment_note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Turn a draft into a real invoice.

    The only place a purchase touches the ledger: the lines' cost basis, the
    payable to the supplier and — optionally — the first payment.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)
    if purchase.is_reversed:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=این خرید برگشت خورده است.",
            status_code=303,
        )
    if not purchase.is_draft:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=این فاکتور قبلاً نهایی شده است.",
            status_code=303,
        )
    if not purchase.supplier_id:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=برای نهایی‌سازی ابتدا تأمین‌کننده را مشخص کنید.",
            status_code=303,
        )

    items = db.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id).all()
    if not items:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=فاکتور بدون قلم کالا قابل نهایی‌سازی نیست.",
            status_code=303,
        )

    total_cost = sum((item.unit_cost or 0) * (item.quantity or 0) for item in items) \
        + (purchase.extra_cost or 0)
    value = _purchase_money(payment_amount)
    if value > total_cost:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=مبلغ پرداختی از مبلغ کل خرید بیشتر است.",
            status_code=303,
        )

    purchase.total_cost = total_cost
    purchase.is_draft = False
    db.flush()
    applied_lines = apply_purchase_cost_basis(
        db, purchase, actor_user_id=guard.id, request_id=request.headers.get("X-Request-ID"),
    )
    if value > 0:
        _record_purchase_payment(db, purchase, value, payment_note, guard, request)

    append_event(
        db,
        "PurchaseRecorded",
        "purchase",
        purchase.id,
        idempotency_key=f"purchase:{purchase.id}:recorded",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "total_cost": purchase.total_cost,
            "supplier_id": purchase.supplier_id,
            "extra_cost": purchase.extra_cost or 0,
            "purchase_date": purchase_effective_at(purchase).isoformat() if purchase_effective_at(purchase) else None,
            "amount_paid": purchase.amount_paid or 0,
        },
    )
    db.commit()
    log_action(
        db, "purchase_finalize", f"نهایی‌سازی فاکتور {total_cost:,} تومان", request=request,
        target_type="purchase", target_id=purchase.id,
        after={"total_cost": total_cost, "applied_lines": applied_lines},
    )
    message = "فاکتور نهایی شد و بهای تمام‌شده به‌روز شد."
    if value > 0:
        message += f" {value:,} تومان پرداخت ثبت شد."
    return RedirectResponse(url=f"/admin/purchases/{purchase.id}?msg={message}", status_code=303)


def _cost_change(payload: str | None):
    """``(old_cost, new_cost)`` when a movement's event recorded a cost change."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    old, new = data.get("old_cost"), data.get("new_cost")
    if old is None or new is None:
        return None
    return (int(old), int(new))


def _movement_event_join(query):
    """Attach the business event written alongside every movement.

    A ledger row and its event share the ``stock-movement:{id}`` idempotency
    key, which is unique and indexed, so this join stays cheap. The event is
    where the acting user and a cost row's before/after values live.
    """
    return query.outerjoin(
        BusinessEvent,
        BusinessEvent.idempotency_key == literal("stock-movement:") + cast(StockMovement.id, String),
    )


@router.get("/inventory-movements", response_class=HTMLResponse)
async def admin_inventory_movements(
    request: Request,
    q: str = "",
    movement_type: str = "all",
    direction: str = "all",
    product_id: str = "",
    variant_id: str = "",
    actor: str = "",
    start_date: str = "",
    end_date: str = "",
    reconcile: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    """Read-only audit view of the append-only inventory ledger.

    Each row shows what the movement did *and* what the balance became, so the
    page can answer "what was the stock then". Balances are derived from the
    ledger itself; a variant whose ledger disagrees with its cached balance is
    flagged rather than hidden.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    movement_type = movement_type if movement_type in MOVEMENT_TYPES else "all"
    direction = direction if direction in MOVEMENT_DIRECTIONS else "all"
    reconcile = reconcile if reconcile in RECONCILE_VIEWS else ""
    search = (q or "").strip()
    page = max(1, page)

    missing_variants = ledger_missing_variants(db)
    mismatched = ledger_mismatched_variants(db)
    reconciliation = {
        "missing_count": len(missing_variants),
        "missing_units": sum(int(v.stock_quantity or 0) for v in missing_variants),
        "missing_value": sum(
            int(v.stock_quantity or 0) * int(v.cost_price or 0) for v in missing_variants
        ),
        "mismatch_count": len(mismatched),
    }

    movements = []
    reconcile_rows = []

    if reconcile == "missing":
        listing = [(variant, 0) for variant in missing_variants]
    elif reconcile == "mismatch":
        listing = list(mismatched)
    else:
        listing = None

    if listing is not None:
        total_count = len(listing)
        total_pages = max(1, -(-total_count // MOVEMENT_PAGE_SIZE))
        page = min(page, total_pages)
        window = listing[(page - 1) * MOVEMENT_PAGE_SIZE: page * MOVEMENT_PAGE_SIZE]
        for variant, ledger_total in window:
            current = int(variant.stock_quantity or 0)
            reconcile_rows.append({
                "variant": variant,
                "product": variant.product,
                "current_stock": current,
                "ledger_total": ledger_total,
                "difference": current - ledger_total,
                "value": current * int(variant.cost_price or 0),
            })
    else:
        query = db.query(StockMovement, BusinessEvent.actor_user_id, BusinessEvent.payload) \
            .outerjoin(ProductVariant, StockMovement.variant_id == ProductVariant.id) \
            .outerjoin(Product, ProductVariant.product_id == Product.id)
        query = _movement_event_join(query)

        if search:
            text_match = or_(
                StockMovement.note.ilike(f"%{search}%"),
                ProductVariant.barcode.ilike(f"%{search}%"),
                ProductVariant.size.ilike(f"%{search}%"),
                ProductVariant.color.ilike(f"%{search}%"),
                Product.name.ilike(f"%{search}%"),
            )
            digits = search.lstrip("#").strip()
            if digits.isdigit():
                # A bare number is usually a movement id, but numeric barcodes
                # are common here, so it matches either instead of dead-ending.
                query = query.filter(or_(StockMovement.id == int(digits), text_match))
            else:
                query = query.filter(text_match)
        if variant_id.isdigit():
            query = query.filter(StockMovement.variant_id == int(variant_id))
        if product_id.isdigit():
            query = query.filter(ProductVariant.product_id == int(product_id))
        if movement_type != "all":
            query = query.filter(StockMovement.movement_type == movement_type)
        if direction == "in":
            query = query.filter(StockMovement.quantity_delta > 0)
        elif direction == "out":
            query = query.filter(StockMovement.quantity_delta < 0)
        if actor.isdigit():
            query = query.filter(BusinessEvent.actor_user_id == int(actor))
        start = _purchase_form_date(start_date)
        end = _purchase_form_date_end(end_date)
        if start:
            query = query.filter(StockMovement.created_at >= start)
        if end:
            query = query.filter(StockMovement.created_at <= end)

        total_count = query.count()
        total_pages = max(1, -(-total_count // MOVEMENT_PAGE_SIZE))
        page = min(page, total_pages)
        window = query.order_by(
            StockMovement.created_at.desc(), StockMovement.id.desc()
        ).options(joinedload(StockMovement.variant)) \
            .offset((page - 1) * MOVEMENT_PAGE_SIZE).limit(MOVEMENT_PAGE_SIZE).all()

        snapshot = ledger_snapshot(db, [movement.variant_id for movement, _, _ in window])
        actor_ids = {actor_id for _, actor_id, _ in window if actor_id}
        actor_names: dict[int, str] = {}
        if actor_ids:
            for user in db.query(StaffUser).filter(StaffUser.id.in_(actor_ids)).all():
                actor_names[user.id] = user.full_name or user.username

        for movement, actor_id, payload in window:
            variant = movement.variant
            current_stock = int(variant.stock_quantity or 0) if variant else 0
            ledger_total = snapshot["totals"].get(movement.variant_id, 0)
            movements.append({
                "movement": movement,
                "product": variant.product if variant else None,
                "direction": movement_direction(movement.quantity_delta),
                "type_label": movement_type_label(movement.movement_type),
                "is_legacy": movement.movement_type in LEGACY_MOVEMENT_TYPES,
                "balance_after": snapshot["by_movement"].get(movement.id),
                "ledger_total": ledger_total,
                "current_stock": current_stock,
                "balanced": ledger_total == current_stock,
                "actor_name": actor_names.get(actor_id),
                "cost_change": _cost_change(payload),
            })

    products = db.query(Product).filter(Product.is_active == True).order_by(Product.name).all()
    actor_options = db.query(StaffUser).join(
        BusinessEvent, BusinessEvent.actor_user_id == StaffUser.id
    ).filter(BusinessEvent.idempotency_key.like("stock-movement:%")).distinct().all()

    return templates.TemplateResponse(request, "admin/inventory_movements.html", {
        "movements": movements,
        "reconcile_rows": reconcile_rows,
        "reconcile": reconcile,
        "reconcile_label": RECONCILE_VIEWS.get(reconcile, ""),
        "reconciliation": reconciliation,
        "movement_labels": MOVEMENT_LABELS,
        "directions": MOVEMENT_DIRECTIONS,
        "products": products,
        "actor_options": [(u.id, u.full_name or u.username) for u in actor_options],
        "movement_type_filter": movement_type,
        "direction_filter": direction,
        "product_filter": product_id,
        "variant_filter": variant_id,
        "actor_filter": actor,
        "search": search,
        "start_date_filter": start_date,
        "end_date_filter": end_date,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "page_size": MOVEMENT_PAGE_SIZE,
        "has_filters": bool(
            search or movement_type != "all" or direction != "all" or product_id
            or variant_id or actor or start_date or end_date
        ),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/inventory-movements/reconcile", response_class=HTMLResponse)
async def admin_inventory_movements_reconcile(request: Request, db: Session = Depends(get_db)):
    """Explain stock that predates the ledger with one opening row per variant.

    Deliberately opt-in rather than a startup migration: it writes to live data.
    It never touches ``stock_quantity`` — those units are already on the shelf;
    the ledger simply had no row explaining them.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    recorded = 0
    for variant in ledger_missing_variants(db):
        if record_ledger_opening(db, variant, actor_user_id=getattr(guard, "id", None)):
            recorded += 1
    db.commit()
    log_action(
        db, "inventory_ledger_opening", f"ثبت موجودی اولیه دفتر برای {recorded} تنوع",
        request=request,
    )
    if not recorded:
        return RedirectResponse(
            url="/admin/inventory-movements?err=تنوعی برای ثبت باقی نمانده است.", status_code=303,
        )
    return RedirectResponse(
        url=f"/admin/inventory-movements?msg=موجودی اولیه {recorded} تنوع در دفتر ثبت شد.",
        status_code=303,
    )


@router.post("/purchases/{purchase_id}/delete", response_class=HTMLResponse)
async def admin_purchase_delete(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    """Reverse a purchase without deleting its historical record.

    A purchase only ever recorded money and cost — it never moved stock — so
    reversing it unlinks its payments and puts the cost basis back, and needs no
    stock lock.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/admin/purchases", status_code=303)
    if purchase.is_reversed:
        return RedirectResponse(url="/admin/purchases?err=این خرید قبلاً برگشت خورده است.", status_code=303)

    if purchase.is_draft:
        # A draft never became a business fact — it holds no cost basis, no
        # payable and no payments — so discarding it simply removes it.
        line_count = db.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id).count()
        db.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id) \
            .delete(synchronize_session=False)
        db.delete(purchase)
        db.commit()
        log_action(
            db, "purchase_draft_discard", f"حذف پیش‌نویس فاکتور #{purchase_id}", request=request,
            target_type="purchase", target_id=purchase_id, before={"lines": line_count},
        )
        return RedirectResponse(url="/admin/purchases?msg=پیش‌نویس فاکتور حذف شد.", status_code=303)

    # Mark first so cost recomputation ignores this purchase while preserving
    # the purchase and its items as immutable historical evidence.
    purchase.is_reversed = True
    purchase.reversed_at = datetime.now(timezone.utc)
    db.flush()

    # Money already handed to the supplier stays real: the payments are simply
    # detached from the reversed purchase instead of being deleted with it.
    unlinked_payments = 0
    for payment in db.query(SupplierPayment).filter(SupplierPayment.purchase_id == purchase.id).all():
        payment.purchase_id = None
        unlinked_payments += 1
    refresh_purchase_amount_paid(db, purchase)

    for item in purchase.items:
        if not item.variant_id:
            continue
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        if not variant:
            continue
        # Stock is untouched by design; only the cost basis goes back.
        previous_cost = variant.cost_price or 0
        restore_cost_after_purchase_reversal(db, variant, item.prev_cost_price)
        record_cost_adjustment(
            db,
            variant,
            previous_cost,
            variant.cost_price or 0,
            note=f"بازگردانی بهای تمام‌شده پس از برگشت خرید #{purchase.id}",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
            purchase_id=purchase.id,
        )

    append_event(
        db,
        "PurchaseReversed",
        "purchase",
        purchase.id,
        idempotency_key=f"purchase:{purchase.id}:reversed",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={"total_cost": purchase.total_cost, "unlinked_payments": unlinked_payments},
        occurred_at=purchase.reversed_at,
    )
    db.commit()
    log_action(db, "purchase_reverse", f"برگشت خرید #{purchase_id}", request=request, target_type="purchase", target_id=purchase_id, after={"reversed": True})
    message = "خرید برگشت داده شد و بهای تمام‌شده بازگردانی شد."
    if unlinked_payments:
        message += f" {unlinked_payments} پرداخت مرتبط آزاد شد و به عنوان بدهی تأمین‌کننده باقی ماند."
    return RedirectResponse(url=f"/admin/purchases?msg={message}", status_code=303)


@router.get("/purchases/{purchase_id}", response_class=HTMLResponse)
async def admin_purchase_detail(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = _purchase_with_lines(db, purchase_id)
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)

    now = datetime.now(timezone.utc)
    return templates.TemplateResponse(request, "admin/purchases_detail.html", {
        "purchase": purchase,
        "settlement": purchase_settlement(db, purchase, now=now),
        "lines": _purchase_lines(purchase),
        "payments": db.query(SupplierPayment).filter(SupplierPayment.purchase_id == purchase.id)
            .order_by(SupplierPayment.created_at.desc(), SupplierPayment.id.desc()).all(),
        "movements": db.query(StockMovement).filter(StockMovement.purchase_id == purchase.id)
            .order_by(StockMovement.created_at.asc(), StockMovement.id.asc()).all(),
        "invoice_total": purchase.total_cost or 0,
        "items_subtotal": sum((i.unit_cost or 0) * i.quantity for i in purchase.items),
        "units": sum(i.quantity for i in purchase.items),
        "now": now,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/purchases/{purchase_id}/payment", response_class=HTMLResponse)
async def admin_purchase_payment(
    purchase_id: int,
    request: Request,
    amount: str = Form("0"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)
    if purchase.is_reversed:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=این خرید برگشت خورده و قابل پرداخت نیست.",
            status_code=303,
        )
    if purchase.is_draft:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=این فاکتور هنوز نهایی نشده است؛ ابتدا آن را نهایی کنید.",
            status_code=303,
        )
    if not purchase.supplier_id:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=برای ثبت پرداخت ابتدا تأمین‌کننده را روی خرید مشخص کنید.",
            status_code=303,
        )

    value = _purchase_money(amount)
    settlement = purchase_settlement(db, purchase)
    if value <= 0 or value > settlement["remaining"]:
        return RedirectResponse(
            url=f"/admin/purchases/{purchase_id}?err=مبلغ پرداخت نامعتبر است یا از مانده خرید بیشتر است.",
            status_code=303,
        )

    payment = _record_purchase_payment(db, purchase, value, note, guard, request)
    db.commit()
    log_action(
        db, "purchase_payment", f"پرداخت {value:,} تومان برای خرید #{purchase.id}",
        request=request, target_type="purchase", target_id=purchase.id,
        after={"amount": value, "payment_id": payment.id},
    )
    return RedirectResponse(url=f"/admin/purchases/{purchase_id}?msg=پرداخت ثبت شد.", status_code=303)


@router.get("/purchases/{purchase_id}/print", response_class=HTMLResponse)
async def admin_purchase_print(purchase_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    purchase = _purchase_with_lines(db, purchase_id)
    if not purchase:
        return RedirectResponse(url="/admin/purchases?err=خرید موردنظر پیدا نشد.", status_code=303)
    return templates.TemplateResponse(request, "admin/purchases_print.html", {
        "purchase": purchase,
        "settlement": purchase_settlement(db, purchase),
        "lines": _purchase_lines(purchase),
        "items_subtotal": sum((i.unit_cost or 0) * i.quantity for i in purchase.items),
        "units": sum(i.quantity for i in purchase.items),
        "payments": db.query(SupplierPayment).filter(SupplierPayment.purchase_id == purchase.id)
            .order_by(SupplierPayment.created_at.asc(), SupplierPayment.id.asc()).all(),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


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
