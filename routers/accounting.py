import csv
import io
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import String, case, cast, func, literal, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (
    BusinessEvent, Customer, Expense, Payment, ProductVariant, Product, Purchase,
    PurchaseItem, Sale, SaleItem, SalaryPayment, Settings, StaffUser, Supplier, StockMovement,
    CashSession, CashSessionEntry, SupplierPayment, FinancialEntry, CheckRecord,
    CheckReminder, PaymentReversal, to_english_digits,
)
from services._common import (
    delta, fmt, check_admin, get_setting_int, jalali_str, page_arg, parse_form_date,
    parse_form_date_end, read_date_window, share,
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
    add_cash_withdrawal, cash_shift_summary, last_counted_balance,
    open_cash_session, reverse_cash_withdrawal,
)
from services.sms import queue_credit_reminder_sms
from services.analytics import KNOWN_PERIODS, UnreadableRange, get_date_range, period_range
from services.security import log_action, require_html_role, role_allows
from services.sorting import parse_sort
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
    record_ledger_opening,
)
from services.reporting import canonical_report, reconciliation_checks
from services.checks import (
    CHECK_AMOUNT_MIN,
    add_reminders,
    check_alert_summary,
    dismiss_reminders,
    get_default_reminder_days,
    normalize_reminder_days,
    parse_amount_rials,
    parse_check_date,
    reminders_enabled,
    trigger_due_reminders,
)
from services.events import append_event

PAYMENT_LABELS = {"card": "💳 کارت", "cash": "💵 نقد", "credit": "📒 نسیه"}
EXPENSE_TYPE_LABELS = {"one_time": "یک‌باره", "monthly": "ماهانه"}
EXPENSE_PAYMENT_LABELS = {"cash": "نقدی", "card": "کارتی"}
EXPENSE_PAGE_SIZE = 50

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

# How the P&L cards name the window they are read against — the same grammar
# the dashboard cards use, adapted to this page's arbitrary ranges.
PREV_LABEL = "همین بازه پیش از آن"


# ── Issued checks ────────────────────────────────────────────────────────────

CHECK_STATUSES = {
    "all": "همه",
    "issued": "صادرشده",
    "overdue": "سررسیدگذشته",
    "upcoming": "دو هفته آینده",
    "paid": "پرداخت‌شده",
    "cancelled": "لغو‌شده",
    "bounced": "برگشتی",
}
CHECKS_PAGE_SIZE = 20
CHECKS_UPCOMING_DAYS = 14


@router.get("/checks", response_class=HTMLResponse)
async def admin_checks(
    request: Request,
    q: str = "",
    status: str = "all",
    supplier_id: str = "",
    bank: str = "",
    start_date: str = "",
    end_date: str = "",
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    triggered_count = trigger_due_reminders(db)
    if triggered_count:
        db.commit()
    now_aware = datetime.now(timezone.utc)
    now_naive = now_aware.replace(tzinfo=None)
    upcoming_end = now_aware + timedelta(days=CHECKS_UPCOMING_DAYS)
    summary = check_alert_summary(db)

    status_filter = status if status in CHECK_STATUSES else "all"
    search = (q or "").strip()
    bank_search = (bank or "").strip()
    page = page_arg(page)

    conds = []
    if search:
        digits = search.lstrip("#").strip()
        if digits.isdigit():
            conds.append(CheckRecord.id == int(digits))
        else:
            like = f"%{search}%"
            conds.append(or_(
                CheckRecord.provider_name.ilike(like),
                CheckRecord.check_number.ilike(like),
                CheckRecord.note.ilike(like),
            ))
    if supplier_id.isdigit():
        conds.append(CheckRecord.supplier_id == int(supplier_id))
    if bank_search:
        conds.append(CheckRecord.bank_name.ilike(f"%{bank_search}%"))
    start = parse_form_date(start_date)
    if start:
        conds.append(CheckRecord.due_at >= start.replace(tzinfo=None))
    end = parse_form_date_end(end_date)
    if end:
        conds.append(CheckRecord.due_at <= end.replace(tzinfo=None))

    base = db.query(CheckRecord).filter(*conds)
    issued = [CheckRecord.status == "issued"]
    stats = {}
    for key, extra in {
        "overdue": issued + [CheckRecord.due_at < now_naive],
        "upcoming": issued + [CheckRecord.due_at >= now_naive,
                              CheckRecord.due_at <= upcoming_end.replace(tzinfo=None)],
        "paid": [CheckRecord.status == "paid"],
    }.items():
        count, amount = base.filter(*extra).with_entities(
            func.count(CheckRecord.id),
            func.coalesce(func.sum(CheckRecord.amount_rials), 0)).one()
        stats[key] = {"count": count or 0, "amount": amount or 0}
    stats["triggered"] = {
        "count": db.query(func.count(CheckReminder.id)).join(
            CheckRecord, CheckRecord.id == CheckReminder.check_id).filter(
            CheckReminder.status == "triggered",
            CheckRecord.status == "issued", *conds).scalar() or 0,
    }

    listing = base
    if status_filter == "issued":
        listing = listing.filter(CheckRecord.status == "issued")
    elif status_filter == "overdue":
        listing = listing.filter(CheckRecord.status == "issued",
                                 CheckRecord.due_at < now_naive)
    elif status_filter == "upcoming":
        listing = listing.filter(
            CheckRecord.status == "issued",
            CheckRecord.due_at >= now_naive,
            CheckRecord.due_at <= upcoming_end.replace(tzinfo=None))
    elif status_filter in {"paid", "cancelled", "bounced"}:
        listing = listing.filter(CheckRecord.status == status_filter)
    total_count = listing.count()
    total_pages = max(1, -(-total_count // CHECKS_PAGE_SIZE))
    page = min(page, total_pages)
    checks = listing.order_by(CheckRecord.due_at.asc(), CheckRecord.id.asc()) \
        .offset((page - 1) * CHECKS_PAGE_SIZE).limit(CHECKS_PAGE_SIZE).all()
    suppliers = db.query(Supplier).order_by(Supplier.name.asc()).all()
    overdue_ids = {check.id for check in summary["overdue"]}
    operator_names = {u.id: (u.full_name or u.username) for u in db.query(StaffUser).filter(
        StaffUser.id.in_([c.operator_user_id for c in checks])).all()} if checks else {}
    days_left_map: dict[int, int | None] = {}
    for check in checks:
        due_at = check.due_at
        if due_at is not None and due_at.tzinfo is not None:
            due_at = due_at.replace(tzinfo=None)
        days_left_map[check.id] = (due_at - now_naive).days if due_at is not None else None
    alert_rows = []
    for reminder in summary["triggered"]:
        due_at = reminder.check.due_at if reminder.check else None
        if due_at is not None and due_at.tzinfo is not None:
            due_at = due_at.replace(tzinfo=None)
        days_left = (due_at - now_naive).days if due_at is not None else None
        alert_rows.append({"reminder": reminder, "days_left": days_left})
    has_filters = bool(search or status_filter != "all" or supplier_id
                        or bank_search or start_date or end_date)
    return templates.TemplateResponse(request, "admin/checks.html", {
        "checks": checks,
        "suppliers": suppliers,
        "summary": summary,
        "stats": stats,
        "overdue_ids": overdue_ids,
        "operator_names": operator_names,
        "days_left_map": days_left_map,
        "alert_rows": alert_rows,
        "is_owner": role_allows(guard.role, "owner"),
        "status_filter": status_filter,
        "statuses": CHECK_STATUSES,
        "search": search,
        "supplier_filter": supplier_id,
        "bank_filter": bank_search,
        "start_date_filter": start_date,
        "end_date_filter": end_date,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "has_filters": has_filters,
        "default_reminder_days": get_default_reminder_days(db),
        "reminders_enabled": reminders_enabled(db),
        # The check form paints its amount floor from the same constant
        # parse_amount_rials refuses below.
        "numeric_rules": {"amount_rials": (CHECK_AMOUNT_MIN, None, "مبلغ چک")},
        "now": now_naive,
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
        # Blank means the check was issued now; a date that was typed and could
        # not be read is refused, because dating a cheque today when the shop
        # wrote something else is a record nobody asked for that looks decided.
        issue_at = parse_check_date(issue_date)
        if issue_at is None:
            if (issue_date or "").strip():
                raise ValueError("تاریخ صدور چک معتبر نیست")
            issue_at = datetime.now(timezone.utc)
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
    check_number_clean = check_number.strip()[:100] or None
    submitted_at = datetime.now(timezone.utc)
    # Double-submit guard: the same operator recording the identical cheque
    # twice within two minutes is a double-click, not two cheques.
    due_naive = due_at.replace(tzinfo=None) if due_at.tzinfo else due_at
    recent = db.query(CheckRecord).filter(
        CheckRecord.provider_name == provider_name[:200],
        CheckRecord.amount_rials == amount,
        CheckRecord.due_at == due_naive,
        CheckRecord.operator_user_id == guard.id,
        CheckRecord.created_at >= (submitted_at - timedelta(minutes=2)).replace(tzinfo=None),
    ).first()
    if recent is not None:
        return RedirectResponse(url="/admin/checks?msg=این چک لحظاتی پیش ثبت شده بود.", status_code=303)
    duplicate_number = None
    if check_number_clean:
        duplicate_number = db.query(CheckRecord).filter(
            CheckRecord.check_number == check_number_clean,
            CheckRecord.status == "issued",
        ).first()
    supplier = None
    if supplier_id.isdigit():
        supplier = db.query(Supplier).filter(Supplier.id == int(supplier_id)).first()

    check = CheckRecord(
        supplier_id=supplier.id if supplier else None,
        provider_name=provider_name[:200],
        check_number=check_number_clean,
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
    if duplicate_number is not None:
        return RedirectResponse(url="/admin/checks?msg=چک ثبت شد. توجه: چک صادرشده دیگری با همین شماره وجود دارد.", status_code=303)
    return RedirectResponse(url="/admin/checks?msg=چک ثبت شد.", status_code=303)


@router.post("/checks/settings", response_class=HTMLResponse)
async def admin_check_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    try:
        days = normalize_reminder_days(str(form.get("reminder_days", "") or "") or get_default_reminder_days(db))
    except ValueError as error:
        return RedirectResponse(url=f"/admin/checks?err={error}", status_code=303)
    # The form posts the checkbox plus a "0" companion, so accept whichever
    # truthy value arrives; without the companion an unchecked box posts
    # nothing and the feature could never be switched off.
    enabled_values = [str(value).strip().lower() for value in form.getlist("enabled")]
    enabled = any(value in {"on", "1", "true", "yes"} for value in enabled_values)
    values = {
        "check_default_reminders": ",".join(str(day) for day in days),
        "check_reminders_enabled": "1" if enabled else "0",
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

    # The range this page actually shows, and the sentence to put on the page
    # when the one that was asked for could not be read.
    window = period_range(period, start_date or None, end_date or None)
    start, end = window.start, window.end
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
        "net_margin": share(report["net_profit"], report["net_sales"]),
        "invoice_count": report["sale_count"],
        # The breakdown the page draws its share bars from: `[]` here made the
        # table state «هزینهای در این بازه ثبت نشده است» under a total that
        # said otherwise, whatever the period held.
        "expense_cats": report["expense_categories"],
    }

    # Every figure on this page is read against the window before it — but a
    # percentage against a base of zero, or from a base so small the ratio is
    # arithmetic, is not a comparison. The dashboard's doctrine says what each
    # impossible ratio becomes instead: the arrival stated as the news it is,
    # or the base itself, so no card here is read against nothing. «همه» is
    # its own honest exception: an open-ended window has no before, and the
    # range beneath the shop's first day is fiction, not a base.
    if window.period != "all":
        span = end - start
        prev_start = start - span
        prev_end = start
        prev = canonical_report(db, prev_start, prev_end)
        pl["delta_revenue"] = delta(report["net_sales"], prev["net_sales"],
                                     label=PREV_LABEL, subject="فروشی")
        pl["delta_gross"] = delta(report["gross_profit"], prev["gross_profit"],
                                  label=PREV_LABEL, subject="سودی")
        pl["delta_expenses"] = delta(report["operating_expenses"], prev["operating_expenses"],
                                     label=PREV_LABEL, lower_is_better=True,
                                     subject="هزینه‌ای")
        pl["delta_net"] = delta(report["net_profit"], prev["net_profit"],
                                label=PREV_LABEL, subject="سودی")
    else:
        pl["delta_revenue"] = pl["delta_gross"] = None
        pl["delta_expenses"] = pl["delta_net"] = None

    debts = debt_totals(db)
    cashbox = get_cashbox(db, start, end, get_opening_balance(db))

    return templates.TemplateResponse(request, "admin/accounting.html", {
        "period": window.period,
        "start_date": start_date,
        "end_date": end_date,
        "range_notice": window.notice,
        "err": request.query_params.get("err", ""),
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

def _export_refused(problem: str) -> RedirectResponse:
    """Send the shop back to the page that owns the range, with the reason on it.

    Not a 400 with a JSON body: the export is a button on a page, and the person
    who pressed it is owed the same Persian sentence everywhere else would use.
    """
    return RedirectResponse(url=f"/admin/accounting?err={quote(problem)}", status_code=303)


@router.get("/accounting/export")
async def admin_accounting_export(
    request: Request,
    kind: str = "sales",
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    q: str = "",
    supplier_id: str = "",
    status: str = "all",
    type: str = "all",
    method: str = "all",
    sort: str = "",
    dir: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    # The buttons sit inside filtered views — the accounting header's period
    # bar, the purchases and expenses pages' own filter forms — so the file
    # answers with the view the owner was looking at, not the whole ledger.
    # KNOWN_PERIODS is the one vocabulary the page links carry.
    if bool(start_date) != bool(end_date):
        # Half a window is not a range: the export used to read one bound and an
        # empty field as «everything», which is a file the shop did not ask for.
        return _export_refused("تاریخ شروع و پایان را با هم وارد کنید.")
    if start_date and end_date:
        # Typed dates are the window, whatever the period bar said — the same
        # precedence the page's own filter uses. And unlike a page, which can
        # degrade an unreadable range to its default *because it says so on
        # screen*, an export is a file the shop keeps: a file of the wrong
        # period is worse than no file, nothing about it looks wrong
        # afterwards. A range that cannot be read is refused, reason named.
        try:
            start, end = get_date_range("custom", start_date, end_date)
        except UnreadableRange as problem:
            return _export_refused(str(problem))
    elif period not in KNOWN_PERIODS:
        return _export_refused(f"بازه «{period}» شناخته نشد؛ فایل ساخته نشد.")
    else:
        window = period_range(period)
        start, end = window.start, window.end
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    if kind == "customers":
        # The figures the screen shows are the invoices', so the file must say
        # the same: one bulk aggregate per column, not the stored counters a
        # drift could make lie against the page they came from.
        from services.customers import _invoice_points, _invoice_totals
        customer_rows = db.query(Customer).order_by(Customer.created_at.desc()).all()
        ids = [c.id for c in customer_rows]
        totals = _invoice_totals(db, ids)
        points = _invoice_points(db, ids)
        rows = [["تلفن", "نام", "نام خانوادگی", "نام فرزند", "سطح", "امتیاز",
                 "تعداد خرید", "مجموع خرید", "بدهی نسیه", "کد معرفی", "تاریخ عضویت"]]
        for c in customer_rows:
            spent, count = totals.get(c.id, (0, 0))
            rows.append([
                c.phone, c.first_name or "", c.last_name or "", c.child_name or "",
                c.tier, points.get(c.id, 0), count,
                spent, c.total_debt or 0, c.referral_code,
                jalali_str(c.created_at, with_time=False),
            ])
        return _csv_response(f"customers_{today}.csv", rows)

    if kind == "purchases":
        # The button sits inside a filtered view, so the file must be that
        # view: the same filter chain the list route reads, not the whole
        # ledger the owner had already narrowed on screen.
        now = datetime.now(timezone.utc)
        paid_sq = _purchase_paid_subquery(db)
        paid_expr = func.coalesce(paid_sq.c.paid, 0)
        remaining_expr = func.coalesce(Purchase.total_cost, 0) - paid_expr
        export_q = db.query(Purchase).outerjoin(paid_sq, paid_sq.c.purchase_id == Purchase.id)
        export_q = _purchase_query_filters(
            export_q, search=(q or "").strip(),
            supplier_id=int(supplier_id) if supplier_id.isdigit() else None,
            supplier_none=(supplier_id == "none"),
            status=status if status in PURCHASE_STATUSES else "all",
            start=start, end=end, paid_expr=paid_expr,
            remaining_expr=remaining_expr, now=now,
        )
        rows = [["شماره", "تاریخ", "تأمین‌کننده", "مبلغ کل", "وضعیت", "توضیح"]]
        _skey, _sdir = parse_sort(request.query_params, {"date": "desc", "total": "desc"}, "date")
        if _skey == "total":
            _porder = Purchase.total_cost.desc() if _sdir == "desc" else Purchase.total_cost.asc()
        else:
            _porder = purchase_effective_column().desc() if _sdir == "desc" \
                else purchase_effective_column().asc()
        for p in export_q.order_by(_porder, Purchase.id.desc()).all():
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
        # The CSV link sits inside the expenses page's filtered view, so the
        # file is that view: the same search, type, method and status clauses
        # the list route reads, over the same window. (Before, it handed over
        # every expense ever recorded whatever the screen said.)
        expense_type = type if type in EXPENSE_TYPE_LABELS else "all"
        payment_method = method if method in EXPENSE_PAYMENT_LABELS else "all"
        status_filter = status if status in {"active", "reversed"} else "all"
        query = db.query(Expense).filter(Expense.created_at.between(start, end))
        search = (q or "").strip()
        if search:
            digits = search.lstrip("#").strip()
            if digits.isdigit():
                query = query.filter(Expense.id == int(digits))
            else:
                like = f"%{search}%"
                query = query.filter(or_(Expense.category.ilike(like), Expense.note.ilike(like)))
        if expense_type != "all":
            query = query.filter(Expense.expense_type == expense_type)
        if payment_method != "all":
            query = query.filter(Expense.payment_method == payment_method)
        if status_filter == "active":
            query = query.filter(Expense.reversed_at.is_(None))
        elif status_filter == "reversed":
            query = query.filter(Expense.reversed_at.isnot(None))
        rows = [["شماره", "تاریخ", "وضعیت", "نوع هزینه", "دسته", "مبلغ", "روش پرداخت", "شیفت", "توضیح"]]
        _ekey, _edir = parse_sort(request.query_params, {"date": "desc", "amount": "desc"}, "date")
        _ecol = Expense.amount if _ekey == "amount" else Expense.created_at
        _eorder = _ecol.desc() if _edir == "desc" else _ecol.asc()
        for e in query.order_by(_eorder, Expense.id.desc()).all():
            rows.append([
                e.id, jalali_str(e.created_at, with_time=False),
                "برگشت‌شده" if e.reversed_at is not None else "فعال",
                EXPENSE_TYPE_LABELS.get(e.expense_type, EXPENSE_TYPE_LABELS["one_time"]),
                e.category or "بدون دسته", e.amount,
                EXPENSE_PAYMENT_LABELS.get(e.payment_method, e.payment_method or "—"),
                f"#{e.cash_session_id}" if e.cash_session_id else "—",
                e.note or "",
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
    page: str = "1",
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

    page = page_arg(page)
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
        # The figures the page prints are worked out here, by the service's own
        # arithmetic (`sale_remaining` clamps a drifted row at 0), so the page
        # cannot disagree with the debt report that uses the same helper.
        invoice_status[sale.id] = {
            "bucket": key,
            "label": AGE_BUCKET_LABELS[key],
            "days_late": max(0, late),
            "due": as_utc(sale.credit_due_date),
            "effective": due_effective_at(sale),
            "paid": sale.credit_paid_amount or 0,
            "remaining": sale_remaining(sale),
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
        "invoice_status": invoice_status,
        "payments": payments,
        "reversal_reasons": reversal_reasons,
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
        amount_int = int(to_english_digits(str(amount or "")).replace(",", "").replace("٬", "").replace(" ", "") or 0)
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
    # A date the shop typed and the app could not read is said out loud: the
    # window used to vanish, leaving a statement of every transaction the
    # customer ever had under a field that still showed the unreadable date.
    start, end, window_notice = read_date_window(
        request.query_params.get("start_date", ""),
        request.query_params.get("end_date", ""),
    )

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
        "range_notice": window_notice,
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
        limit = int(to_english_digits(str(credit_limit or "")).replace(",", "").replace("٬", "").replace(" ", "") or 0)
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
async def admin_suppliers(
    request: Request,
    q: str = "",
    sort: str = "newest",
    dir: str = "desc",
    per_page: str = "10",
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    search = (q or "").strip()
    # Unknown sort keys answer the newest-first list, never an error — the
    # same degrade the purchases ledger uses.
    sort_key = sort if sort in ("name", "debt", "newest") else "newest"
    sort_dir = dir if dir in ("asc", "desc") else "desc"
    per_page_int = int(per_page) if str(per_page).isdigit() and int(per_page) in (10, 25, 50) else 10
    page_int = max(1, int(page)) if str(page).isdigit() else 1
    suppliers = db.query(Supplier).order_by(Supplier.created_at.desc()).all()
    # One definition for every figure on the page: get_supplier_balances's
    # `invoiced` runs the same filter chain (non-reversed, non-draft) the
    # dedicated total_by_supplier query used to re-derive per render. The
    # template reads balances for مجموع خرید, پرداخت‌شده and بدهی alike.
    # The payment rows get a picker of this supplier's open invoices, so a
    # payment can name the invoice it settles. One query for the page; the
    # paid side comes from the same rollup the detail page trusts, so the
    # picker's remaining figure cannot disagree with the clamp's arithmetic.
    open_rows = db.query(Purchase).filter(
        Purchase.is_reversed == False,  # noqa: E712
        Purchase.is_draft == False,  # noqa: E712
        Purchase.total_cost > func.coalesce(Purchase.amount_paid, 0),
    ).order_by(Purchase.created_at.desc()).all()
    rollup = purchase_payment_rollup(db, [p.id for p in open_rows])
    now = datetime.now(timezone.utc)
    open_invoices: dict[int, list[dict]] = {}
    overdue_suppliers: set[int] = set()
    for purchase in open_rows:
        paid = purchase_paid_from_rollup(purchase, rollup.get(purchase.id))
        remaining = max(0, (purchase.total_cost or 0) - paid)
        if remaining <= 0:
            continue
        is_overdue = bool(purchase.due_date and as_utc(purchase.due_date) < now)
        if is_overdue and purchase.supplier_id is not None:
            overdue_suppliers.add(purchase.supplier_id)
        open_invoices.setdefault(purchase.supplier_id, []).append({
            "id": purchase.id,
            "remaining": remaining,
            "label": f"فاکتور #{purchase.id} — مانده {fmt(remaining)} تومان"
                     + (" — سررسیدگذشته" if purchase.due_date and as_utc(purchase.due_date) < now else ""),
        })
    balances = {row["supplier"].id: row for row in get_supplier_balances(db)}
    # Search, sort and paginate over the loaded rows: the supplier book is
    # dozens of rows, not thousands, and the debt sort needs the balances
    # map no SQL ORDER BY can see. Stats above the table keep reading the
    # full book, so the totals stay honest while the rows narrow.
    if search:
        needle = search.casefold()
        digit_needle = to_english_digits(search).strip()
        suppliers = [s for s in suppliers
                     if needle in (s.name or "").casefold()
                     or needle in (s.note or "").casefold()
                     or (digit_needle and digit_needle in (s.phone or ""))]
    reverse = (sort_dir == "desc")
    if sort_key == "name":
        suppliers.sort(key=lambda s: (s.name or "").casefold(), reverse=reverse)
    elif sort_key == "debt":
        suppliers.sort(key=lambda s: balances.get(s.id, {}).get("owed", 0), reverse=reverse)
    elif sort_dir == "asc":
        # ids grow with creation, so id order is creation order.
        suppliers.sort(key=lambda s: (s.id or 0))
    total_count = len(suppliers)
    total_pages = max(1, -(-total_count // per_page_int))
    page_int = min(page_int, total_pages)
    suppliers = suppliers[(page_int - 1) * per_page_int:page_int * per_page_int]
    return templates.TemplateResponse(request, "admin/suppliers.html", {
        "suppliers": suppliers,
        "balances": balances,
        "open_invoices": open_invoices,
        "overdue_suppliers": overdue_suppliers,
        "search": search,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "per_page": per_page_int,
        "per_page_options": (10, 25, 50),
        "page": page_int,
        "total_pages": total_pages,
        "total_count": total_count,
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
    clean_name = name.strip()
    clean_phone = _normalize_supplier_phone(phone)
    if clean_phone is None:
        return RedirectResponse(
            url="/admin/suppliers?err=" + quote_plus("شماره تلفن معتبر نیست — فقط رقم بنویسید."),
            status_code=303)
    supplier = Supplier(name=clean_name, phone=clean_phone or None, note=note.strip() or None)
    db.add(supplier)
    db.flush()
    db.commit()
    log_action(db, "supplier_add", clean_name, request=request, target_type="supplier", target_id=supplier.id, after={"name": clean_name})
    # Warn-but-allow on duplicates: two wholesalers can share a name, but the
    # owner should know the new row is not the only one wearing it.
    message = "تأمین‌کننده اضافه شد."
    if _supplier_name_taken(db, clean_name, ignore_id=supplier.id):
        message += " هم‌نام دیگری با همین نام وجود دارد."
    return RedirectResponse(url="/admin/suppliers?msg=" + quote_plus(message), status_code=303)


@router.post("/suppliers/{supplier_id}/edit", response_class=HTMLResponse)
async def admin_supplier_edit(
    supplier_id: int,
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return RedirectResponse(url="/admin/suppliers?err=تأمین‌کننده یافت نشد.", status_code=303)
    if not name.strip():
        return RedirectResponse(url="/admin/suppliers?err=نام تأمین‌کننده الزامی است.", status_code=303)
    clean_phone = _normalize_supplier_phone(phone)
    if clean_phone is None:
        return RedirectResponse(
            url="/admin/suppliers?err=" + quote_plus("شماره تلفن معتبر نیست — فقط رقم بنویسید."),
            status_code=303)
    before = {"name": supplier.name, "phone": supplier.phone, "note": supplier.note}
    supplier.name = name.strip()
    supplier.phone = clean_phone or None
    supplier.note = note.strip() or None
    db.commit()
    log_action(db, "supplier_edit", supplier.name, request=request, target_type="supplier", target_id=supplier.id,
               before=before, after={"name": supplier.name, "phone": supplier.phone, "note": supplier.note})
    message = f"مشخصات {supplier.name} به‌روز شد."
    if _supplier_name_taken(db, supplier.name, ignore_id=supplier.id):
        message += " هم‌نام دیگری با همین نام وجود دارد."
    return RedirectResponse(url="/admin/suppliers?msg=" + quote_plus(message), status_code=303)


@router.post("/suppliers/{supplier_id}/payment", response_class=HTMLResponse)
async def admin_supplier_payment(supplier_id: int, request: Request, amount: str = Form("0"), purchase_id: str = Form(""), note: str = Form(""), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        # Before the balances, not after: the clamp's `next()` reads
        # `supplier.id`, and an unknown id used to raise AttributeError — a 500
        # where a redirect was meant.
        return RedirectResponse(url="/admin/suppliers?err=تأمین‌کننده یافت نشد.", status_code=303)
    # The same tolerant reader the purchases page pays with: Persian digits and
    # thousands separators are how this shop types money. A raw `int()` read
    # «۵۰۰٬۰۰۰» as 0 and the refusal then claimed the amount exceeded the debt —
    # the message named the wrong reason.
    amount_int = _purchase_money(amount)
    purchase = None
    if purchase_id.isdigit():
        # Drafts excluded: they are not invoices yet, so they are not in the
        # owed figure this payment is clamped against — a payment against one
        # would raise `paid` while `owed` stayed still, and after finalisation
        # the same invoice could be paid twice.
        purchase = db.query(Purchase).filter(
            Purchase.id == int(purchase_id),
            Purchase.supplier_id == supplier.id,
            Purchase.is_reversed == False,  # noqa: E712
            Purchase.is_draft == False,  # noqa: E712
        ).first()
    # The clamp reads the shared owed arithmetic — two aggregate queries, and
    # no purchase objects since `with_purchases` stopped being the default.
    supplier_owed = get_supplier_balances(db)
    balance = next((row["owed"] for row in supplier_owed if row["supplier"].id == supplier.id), 0)
    if amount_int <= 0:
        return RedirectResponse(url="/admin/suppliers?err=" + quote_plus("مبلغ پرداخت معتبر نیست."), status_code=303)
    if amount_int > balance:
        # The cap is named: a figure that refuses without saying what it allows
        # sends the owner hunting through the table for the number.
        return RedirectResponse(
            url="/admin/suppliers?err=" + quote_plus(
                f"مبلغ بیشتر از بدهی تأمین‌کننده است — بدهی {_pd_money(balance)} تومان."),
            status_code=303)
    if purchase is not None:
        # The named invoice's own bound, enforced where the form's script can
        # be bypassed: a payment that names an invoice may not exceed that
        # invoice's remaining — the same figure the picker's label states and
        # the client clamp narrows to. The read is the rollup the picker and
        # the detail page use, so all three say the same remaining.
        invoice_paid = purchase_paid_amount(db, purchase)
        invoice_remaining = max(0, (purchase.total_cost or 0) - invoice_paid)
        if amount_int > invoice_remaining:
            return RedirectResponse(
                url="/admin/suppliers?err=" + quote_plus(
                    f"مبلغ بیشتر از ماندهٔ فاکتور #{purchase.id} است — مانده {_pd_money(invoice_remaining)} تومان."),
                status_code=303)
    open_session = open_cash_session(db)
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
    # The slip is the receipt: the payment lands on its own print page, which
    # also says the shift linkage (in this shift's statement, or outside any
    # shift) instead of a toast that vanishes.
    return RedirectResponse(url=f"/admin/suppliers/payments/{supplier_payment.id}/print", status_code=303)


@router.get("/suppliers/payments/{payment_id}/print", response_class=HTMLResponse)
async def admin_supplier_payment_print(payment_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(SupplierPayment).filter(SupplierPayment.id == payment_id).first()
    if not payment:
        return RedirectResponse(url="/admin/suppliers?err=پرداخت یافت نشد.", status_code=303)
    supplier = db.query(Supplier).filter(Supplier.id == payment.supplier_id).first()
    purchase = db.query(Purchase).filter(Purchase.id == payment.purchase_id).first() if payment.purchase_id else None
    operator = db.query(StaffUser).filter(StaffUser.id == payment.operator_user_id).first() if payment.operator_user_id else None
    return templates.TemplateResponse(request, "admin/supplier_payment_print.html", {
        "payment": payment,
        "supplier": supplier,
        "purchase": purchase,
        "operator_name": (operator.full_name or operator.username) if operator else "—",
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/suppliers/{supplier_id}/delete", response_class=HTMLResponse)
async def admin_supplier_delete(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return RedirectResponse(url="/admin/suppliers", status_code=303)
    # The name is read before anything else: the audit entry is written *now*,
    # not after `db.delete(supplier)` — the old order read `supplier.name` on a
    # deleted object, so log_action's own try/except swallowed the error and a
    # destructive action left no trail.
    name = supplier.name
    payment_count = db.query(func.count(SupplierPayment.id)).filter(
        SupplierPayment.supplier_id == supplier.id).scalar() or 0
    if payment_count:
        # Money history is never destroyed: a supplier the shop has ever paid
        # (or recorded a payment for) stays on the books. The purchases are
        # the other half of their story — naming that count too tells the
        # owner what deleting would have orphaned.
        purchase_count = db.query(func.count(Purchase.id)).filter(
            Purchase.supplier_id == supplier.id).scalar() or 0
        return RedirectResponse(
            url="/admin/suppliers?err=" + quote_plus(
                f"حذف ممکن نیست: {name} {fmt(payment_count)} پرداخت ثبت‌شده دارد."
                + (f" ({fmt(purchase_count)} خرید هم ثبت شده است.)" if purchase_count else "")
                + " تأمین‌کننده‌ای که پولی به او پرداخت شده از تاریخ حذف نمی‌شود."),
            status_code=303)
    for p in db.query(Purchase).filter(Purchase.supplier_id == supplier.id).all():
        p.supplier_id = None
    db.delete(supplier)
    db.commit()
    log_action(db, "supplier_delete", name, request=request, target_type="supplier", target_id=supplier_id, before={"name": name})
    return RedirectResponse(url="/admin/suppliers?msg=" + quote_plus(f"تأمین‌کننده {name} حذف شد."), status_code=303)


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


def _purchase_query_filters(query, *, search, supplier_id, supplier_none, status, start, end, paid_expr, remaining_expr, now):
    """The purchases table's filter chain, in the list route's exact order.

    The list route and the CSV export must read the same rows, and the export
    was reading *every* purchase while the page showed a filtered slice — the
    owner filtered to one month, hit «خروجی CSV» and got the whole ledger.
    One chain, two callers.
    """
    if supplier_id:
        query = query.filter(Purchase.supplier_id == supplier_id)
    elif supplier_none:
        # The orphans: purchases whose supplier was deleted and detached.
        # A named id and the ownerless state are mutually exclusive filters.
        query = query.filter(Purchase.supplier_id.is_(None))
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
    return query



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


def _pd_money(amount: int) -> str:
    """A money figure in the digits and separator this shop reads: «۷۰۰٬۰۰۰».

    `fmt` groups with the ASCII comma; a Persian sentence wants the Persian
    thousands separator. One definition — three messages in the supplier
    payment flow were spelling this composite inline.
    """
    from services._common import _to_persian_digits as _pd
    return _pd(fmt(amount).replace(",", "٬"))


def _normalize_supplier_phone(value) -> str | None:
    """Phone with money-field discipline: Farsi digits become English ones.

    Returns the clean digits, "" when nothing was typed, or None when the
    input cannot be a phone number (letters mixed in, too short, too long).
    Landlines stay valid — the rule is digits of a plausible length, not the
    mobile-only 09/11 shape the customer signup enforces.
    """
    cleaned = (to_english_digits(str(value or ""))
               .replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
               .replace("٬", "").replace(",", "").strip())
    if not cleaned:
        return ""
    if not cleaned.isdigit() or not 8 <= len(cleaned) <= 15:
        return None
    return cleaned


def _supplier_name_taken(db, name: str, ignore_id: int | None = None) -> bool:
    """A same-name supplier already on the books (warn, never block)."""
    query = db.query(Supplier.id).filter(Supplier.name == name)
    if ignore_id is not None:
        query = query.filter(Supplier.id != ignore_id)
    return query.first() is not None


def _purchase_money(value) -> int:
    """Tolerant money reader: Persian digits and thousands separators allowed.

    Both separators — the ASCII comma and the Persian «٬» (U+066C) — plus any
    spaces: this shop types «۵۰۰٬۰۰۰» and the reader must not read it as 0.
    The expenses route spelled this same cleanup inline first; this is the one
    definition both paths now share.
    """
    try:
        cleaned = (to_english_digits(str(value or ""))
                   .replace(",", "").replace("٬", "").replace(" ", "").strip() or "0")
        return max(0, int(float(cleaned)))
    except (TypeError, ValueError):
        return 0


# The cashbox forms' numeric fields, with the bound each route enforces and
# the label it refuses by. One table, two readers: the POST validates against
# it and the forms render their min/max from it, so the browser's attributes
# stay a kindness, not the rule. (The withdrawal's *upper* bound is the register
# balance — a live figure that belongs to no table, and the form deliberately
# does not show it: the count must be a real observation.)
CASHBOX_NUMERIC_RULES = {
    "amount": (1, None, "مبلغ برداشت"),
    "counted": (0, None, "مبلغ شمارش‌شده"),
    "opening": (0, None, "موجودی ابتدای روز"),
}


def _record_purchase_payment(db, purchase, amount: int, note: str, guard, request: Request):
    """Write one supplier payment against a purchase and refresh its paid cache.

    Money paid to a supplier always leaves the business the same way, so these
    are recorded as ``cash`` (the only value the cash register counts) and tied
    to the open cash session. Cash-vs-card is a distinction that only matters
    for the shop's own checks, not for paying a wholesaler.
    """
    open_session = open_cash_session(db)
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

    The receipt only chooses variants: quantity and unit cost always come
    from the variant itself (its recorded stock and cost basis), never from
    posted fields — a crafted row cannot smuggle in new numbers. A variant
    already received by another invoice refuses loudly, naming its row.
    Returns ``(items, error)`` where each item is a
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
    for position, idx in enumerate(sorted(indices), start=1):
        try:
            variant_id = int(str(form.get(f"purchase_variant_{idx}", "") or ""))
        except (TypeError, ValueError):
            continue
        if variant_id <= 0:
            continue
        if variant_id in merged:
            # The same product on two rows is one line: the picker excludes
            # picked variants, so this only answers stale double posts.
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue
        if variant.received_purchase_id:
            return None, f"«{variant.display_name}» در ردیف {position} قبلاً رسید شده است."
        # One invoice covers one supplier's goods. Products with no supplier
        # keep working so pre-existing catalogue data is not blocked.
        product = variant.product
        if product is not None and supplier_pk and product.supplier_id \
                and product.supplier_id != supplier_pk:
            return None, f"کالای {product.name} به تأمین‌کننده دیگری تعلق دارد."
        entry = [variant, variant.stock_quantity or 0, variant.cost_price or 0]
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
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    status = status if status in PURCHASE_STATUSES else "all"
    # A typed or malformed page number degrades to page 1 — a bare 422 JSON is
    # not an answer a shop can read.
    page = max(1, int(page)) if str(page).isdigit() else 1
    now = datetime.now(timezone.utc)

    paid_sq = _purchase_paid_subquery(db)
    paid_expr = func.coalesce(paid_sq.c.paid, 0)
    total_expr = func.coalesce(Purchase.total_cost, 0)
    remaining_expr = total_expr - paid_expr

    query = db.query(Purchase, paid_expr.label("paid"), paid_sq.c.payment_rows) \
        .outerjoin(paid_sq, paid_sq.c.purchase_id == Purchase.id) \
        .options(joinedload(Purchase.supplier))  # the table names the supplier

    search = (q or "").strip()
    start = _purchase_form_date(start_date)
    end = _purchase_form_date_end(end_date)
    supplier_none = (supplier_id == "none")
    query = _purchase_query_filters(
        query, search=search, supplier_id=int(supplier_id) if supplier_id.isdigit() else None,
        supplier_none=supplier_none,
        status=status, start=start, end=end,
        paid_expr=paid_expr, remaining_expr=remaining_expr, now=now,
    )

    total_count = query.count()
    total_pages = max(1, -(-total_count // PURCHASE_PAGE_SIZE))
    page = min(page, total_pages)
    # Ledger sorting: effective date or invoice total. Unknown keys answer the
    # default newest-first list, never an error.
    # Ledger sorting: effective date or invoice total. Unknown keys answer the
    # default newest-first list, never an error.
    sort_key, sort_dir = parse_sort(request.query_params,
                                    {"date": "desc", "total": "desc"}, "date")
    if sort_key == "total":
        primary = total_expr.desc() if sort_dir == "desc" else total_expr.asc()
    else:
        primary = purchase_effective_column().desc() if sort_dir == "desc" \
            else purchase_effective_column().asc()
    rows = query.order_by(primary, Purchase.id.desc()) \
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

    from urllib.parse import urlencode
    sort_base_qs = urlencode({
        "status": status,
        "q": search,
        "supplier_id": supplier_id,
        "start_date": start_date,
        "end_date": end_date,
    })
    filter_qs = f"{sort_base_qs}&sort={sort_key}&dir={sort_dir}" if sort_base_qs else f"sort={sort_key}&dir={sort_dir}"

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
        "filter_qs": filter_qs,
        "sort_base_qs": sort_base_qs,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
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
    # The editing layout draws no KPI row — the draft banner replaces it — so
    # the overview (four aggregate queries) is not computed to be ignored.
    return templates.TemplateResponse(request, "admin/purchases.html", {
        "products": db.query(Product).filter(Product.is_active == True)
            .order_by(Product.name).all(),
        "suppliers": db.query(Supplier).order_by(Supplier.name).all(),
        "purchases": [],
        "draft_count": 0,
        "edit_purchase": purchase,
        "edit_lines": _purchase_draft_lines(purchase),
        "overview": None,
        "status": "all",
        "statuses": PURCHASE_STATUSES,
        "supplier_filter": "",
        "search": "",
        "start_date_filter": "",
        "end_date_filter": "",
        "page": 1,
        "total_pages": 1,
        "total_count": 0,
        "filter_qs": "",
        "sort_base_qs": "",
        "sort_key": "date",
        "sort_dir": "desc",
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
    db: Session = Depends(get_db),
):
    """Turn a draft into a real invoice.

    The receipt files what arrived and books the supplier debt — full credit,
    always. It never rewrites a variant's cost or stock; arrival is stamped
    on each line's variant so it cannot be received twice.
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

    # One arrival, one receipt: a line already stamped by another invoice
    # refuses here, naming itself, instead of being bought twice.
    for item in items:
        variant = item.variant
        if variant is not None and variant.received_purchase_id \
                and variant.received_purchase_id != purchase.id:
            return RedirectResponse(
                url=f"/admin/purchases/{purchase_id}?err=«{variant.display_name}» قبلاً با فاکتور دیگری رسید شده است.",
                status_code=303,
            )

    total_cost = sum((item.unit_cost or 0) * (item.quantity or 0) for item in items) \
        + (purchase.extra_cost or 0)

    purchase.total_cost = total_cost
    purchase.is_draft = False
    for item in items:
        if item.variant is not None:
            item.variant.received_purchase_id = purchase.id
    db.flush()
    applied_lines = apply_purchase_cost_basis(
        db, purchase, actor_user_id=guard.id, request_id=request.headers.get("X-Request-ID"),
    )

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
    message = "فاکتور نهایی شد و کامل نسیه ثبت گردید."
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
    page: str = "1",
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
    # A typed or malformed page number degrades to page 1 — a bare 422 JSON is
    # not an answer a shop can read (the purchases list route's rule too).
    page = max(1, int(page)) if str(page).isdigit() else 1

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

    from urllib.parse import urlencode
    filter_qs = urlencode({
        "q": search,
        "movement_type": movement_type,
        "direction": direction,
        "product_id": product_id,
        "variant_id": variant_id,
        "actor": actor,
        "start_date": start_date,
        "end_date": end_date,
    })

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
        "filter_qs": filter_qs,
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

    A purchase only ever recorded money and evidence — it never moved stock
    and never rewrote a cost — so reversing it unlinks its payments and
    unstamps its variants, and needs no stock lock and no cost restore.
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
        # The arrival is undone: this variant may be received again.
        if variant.received_purchase_id == purchase.id:
            variant.received_purchase_id = None

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
    message = "خرید برگشت داده شد و تنوع‌ها برای رسید دوباره آزاد شدند."
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
async def admin_expenses(
    request: Request,
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    q: str = "",
    type: str = "all",
    method: str = "all",
    status: str = "all",
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    window = period_range(period, start_date or None, end_date or None)
    start, end = window.start, window.end
    search = (q or "").strip()
    expense_type = type if type in EXPENSE_TYPE_LABELS else "all"
    payment_method = method if method in EXPENSE_PAYMENT_LABELS else "all"
    status_filter = status if status in {"active", "reversed"} else "all"
    page = page_arg(page)

    query = db.query(Expense).filter(Expense.created_at.between(start, end))
    if search:
        digits = search.lstrip("#").strip()
        if digits.isdigit():
            query = query.filter(Expense.id == int(digits))
        else:
            like = f"%{search}%"
            query = query.filter(or_(Expense.category.ilike(like), Expense.note.ilike(like)))
    if expense_type != "all":
        query = query.filter(Expense.expense_type == expense_type)
    if payment_method != "all":
        query = query.filter(Expense.payment_method == payment_method)
    if status_filter == "active":
        query = query.filter(Expense.reversed_at.is_(None))
    elif status_filter == "reversed":
        query = query.filter(Expense.reversed_at.isnot(None))

    total = query.filter(Expense.reversed_at.is_(None)).with_entities(
        func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
    one_time_total = query.filter(
        Expense.reversed_at.is_(None), Expense.expense_type == "one_time").with_entities(
        func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
    monthly_total = query.filter(
        Expense.reversed_at.is_(None), Expense.expense_type == "monthly").with_entities(
        func.coalesce(func.sum(Expense.amount), 0)).scalar() or 0
    total_count = query.count()
    total_pages = max(1, -(-total_count // EXPENSE_PAGE_SIZE))
    page = min(page, total_pages)
    sort_key, sort_dir = parse_sort(request.query_params,
                                    {"date": "desc", "amount": "desc"}, "date")
    order_column = Expense.amount if sort_key == "amount" else Expense.created_at
    order = order_column.desc() if sort_dir == "desc" else order_column.asc()
    expenses = query.order_by(order, Expense.id.desc()) \
        .offset((page - 1) * EXPENSE_PAGE_SIZE).limit(EXPENSE_PAGE_SIZE).all()

    ids = [e.id for e in expenses]
    salary_map: dict[int, int] = {}
    if ids:
        for row in db.query(SalaryPayment.expense_id, SalaryPayment.id).filter(
                SalaryPayment.expense_id.in_(ids)).all():
            salary_map[row[0]] = row[1]
    actor_map: dict[int, str] = {}
    if ids:
        events = db.query(BusinessEvent).filter(
            BusinessEvent.event_type == "ExpenseRecorded",
            BusinessEvent.aggregate_id.in_(ids)).all()
        user_ids = {ev.actor_user_id for ev in events if ev.actor_user_id}
        names = {u.id: (u.full_name or u.username) for u in db.query(StaffUser).filter(
            StaffUser.id.in_(list(user_ids))).all()} if user_ids else {}
        for ev in events:
            if ev.aggregate_id is not None and ev.actor_user_id in names:
                actor_map[ev.aggregate_id] = names[ev.actor_user_id]

    has_filters = bool(search or expense_type != "all" or payment_method != "all"
                        or status_filter != "all" or window.period != "month"
                        or start_date or end_date)
    # One urlencode-built string for every link that must land back on this
    # view: pagination, and the CSV export whose file must be the view it
    # sits inside. Hand-concatenating raw values here was how a filter value
    # could break out of a quoted href (the ledger page's fix, before that).
    from urllib.parse import urlencode
    export_qs = urlencode({
        "kind": "expenses", "period": window.period,
        "sort": sort_key, "dir": sort_dir,
        **({"start_date": start_date} if start_date else {}),
        **({"end_date": end_date} if end_date else {}),
        **({"q": search} if search else {}),
        **({"type": expense_type} if expense_type != "all" else {}),
        **({"method": payment_method} if payment_method != "all" else {}),
        **({"status": status_filter} if status_filter != "all" else {}),
    })
    return templates.TemplateResponse(request, "admin/expenses.html", {
        "expenses": expenses,
        "total": total,
        "expense_type_totals": {"one_time": one_time_total, "monthly": monthly_total},
        "expense_type_labels": EXPENSE_TYPE_LABELS,
        "expense_payment_labels": EXPENSE_PAYMENT_LABELS,
        "salary_map": salary_map,
        "actor_map": actor_map,
        "period": window.period,
        "start_date": start_date,
        "end_date": end_date,
        "range_notice": window.notice,
        "search": search,
        "type_filter": expense_type,
        "method_filter": payment_method,
        "status_filter": status_filter,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "has_filters": has_filters,
        "export_qs": export_qs,
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
    payment_method: str = Form("cash"),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        cleaned = to_english_digits(str(amount or "")).replace(",", "").replace("٬", "").replace(" ", "").strip()
        amount_int = int(cleaned) if cleaned else 0
    except (TypeError, ValueError):
        amount_int = 0
    if amount_int <= 0 or amount_int > 999_999_999_999:
        return RedirectResponse(url="/admin/expenses?err=مبلغ معتبر نیست.", status_code=303)
    if expense_type not in EXPENSE_TYPE_LABELS:
        return RedirectResponse(url="/admin/expenses?err=نوع هزینه نامعتبر است.", status_code=303)
    if payment_method not in EXPENSE_PAYMENT_LABELS:
        return RedirectResponse(url="/admin/expenses?err=روش پرداخت نامعتبر است.", status_code=303)
    category_clean = (category or "").strip()[:100] or None
    note_clean = (note or "").strip()[:1000] or None
    request_id = request.headers.get("X-Request-ID")
    if request_id:
        existing = db.query(BusinessEvent).filter(
            BusinessEvent.event_type == "ExpenseRecorded",
            BusinessEvent.request_id == request_id).first()
        if existing is not None:
            return RedirectResponse(url="/admin/expenses?msg=هزینه ثبت شد.", status_code=303)
    open_session = open_cash_session(db)
    expense = Expense(
        amount=amount_int,
        category=category_clean,
        expense_type=expense_type,
        payment_method=payment_method,
        cash_session_id=open_session.id if (open_session and payment_method == "cash") else None,
        note=note_clean,
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
        request_id=request_id,
        payload={
            "amount": expense.amount,
            "category": expense.category,
            "expense_type": expense.expense_type,
            "payment_method": expense.payment_method,
            "cash_session_id": expense.cash_session_id,
        },
    )
    db.commit()
    log_action(db, "expense_add", f"{amount_int:,} تومان ({category_clean or 'بدون دسته'}، {EXPENSE_TYPE_LABELS[expense_type]}، {EXPENSE_PAYMENT_LABELS[payment_method]})", request=request, target_type="expense", target_id=expense.id, after={"amount": amount_int, "category": category_clean, "expense_type": expense_type, "payment_method": payment_method})
    return RedirectResponse(url="/admin/expenses?msg=هزینه ثبت شد.", status_code=303)


@router.post("/expenses/{expense_id}/delete", response_class=HTMLResponse)
async def admin_expense_delete(expense_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if expense is None:
        return RedirectResponse(url="/admin/expenses?err=هزینه یافت نشد.", status_code=303)
    if expense.reversed_at is not None:
        return RedirectResponse(url="/admin/expenses?err=این هزینه قبلاً برگشت داده شده است.", status_code=303)
    payroll_link = db.query(SalaryPayment).filter(SalaryPayment.expense_id == expense.id).first()
    if payroll_link is not None:
        return RedirectResponse(
            url="/admin/expenses?err=این هزینه حقوق است و فقط از پرونده پرسنل قابل پیگیری است.",
            status_code=303,
        )
    form = await request.form()
    raw_reason = str(form.get("reason", "") or "").strip()[:500]
    from services.ledger import reverse_expense_immutably
    entry = reverse_expense_immutably(
        db,
        expense,
        guard.id,
        raw_reason or "ابطال دستی هزینه",
        request_id=request.headers.get("X-Request-ID"),
    )
    if entry is None:
        return RedirectResponse(url="/admin/expenses?err=این هزینه قبلاً برگشت داده شده است.", status_code=303)
    db.commit()
    log_action(db, "expense_reverse", f"ابطال هزینه {expense.amount:,} تومان ({expense.category or 'بدون دسته'})", request=request, target_type="expense", target_id=expense_id, after={"reversed": True, "operator_user_id": guard.id, "reason": raw_reason or None})
    return RedirectResponse(url="/admin/expenses?msg=هزینه برگشت داده شد.", status_code=303)


# ── Cash box (the drawer) ────────────────────────────────────────────────────
#
# The page is the drawer, and the drawer is a shift: opened with a count, closed
# with a count. Everything that adds up to «what should be in here» is kept from
# whoever is about to count it — see ``_blind`` below — because a count typed off
# a figure the register already printed can never disagree with it.

SESSIONS_SHOWN = 20


def _staff_names(db, ids) -> dict:
    """Names for the people on a shift, so the till never says «کاربر #۳»."""
    wanted = {int(i) for i in ids if i}
    if not wanted:
        return {}
    return {user.id: (user.full_name or user.username)
            for user in db.query(StaffUser).filter(StaffUser.id.in_(wanted)).all()}


def _cash_session_row(session, names: dict) -> dict:
    """One shift as the history table reads it, difference already worded."""
    variance = session.variance
    if session.status == "open":
        status_label, variance_label, variance_tone = "باز", "—", None
    elif session.status == "abandoned":
        status_label, variance_label, variance_tone = "بسته‌شده بدون شمارش", "—", None
    else:
        status_label = "بسته"
        if variance is None:
            variance_label, variance_tone = "ثبت نشده", None
        elif variance == 0:
            variance_label, variance_tone = "مطابق", "balanced"
        elif variance < 0:
            variance_label, variance_tone = f"{fmt(abs(variance))} ت کسری", "short"
        else:
            variance_label, variance_tone = f"{fmt(variance)} ت اضافه", "over"
    return {
        "session": session,
        "status_label": status_label,
        "variance_label": variance_label,
        "variance_tone": variance_tone,
        "opener": names.get(session.cashier_user_id) or "کاربر حذف‌شده",
        "closer": names.get(session.manager_user_id) if session.manager_user_id else None,
    }


def _withdrawals_of(summary: dict) -> list:
    return [row for row in summary["movements"] if row["kind"] == "withdrawal"]


@router.get("/cashbox", response_class=HTMLResponse)
async def admin_cashbox(
    request: Request,
    period: str = "today",
    start_date: str = "",
    end_date: str = "",
    history: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard

    verifier = role_allows(guard.role, "manager")
    session = open_cash_session(db)
    suggestion = last_counted_balance(db)

    shift = None
    if session:
        summary = cash_shift_summary(db, session)
        shift = {
            "session": session,
            "opener": _staff_names(db, [session.cashier_user_id]).get(session.cashier_user_id) or "کاربر حذف‌شده",
            "withdrawals": _withdrawals_of(summary),
            "can_close": verifier or session.cashier_user_id == guard.id,
            # SQLite returns the row without a timezone, so the comparison needs
            # the boundary attached to it rather than the other way round.
            "stale": as_utc(session.opened_at) < get_date_range("today")[0],
        }
        # The blind count: the figures that add up to what should be in the
        # drawer reach a manager's page and nobody else's until the shift is
        # closed. The opener still sees the float they put in and the
        # withdrawals they recorded — those are inputs, not the answer.
        if verifier:
            shift["register"] = summary["register"]
            shift["expected"] = summary["register"]["closing"]

    open_shifts = db.query(CashSession).order_by(CashSession.opened_at.desc())
    if not verifier:
        # A cashier reads their own shifts, not the rest of the shop's day.
        open_shifts = open_shifts.filter(CashSession.cashier_user_id == guard.id)
    total_shifts = open_shifts.count()
    listed = open_shifts.all() if history == "all" else open_shifts.limit(SESSIONS_SHOWN).all()
    names = _staff_names(db, [s.cashier_user_id for s in listed] + [s.manager_user_id for s in listed])
    sessions = [_cash_session_row(s, names) for s in listed]

    window = period_range(period, start_date or None, end_date or None)
    report = None
    settings_opening = None
    if verifier:
        start, end = window.start, window.end
        settings_opening = get_opening_balance(db)
        register = get_cashbox(db, start, end, settings_opening)
        # For «همه» there is no float to have started from, so a closing balance
        # would be arithmetic about nothing; the net movement of the range is a
        # figure that means something instead.
        report = {
            "register": register,
            "timeless": window.period == "all",
            "net": register["cash_in"] - register["cash_out"],
        }

    return templates.TemplateResponse(request, "admin/cashbox.html", {
        "period": window.period,
        "start_date": start_date,
        "end_date": end_date,
        "range_notice": window.notice,
        "history": history,
        "verify": verifier,
        "shift": shift,
        "sessions": sessions,
        # Reading «۲۰ از ۲۵» above a list that is showing all of them would make
        # the page lie to the reader about what is in front of them.
        "sessions_shown": total_shifts if history == "all" else min(total_shifts, SESSIONS_SHOWN),
        "sessions_total": total_shifts,
        "report": report,
        "settings_opening": settings_opening,
        # The cashbox forms paint their bounds from the same table the POSTs
        # validate against.
        "numeric_rules": CASHBOX_NUMERIC_RULES,
        "opening_suggestion": suggestion,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "msg_tone": "warning" if request.query_params.get("tone") == "warning" else "success",
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/cashbox/open", response_class=HTMLResponse)
async def admin_cashbox_open(request: Request, opening: str = Form("0"), db: Session = Depends(get_db)):
    """Open the drawer for the person standing at it.

    Anyone signed in may do this — the cashier is who actually counts the float
    — but only one drawer exists at a time, so a shift already running refuses a
    second one by name instead of silently splitting the day in two.
    """
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    alive = open_cash_session(db)
    if alive:
        who = _staff_names(db, [alive.cashier_user_id]).get(alive.cashier_user_id) or "کاربر حذف‌شده"
        return RedirectResponse(
            url=f"/admin/cashbox?err={quote_plus(f'صندوق از طرف {who} باز است؛ تا آن بسته نشود صندوق تازه‌ای باز نمی‌شود.')}",
            status_code=303,
        )
    try:
        opening_int = int(to_english_digits(opening))
    except (TypeError, ValueError):
        opening_int = -1
    if opening_int < CASHBOX_NUMERIC_RULES["opening"][0]:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('موجودی ابتدای صندوق باید عددی صفر یا بیشتر باشد.')}", status_code=303)
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
    try:
        db.commit()
    except IntegrityError:
        # Two people opening at the same moment: the index keeps one, and the
        # loser is told plainly rather than shown an error page.
        db.rollback()
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('همان لحظه یک صندوق دیگر باز شد؛ صفحه را دوباره ببینید.')}", status_code=303)
    log_action(db, "cash_session_open", "باز کردن صندوق", request=request, target_type="cash_session", target_id=session.id, after={"opening_balance": opening_int})
    return RedirectResponse(url=f"/admin/cashbox?msg={quote_plus('صندوق باز شد. حالا فروش نقدی این شیفت شمرده می‌شود.')}", status_code=303)


@router.post("/cashbox/close", response_class=HTMLResponse)
async def admin_cashbox_close(request: Request, counted: str = Form("0"), db: Session = Depends(get_db)):
    """Count the drawer and record the difference.

    The opener may close their own shift; a manager or the owner may close any
    shift, which is what «verifying» means here. What the register expected is
    worked out *after* the count arrives and only then said out loud, so the
    count is a real observation of the drawer rather than a copy of a figure.
    """
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    session = open_cash_session(db)
    if not session:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('صندوقی باز نیست.')}", status_code=303)
    if not (role_allows(guard.role, "manager") or session.cashier_user_id == guard.id):
        raise HTTPException(
            status_code=403,
            detail="این صندوق را کسی دیگر باز کرده است؛ فقط بازکنندهٔ آن یا مدیر می‌تواند ببندد.",
        )
    try:
        counted_int = int(to_english_digits(counted))
    except (TypeError, ValueError):
        counted_int = -1
    if counted_int < CASHBOX_NUMERIC_RULES["counted"][0]:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('مبلغ شمارش‌شده باید عددی صفر یا بیشتر باشد.')}", status_code=303)
    start = session.opened_at
    end = datetime.now(timezone.utc)
    expected = get_cashbox(db, start, end, session.opening_balance, session.id)["closing"]
    # Written down, not merely spoken in the event payload: the shift history and
    # the statement read this column, and until now nothing ever filled it — the
    # register recorded the expected figure and then showed it nowhere.
    session.expected_closing_balance = expected
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
    # Only now is the expected figure spoken: the count is already in.
    if session.variance == 0:
        note = f"صندوق بسته شد. شمارش {fmt(counted_int)} ت با عدد مورد انتظار می‌خواند."
        tone = ""
    elif session.variance < 0:
        note = (f"صندوق بسته شد. شمارش {fmt(counted_int)} ت، {fmt(abs(session.variance))} ت کمتر از عدد مورد انتظار "
                f"({fmt(expected)} ت) است؛ اختلاف ثبت شد.")
        tone = "&tone=warning"
    else:
        note = (f"صندوق بسته شد. شمارش {fmt(counted_int)} ت، {fmt(session.variance)} ت بیشتر از عدد مورد انتظار "
                f"({fmt(expected)} ت) است؛ اختلاف ثبت شد.")
        tone = "&tone=warning"
    return RedirectResponse(url=f"/admin/cashbox?msg={quote_plus(note)}{tone}", status_code=303)


@router.post("/cashbox/withdraw", response_class=HTMLResponse)
async def admin_cashbox_withdraw(
    request: Request,
    amount: str = Form("0"),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    """Take cash out of the drawer before the count.

    A bank deposit or a small cash purchase: the money is still the shop's, so
    it is not an expense — but it is no longer in the drawer, so what should be
    counted has to know about it. The reason is required rather than optional:
    a month later, «برداشت ۵۰۰٬۰۰۰» with no reason is a hole in the records.
    """
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    session = open_cash_session(db)
    if not session:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('صندوقی باز نیست؛ برداشت فقط از صندوق باز ثبت می‌شود.')}", status_code=303)
    if not (role_allows(guard.role, "manager") or session.cashier_user_id == guard.id):
        raise HTTPException(
            status_code=403,
            detail="برداشت از این صندوق فقط به دست بازکنندهٔ آن یا مدیر ممکن است.",
        )
    try:
        amount_int = int(to_english_digits(amount))
    except (TypeError, ValueError):
        amount_int = 0
    if amount_int < CASHBOX_NUMERIC_RULES["amount"][0]:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('مبلغ برداشت باید بیشتر از صفر باشد.')}", status_code=303)
    reason_text = reason.strip()
    if not reason_text:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('دلیل برداشت را بنویسید تا بعداً معلوم باشد پول کجا رفته است.')}", status_code=303)
    if amount_int > cash_shift_summary(db, session)["register"]["closing"]:
        # Deliberately without the figure: this is the one number the person
        # about to count should not be reading off the screen.
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('این مبلغ از موجودی صندوق بیشتر است.')}", status_code=303)
    entry = add_cash_withdrawal(db, session, amount_int, reason_text, guard.id,
                                request_id=request.headers.get("X-Request-ID"))
    db.commit()
    log_action(db, "cash_session_withdrawal", f"برداشت از صندوق {fmt(amount_int)}", request=request, target_type="cash_session_entry", target_id=entry.id, after={"amount": amount_int, "reason": reason_text})
    return RedirectResponse(url=f"/admin/cashbox?msg={quote_plus('برداشت ثبت شد و از عددی که باید در کشو باشد کم شد.')}", status_code=303)


@router.post("/cashbox/withdrawals/{entry_id}/reverse", response_class=HTMLResponse)
async def admin_cashbox_withdrawal_reverse(request: Request, entry_id: int, db: Session = Depends(get_db)):
    """Undo a withdrawal entered by mistake — while its shift is still open."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    entry = db.query(CashSessionEntry).filter(CashSessionEntry.id == entry_id).first()
    if not entry:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('این برداشت پیدا نشد.')}", status_code=303)
    session = db.query(CashSession).filter(CashSession.id == entry.cash_session_id).first()
    if entry.reversed_at is not None:
        return RedirectResponse(url=f"/admin/cashbox/sessions/{entry.cash_session_id}?err={quote_plus('این برداشت قبلاً برگشت خورده است.')}", status_code=303)
    if not session or session.status != "open":
        return RedirectResponse(
            url=f"/admin/cashbox/sessions/{entry.cash_session_id}?err={quote_plus('این شیفت بسته شده است؛ برداشت‌های یک شیفت بسته‌شده برگشت نمی‌خورند چون شمارش و اختلافش ثبت شده است.')}",
            status_code=303,
        )
    reverse_cash_withdrawal(db, entry, guard.id, request_id=request.headers.get("X-Request-ID"))
    db.commit()
    log_action(db, "cash_session_withdrawal_reverse", f"برگشت برداشت {fmt(entry.amount)}", request=request, target_type="cash_session_entry", target_id=entry.id, after={"amount": entry.amount})
    return RedirectResponse(url=f"/admin/cashbox/sessions/{entry.cash_session_id}?msg={quote_plus('برداشت برگشت خورد و به عدد مورد انتظار برگشت.')}", status_code=303)


@router.get("/cashbox/sessions/{session_id}", response_class=HTMLResponse)
async def admin_cash_session(request: Request, session_id: int, db: Session = Depends(get_db)):
    """One shift, with every movement behind its expected figure.

    While the shift is open only a manager sees this page. Its whole content is
    the arithmetic the counter is deliberately not shown before counting, so a
    cashier could reach the number through it; once the shift is closed there is
    nothing left to bias, and the opener may read their own.
    """
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    session = db.query(CashSession).filter(CashSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404)
    verifier = role_allows(guard.role, "manager")
    if not verifier and session.cashier_user_id != guard.id:
        raise HTTPException(
            status_code=403,
            detail="این شیفت با حساب شما باز نشده است؛ فقط شیفت‌های خودتان را می‌بینید.",
        )
    if not verifier and session.status == "open":
        raise HTTPException(
            status_code=403,
            detail="تا این شیفت بسته نشود، عدد مورد انتظار را نشان نمی‌دهیم تا شمارش واقعی بماند؛ بعد از بستن می‌توانید همین صفحه را ببینید.",
        )

    summary = cash_shift_summary(db, session)
    names = _staff_names(db, [session.cashier_user_id, session.manager_user_id])
    register = summary["register"]
    return templates.TemplateResponse(request, "admin/cashbox_session.html", {
        "row": _cash_session_row(session, names),
        "session": session,
        "movements": summary["movements"],
        "expected": (session.expected_closing_balance
                     if session.expected_closing_balance is not None else register["closing"]),
        "register": register,
        "verify": verifier,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/cashbox/opening", response_class=HTMLResponse)
async def admin_cashbox_opening(request: Request, opening: str = Form("0"), db: Session = Depends(get_db)):
    """The float suggested when no shift has ever been counted.

    A setting rather than a daily control: the day-to-day starting point is the
    last count, which the form suggests by itself. Negative amounts are refused
    — a negative float would quietly bend every closing figure derived from it.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        opening_int = int(to_english_digits(opening))
    except (TypeError, ValueError):
        opening_int = -1
    if opening_int < CASHBOX_NUMERIC_RULES["opening"][0]:
        return RedirectResponse(url=f"/admin/cashbox?err={quote_plus('موجودی پیش‌فرض نمی‌تواند منفی باشد.')}", status_code=303)
    row = db.query(Settings).filter(Settings.key == "cash_opening_balance").first()
    if row:
        row.value = str(opening_int)
    else:
        db.add(Settings(key="cash_opening_balance", value=str(opening_int)))
    db.commit()
    return RedirectResponse(url=f"/admin/cashbox?msg={quote_plus('موجودی پیش‌فرض شروع روز ذخیره شد.')}", status_code=303)
