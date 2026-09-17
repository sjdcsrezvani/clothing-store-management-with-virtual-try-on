"""Accounting-lite: net profit & loss, cash box register, customer debt
(نسیه) ledger, FIFO settlement of credit-sale payments, credit surcharge,
and aged-receivables (collection) dashboard."""
import math
from datetime import datetime, timezone, timedelta
from sqlalchemy import case, func, or_

from models import (
    Customer, Sale, SaleItem, Expense, Purchase, PurchaseItem, Payment, Settings,
    Supplier, SupplierPayment, CashSession, CashSessionEntry,
)
from services._common import get_setting_int, share


def get_net_pl(db, start, end) -> dict:
    """Net profit & loss for a period:
    revenue (confirmed non-refunded sales) − COGS (sold items' cost) − expenses."""
    sales = db.query(Sale).filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).all()
    sale_ids = [s.id for s in sales]
    revenue = sum(s.final_amount for s in sales)

    cogs = 0
    if sale_ids:
        cogs = db.query(func.sum(SaleItem.unit_cost * SaleItem.quantity)) \
            .filter(SaleItem.sale_id.in_(sale_ids)).scalar() or 0

    gross = revenue - cogs
    expenses = db.query(Expense).filter(Expense.created_at.between(start, end)).all()
    total_expenses = sum(e.amount for e in expenses if e.reversed_at is None)
    expense_type_totals = {"one_time": 0, "monthly": 0}
    by_cat: dict[str, int] = {}
    for e in expenses:
        if e.reversed_at is not None:
            continue
        expense_type = e.expense_type if e.expense_type in expense_type_totals else "one_time"
        expense_type_totals[expense_type] += e.amount
        key = e.category or "بدون دسته"
        by_cat[key] = by_cat.get(key, 0) + e.amount
    expense_cats = sorted(
        [{"category": k, "amount": v} for k, v in by_cat.items()],
        key=lambda x: x["amount"], reverse=True,
    )
    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross": gross,
        # `share`, not a nought: a period that sold nothing has no margin, and
        # «0٪» under a zero revenue is a figure the shop can only misread.
        "gross_margin": share(gross, revenue),
        "expenses": total_expenses,
        "expense_cats": expense_cats,
        "expense_type_totals": expense_type_totals,
        "net": gross - total_expenses,
        "net_margin": share(gross - total_expenses, revenue),
        "invoice_count": len(sales),
        "aov": round(revenue / len(sales)) if sales else 0,
    }


def get_credit_limit(db, customer) -> int:
    """Effective credit limit (سقف اعتبار) for a customer: their own override
    when set, otherwise the store-wide default. 0 means unlimited."""
    if customer and customer.credit_limit:
        return customer.credit_limit
    return get_setting_int(db, "default_credit_limit", 0)


def get_credit_surcharge_percent(db) -> int:
    """Configurable percent added to a نسیه sale's final amount (0 = off).
    Read from the Settings table so the owner can tune it without a restart."""
    return get_setting_int(db, "credit_surcharge_percent", 0)


def apply_credit_surcharge(db, total_amount: int, discount_amount: int = 0) -> tuple[int, int]:
    """Compute the نسیه surcharge on the discounted subtotal.

    Returns (surcharge_amount, new_final). The surcharge is a percent of
    (total − discount), rounded to the nearest toman. 0 when the setting is
    off or the subtotal is zero/negative."""
    percent = get_credit_surcharge_percent(db)
    subtotal = max(0, total_amount - discount_amount)
    if percent <= 0 or subtotal <= 0:
        return 0, subtotal
    surcharge = round(subtotal * percent / 100)
    return surcharge, subtotal + surcharge


def credit_sale_allowed(db, customer, new_amount: int) -> tuple[bool, int]:
    """True if adding `new_amount` of نسیه debt keeps the customer within their
    effective credit limit. Returns (allowed, limit) — limit 0 = unlimited."""
    limit = get_credit_limit(db, customer)
    if limit <= 0:
        return True, limit
    return ((customer.total_debt or 0) + new_amount) <= limit, limit


# ── نسیه (credit) ─────────────────────────────────────────────────────────────
# One rule decides how an invoice ages: the date it was due, falling back to its
# own date for invoices the shop never agreed a term for. The list, its KPIs, the
# ageing report and the statement all read that one rule, so they cannot disagree
# about who is late.

CREDIT_TERMS_KEY = "credit_terms_days"
CREDIT_REMINDER_PATTERN_KEY = "sms_pattern_credit_reminder"
CREDIT_REMINDER_COOLDOWN_KEY = "credit_reminder_min_hours"
DUE_SOON_DAYS = 7
PER_PAGE = 25

AGE_BUCKETS = ("current", "1_30", "31_60", "61_90", "over_90")
AGE_BUCKET_LABELS = {
    "current": "جاری",
    "1_30": "۱–۳۰ روز دیرکرد",
    "31_60": "۳۱–۶۰ روز دیرکرد",
    "61_90": "۶۱–۹۰ روز دیرکرد",
    "over_90": "بیش از ۹۰ روز دیرکرد",
}
# The status keys are shared by the route, the KPI cards and the tests instead of
# being restated in each — a card links to the filter that reproduces its number.
DEBT_STATUS_LABELS = {
    "all": "همه بدهکاران",
    "overdue": "سررسید گذشته",
    "due_soon": f"سررسید تا {DUE_SOON_DAYS} روز",
    "over_limit": "بالای سقف اعتبار",
    "no_due_date": "بدون سررسید",
}
DEBT_SORTS = {
    "debt": "بیشترین بدهی",
    "oldest": "قدیمی‌ترین سررسید",
    "lateness": "بیشترین دیرکرد",
    "name": "نام مشتری",
}


def credit_terms_days(db) -> int:
    """The agreed نسیه term in days. 0 means the shop sets no سررسید at all,
    and every invoice falls back to ageing by its own date."""
    return max(0, get_setting_int(db, CREDIT_TERMS_KEY, 30))


def credit_due_date_for(db, when: datetime | None = None) -> datetime | None:
    """The سررسید a نسیه sale recorded at `when` falls due, or None."""
    days = credit_terms_days(db)
    if days <= 0:
        return None
    base = as_utc(when) or datetime.now(timezone.utc)
    return base + timedelta(days=days)


def sale_remaining(sale) -> int:
    """What is still owed on one invoice."""
    return max(0, (sale.final_amount or 0) - (sale.credit_paid_amount or 0))


def due_effective_at(sale) -> datetime | None:
    """When ageing starts for an invoice: its سررسید, or its own date.

    The fallback is what this page has always done, so an invoice recorded
    before سررسید existed keeps the bucket it is already in.
    """
    return as_utc(sale.credit_due_date) or as_utc(sale.created_at)


def days_past_due(sale, now: datetime | None = None) -> int:
    """Whole days since the invoice fell due. Negative means not due yet."""
    now = now or datetime.now(timezone.utc)
    effective = due_effective_at(sale)
    if effective is None:
        return 0
    return (now - effective).days


def age_bucket(sale, now: datetime | None = None) -> str:
    """Which ageing bucket an invoice sits in.

    An invoice with a سررسید that has not arrived is جاری; one with no agreed
    term is جاری until it is more than 30 days old, exactly as before, so no
    existing debtor jumps buckets because a column appeared.
    """
    days = days_past_due(sale, now)
    if days <= 0:
        return "current"
    if days <= 30:
        return "1_30" if sale.credit_due_date is not None else "current"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "over_90"


def unpaid_credit_sales(db, customer_id: int) -> list:
    """A customer's open نسیه invoices, oldest first — FIFO's settle order."""
    return db.query(Sale).filter(
        Sale.customer_id == customer_id,
        Sale.payment_method == "credit",
        Sale.is_refunded == False,  # noqa: E712
        Sale.credit_settled == False,  # noqa: E712
    ).order_by(Sale.created_at.asc(), Sale.id.asc()).all()


def _open_invoices_by_customer(db, customer_ids: list, now: datetime) -> dict:
    """Open invoices for a whole page of customers in one query, not one each."""
    grouped: dict[int, list] = {cid: [] for cid in customer_ids}
    if not customer_ids:
        return grouped
    sales = db.query(Sale).filter(
        Sale.customer_id.in_(customer_ids),
        Sale.payment_method == "credit",
        Sale.is_refunded == False,  # noqa: E712
        Sale.credit_settled == False,  # noqa: E712
    ).order_by(Sale.created_at.asc(), Sale.id.asc()).all()
    for sale in sales:
        grouped.setdefault(sale.customer_id, []).append(sale)
    return grouped


def _last_payments_by_customer(db, customer_ids: list) -> dict:
    """Each customer's most recent receipt, in one query."""
    latest: dict[int, Payment] = {}
    if not customer_ids:
        return latest
    payments = db.query(Payment).filter(
        Payment.customer_id.in_(customer_ids),
        Payment.reversed_at.is_(None),
    ).order_by(Payment.created_at.desc(), Payment.id.desc()).all()
    for payment in payments:
        latest.setdefault(payment.customer_id, payment)
    return latest


def _stored_reminders(db, customer_ids: list) -> dict:
    """When each customer was last reminded, from the marker settings."""
    marks: dict[int, datetime] = {}
    if not customer_ids:
        return marks
    keys = [f"credit_reminder_{cid}" for cid in customer_ids]
    for row in db.query(Settings).filter(Settings.key.in_(keys)).all():
        try:
            marks[int(row.key.rsplit("_", 1)[1])] = as_utc(datetime.fromisoformat(row.value))
        except (ValueError, TypeError, IndexError):
            continue
    return marks


def build_debt_rows(db, *, search: str = "", now: datetime | None = None) -> list[dict]:
    """Every debtor with the derived ageing the list, KPIs and filters share.

    Built here, in a fixed number of queries, rather than in the template or per
    row: the previous version ran four queries per debtor (two of them for data
    the page never showed), which is what made this page slow to grow.
    """
    now = now or datetime.now(timezone.utc)
    query = db.query(Customer).filter(Customer.total_debt > 0)
    search = (search or "").strip()
    if search:
        query = query.filter(or_(
            Customer.phone.contains(search),
            Customer.first_name.contains(search),
            Customer.last_name.contains(search),
        ))
    customers = query.order_by(Customer.total_debt.desc()).all()
    ids = [customer.id for customer in customers]
    invoices_by_customer = _open_invoices_by_customer(db, ids, now)
    last_payments = _last_payments_by_customer(db, ids)
    reminders = _stored_reminders(db, ids)
    default_limit = get_setting_int(db, "default_credit_limit", 0)

    rows = []
    for customer in customers:
        invoices = invoices_by_customer.get(customer.id, [])
        limit = customer.credit_limit or default_limit or 0
        debt = customer.total_debt or 0
        bucket_amounts = {key: 0 for key in AGE_BUCKETS}
        bucket_counts = {key: 0 for key in AGE_BUCKETS}
        overdue_amount = due_soon_amount = no_due_date_amount = 0
        no_due_date_count = 0
        days_late = 0
        oldest_due = None
        for sale in invoices:
            left = sale_remaining(sale)
            key = age_bucket(sale, now)
            late = days_past_due(sale, now)
            bucket_amounts[key] += left
            bucket_counts[key] += 1
            days_late = max(days_late, late)
            if key != "current":
                overdue_amount += left
            elif sale.credit_due_date is not None and -DUE_SOON_DAYS <= late < 0:
                due_soon_amount += left
            if sale.credit_due_date is None:
                no_due_date_count += 1
                no_due_date_amount += left
            effective = due_effective_at(sale)
            if effective and (oldest_due is None or effective < oldest_due):
                oldest_due = effective
        worst = "current"
        for key in reversed(AGE_BUCKETS):
            if bucket_amounts[key] > 0:
                worst = key
                break
        rows.append({
            "customer": customer,
            "debt": debt,
            "limit": limit,
            "headroom": max(0, limit - debt) if limit > 0 else 0,
            "over_limit": limit > 0 and debt > limit,
            "invoices": invoices,
            "invoice_count": len(invoices),
            "bucket": worst,
            "bucket_label": AGE_BUCKET_LABELS[worst],
            "bucket_amounts": bucket_amounts,
            "bucket_counts": bucket_counts,
            "overdue_amount": overdue_amount,
            "due_soon_amount": due_soon_amount,
            "no_due_date_count": no_due_date_count,
            "no_due_date_amount": no_due_date_amount,
            "days_late": max(0, days_late),
            "oldest_due": oldest_due,
            "last_payment": last_payments.get(customer.id),
            "last_reminder": reminders.get(customer.id),
            "drift": debt != sum(sale_remaining(sale) for sale in invoices),
        })
    return rows


def summarise_debts(rows: list, now: datetime | None = None) -> dict:
    """The KPI numbers, aggregated from the same rows the table shows."""
    now = now or datetime.now(timezone.utc)
    bucket_totals = {key: 0 for key in AGE_BUCKETS}
    bucket_counts = {key: 0 for key in AGE_BUCKETS}
    total_debt = overdue_amount = overdue_customers = 0
    over_limit_count = over_limit_amount = 0
    due_soon_amount = due_soon_customers = 0
    no_due_date_amount = no_due_date_count = 0
    drift_count = 0
    for row in rows:
        total_debt += row["debt"]
        for key in AGE_BUCKETS:
            bucket_totals[key] += row["bucket_amounts"][key]
            bucket_counts[key] += row["bucket_counts"][key]
        if row["overdue_amount"] > 0:
            overdue_customers += 1
            overdue_amount += row["overdue_amount"]
        if row["over_limit"]:
            over_limit_count += 1
            over_limit_amount += row["debt"] - row["limit"]
        if row["due_soon_amount"] > 0:
            due_soon_customers += 1
            due_soon_amount += row["due_soon_amount"]
        if row["no_due_date_count"]:
            no_due_date_count += row["no_due_date_count"]
            no_due_date_amount += row["no_due_date_amount"]
        if row["drift"]:
            drift_count += 1
    return {
        "total_debt": total_debt,
        "debtor_count": len(rows),
        "overdue_amount": overdue_amount,
        "overdue_customers": overdue_customers,
        "over_limit_count": over_limit_count,
        "over_limit_amount": over_limit_amount,
        "due_soon_amount": due_soon_amount,
        "due_soon_customers": due_soon_customers,
        "no_due_date_amount": no_due_date_amount,
        "no_due_date_count": no_due_date_count,
        "drift_count": drift_count,
        "buckets": bucket_totals,
        "bucket_counts": bucket_counts,
        "bucket_labels": AGE_BUCKET_LABELS,
        "due_soon_days": DUE_SOON_DAYS,
        "now": now,
    }


def page_debts(rows: list, *, status: str = "all", bucket: str = "",
               sort: str = "debt", page: int = 1, per_page: int = PER_PAGE) -> dict:
    """Filter, order and cut one page out of the debtor rows."""
    filtered = rows
    if status == "overdue":
        filtered = [row for row in filtered if row["overdue_amount"] > 0]
    elif status == "due_soon":
        filtered = [row for row in filtered if row["due_soon_amount"] > 0]
    elif status == "over_limit":
        filtered = [row for row in filtered if row["over_limit"]]
    elif status == "no_due_date":
        filtered = [row for row in filtered if row["no_due_date_count"] > 0]
    if bucket in AGE_BUCKETS:
        filtered = [row for row in filtered if row["bucket_amounts"].get(bucket, 0) > 0]

    far_future = datetime.max.replace(tzinfo=timezone.utc)
    sorters = {
        "debt": lambda row: (-row["debt"], row["customer"].full_name or ""),
        "oldest": lambda row: (row["oldest_due"] or far_future,),
        "lateness": lambda row: (-row["days_late"], -row["debt"]),
        "name": lambda row: (row["customer"].full_name or "",),
    }
    filtered = sorted(filtered, key=sorters.get(sort, sorters["debt"]))

    total = len(filtered)
    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return {
        "rows": filtered[start:start + per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
    }


def list_debts(db, *, search: str = "", status: str = "all", bucket: str = "",
               sort: str = "debt", page: int = 1, per_page: int = PER_PAGE,
               now: datetime | None = None) -> dict:
    """One call for the credit page: the rows, the page and the KPI summary."""
    now = now or datetime.now(timezone.utc)
    rows = build_debt_rows(db, search=search, now=now)
    paged = page_debts(rows, status=status, bucket=bucket, sort=sort,
                       page=page, per_page=per_page)
    return {
        **paged,
        "overview": summarise_debts(rows, now=now),
        "has_filters": bool((search or "").strip() or status != "all" or bucket or sort != "debt"),
    }


def list_open_invoices(db, *, search: str = "", status: str = "all", bucket: str = "",
                       page: int = 1, per_page: int = PER_PAGE,
                       now: datetime | None = None) -> dict:
    """Every open نسیه invoice, latest deadline first — the invoice-level view.

    The old collections page could only show one row per customer, which hides
    the fact that someone with six open invoices has five of them current.
    """
    now = now or datetime.now(timezone.utc)
    query = db.query(Sale).join(Customer, Sale.customer_id == Customer.id).filter(
        Sale.payment_method == "credit",
        Sale.is_refunded == False,  # noqa: E712
        Sale.credit_settled == False,  # noqa: E712
    )
    search = (search or "").strip()
    if search:
        query = query.filter(or_(
            Customer.phone.contains(search),
            Customer.first_name.contains(search),
            Customer.last_name.contains(search),
        ))
    rows = []
    for sale in query.order_by(Sale.created_at.asc(), Sale.id.asc()).all():
        key = age_bucket(sale, now)
        late = days_past_due(sale, now)
        if bucket in AGE_BUCKETS and key != bucket:
            continue
        if status == "overdue" and key == "current":
            continue
        if status == "due_soon" and not (
            sale.credit_due_date is not None and -DUE_SOON_DAYS <= late < 0
        ):
            continue
        if status == "no_due_date" and sale.credit_due_date is not None:
            continue
        rows.append({
            "sale": sale,
            "customer": sale.customer,
            "remaining": sale_remaining(sale),
            "due": as_utc(sale.credit_due_date),
            "effective": due_effective_at(sale),
            "days_late": late,
            "bucket": key,
            "bucket_label": AGE_BUCKET_LABELS[key],
        })
    rows.sort(key=lambda row: -row["days_late"])
    total = len(rows)
    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return {
        "rows": rows[start:start + per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
    }


def debt_drift(db, customer) -> dict:
    """Stored debt vs the open invoices that should add up to it.

    The counter is adjusted in place at checkout, at every receipt and on a
    reversal, so it can drift. The page says so rather than quietly trusting it.
    """
    stored = customer.total_debt or 0
    recomputed = sum(sale_remaining(sale) for sale in unpaid_credit_sales(db, customer.id))
    return {"stored": stored, "recomputed": recomputed, "mismatch": stored != recomputed}


def assign_due_dates(db, terms_days: int | None = None) -> int:
    """Stamp a سررسید on open invoices that have none. Opt-in, one click.

    Existing invoices are deliberately left alone by the migration, so ageing
    never changes behind the owner's back; this is the button that changes it.
    """
    days = credit_terms_days(db) if terms_days is None else max(0, terms_days)
    if days <= 0:
        return 0
    updated = 0
    sales = db.query(Sale).filter(
        Sale.payment_method == "credit",
        Sale.is_refunded == False,  # noqa: E712
        Sale.credit_settled == False,  # noqa: E712
        Sale.credit_due_date.is_(None),
    ).all()
    for sale in sales:
        base = as_utc(sale.created_at) or datetime.now(timezone.utc)
        sale.credit_due_date = base + timedelta(days=days)
        updated += 1
    return updated


def last_credit_reminder(db, customer_id: int) -> datetime | None:
    """When this customer was last sent a debt reminder (None = never)."""
    return _stored_reminders(db, [customer_id]).get(customer_id)


def credit_reminder_cooldown_hours(db) -> int:
    """How long to wait before reminding the same customer again."""
    return max(0, get_setting_int(db, CREDIT_REMINDER_COOLDOWN_KEY, 24))


def credit_reminder_allowed(db, customer) -> tuple[bool, str]:
    """Whether a reminder may go out now, with the reason when it may not."""
    last = last_credit_reminder(db, customer.id)
    hours = credit_reminder_cooldown_hours(db)
    if last is None or hours <= 0:
        return True, ""
    ready_at = last + timedelta(hours=hours)
    now = datetime.now(timezone.utc)
    if ready_at <= now:
        return True, ""

    def _fa(number: int) -> str:
        return str(number).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    wait = ready_at - now
    if wait >= timedelta(hours=1):
        return False, f"آخرین یادآوری کمتر از {_fa(hours)} ساعت پیش بوده — {_fa(math.ceil(wait.total_seconds() / 3600))} ساعت دیگر دوباره تلاش کنید."
    return False, f"آخرین یادآوری کمتر از {_fa(hours)} ساعت پیش بوده — {_fa(max(1, math.ceil(wait.total_seconds() / 60)))} دقیقه دیگر دوباره تلاش کنید."


def mark_credit_reminder_sent(db, customer_id: int) -> datetime:
    """Remember a reminder so the cool-down can be enforced."""
    now = datetime.now(timezone.utc)
    key = f"credit_reminder_{customer_id}"
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = now.isoformat()
    else:
        db.add(Settings(key=key, value=now.isoformat()))
    return now


def credit_reminder_vars(customer, amount: int, due_date: datetime | None = None) -> dict:
    """Attributes an owner's reminder template can use: var1 نام، var2 مبلغ،
    var3 سررسید."""
    from services._common import jalali_str

    return {
        "var1": customer.full_name or "مشتری",
        "var2": f"{int(amount):,}",
        "var3": jalali_str(due_date, with_time=False) if due_date else "—",
    }


def get_payment_history(db, customer_id: int, limit: int = 50) -> list:
    return db.query(Payment).filter(Payment.customer_id == customer_id) \
        .order_by(Payment.created_at.desc()).limit(limit).all()


def apply_customer_payment(
    db,
    customer,
    amount: int,
    method: str = "cash",
    note: str = "",
    sale_id: int | None = None,
    operator_user_id: int | None = None,
    received_by_id: int | None = None,
    request_id: str | None = None,
) -> int:
    """Record a payment toward a customer's نسیه debt.

    With a `sale_id` the money lands on exactly that invoice, capped at what it
    still owes: the cashier pointed at one فاکتور, so none of it spills onto
    another behind their back. Without one it settles oldest-first (FIFO) across
    every open invoice, which is how a round payment from a customer is handled.
    A `payments` row records the applied amount; it returns that amount, or 0
    when there was nothing to settle.
    """
    amount = max(0, amount)
    if amount <= 0:
        return 0

    applied = 0
    if sale_id is not None:
        target = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.customer_id == customer.id,
            Sale.payment_method == "credit",
            Sale.is_refunded == False,  # noqa: E712
        ).first()
        if target is None:
            return 0
        applied = min(amount, sale_remaining(target))
        if applied <= 0:
            return 0
        target.credit_paid_amount = (target.credit_paid_amount or 0) + applied
        if sale_remaining(target) <= 0:
            target.credit_settled = True
    else:
        for sale in unpaid_credit_sales(db, customer.id):
            remaining = sale_remaining(sale)
            if remaining <= 0:
                sale.credit_settled = True
                continue
            pay = min(remaining, amount - applied)
            if pay > 0:
                sale.credit_paid_amount = (sale.credit_paid_amount or 0) + pay
                applied += pay
                if sale_remaining(sale) <= 0:
                    sale.credit_settled = True
            if applied >= amount:
                break

    if applied > 0:
        open_session = open_cash_session(db)
        payment = Payment(
            customer_id=customer.id,
            sale_id=sale_id,
            amount=applied,
            method=method,
            cash_session_id=open_session.id if open_session and method == "cash" else None,
            received_by_id=received_by_id or operator_user_id,
            note=note or "",
        )
        db.add(payment)
        db.flush()
        from services.events import append_event
        append_event(
            db,
            "CreditPaymentRecorded",
            "payment",
            payment.id,
            idempotency_key=f"payment:{payment.id}:recorded",
            actor_user_id=operator_user_id,
            request_id=request_id,
            payload={
                "customer_id": customer.id,
                "sale_id": sale_id,
                "amount": applied,
                "method": method,
                "cash_session_id": payment.cash_session_id,
                "received_by_id": payment.received_by_id,
            },
        )
        customer.total_debt = max(0, (customer.total_debt or 0) - applied)

    return applied


def reverse_payment(
    db,
    payment,
    operator_id=None,
    reason="Payment reversal",
    request_id=None,
) -> int:
    """Record an immutable reversal and rebuild the remaining allocation."""
    if getattr(payment, "reversed_at", None):
        return 0
    from services.ledger import reverse_payment_immutably
    customer = payment.customer
    amount = payment.amount or 0
    reverse_payment_immutably(db, payment, operator_id, reason, request_id=request_id)
    db.flush()

    sales = db.query(Sale).filter(
        Sale.customer_id == customer.id,
        Sale.payment_method == "credit",
        Sale.is_refunded == False,
    ).order_by(Sale.created_at.asc(), Sale.id.asc()).all()

    for sale in sales:
        sale.credit_paid_amount = 0
        sale.credit_settled = False

    by_id = {sale.id: sale for sale in sales}
    remaining_payments = db.query(Payment).filter(
        Payment.customer_id == customer.id,
        Payment.reversed_at.is_(None),
    ).order_by(Payment.created_at.asc(), Payment.id.asc()).all()

    # A receipt aimed at one invoice keeps that invoice first; whatever it is
    # worth beyond that then settles oldest-first with every other receipt. Money
    # is never dropped on the floor: the remainder always lands somewhere.
    leftovers = []
    for pay_row in remaining_payments:
        pocket = pay_row.amount or 0
        target = by_id.get(pay_row.sale_id) if pay_row.sale_id else None
        if target is not None and pocket > 0:
            take = min(sale_remaining(target), pocket)
            if take > 0:
                target.credit_paid_amount = (target.credit_paid_amount or 0) + take
                if sale_remaining(target) <= 0:
                    target.credit_settled = True
                pocket -= take
        leftovers.append(pocket)

    for pocket in leftovers:
        for sale in sales:
            if pocket <= 0:
                break
            take = min(sale_remaining(sale), pocket)
            if take > 0:
                sale.credit_paid_amount = (sale.credit_paid_amount or 0) + take
                if sale_remaining(sale) <= 0:
                    sale.credit_settled = True
                pocket -= take

    customer.total_debt = max(0, sum(sale_remaining(sale) for sale in sales))
    return amount


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime (SQLite rows can come back without tzinfo)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def purchase_effective_at(purchase) -> datetime | None:
    """Invoice date of a purchase, falling back to when it was recorded.

    Money and reporting key off this date; the stock ledger keeps using
    ``created_at`` because that is when the units physically arrived.
    """
    return purchase.purchase_date or purchase.created_at


def purchase_effective_column():
    """SQL expression matching :func:`purchase_effective_at` for range filters."""
    return func.coalesce(Purchase.purchase_date, Purchase.created_at)


def purchase_paid_amount(db, purchase) -> int:
    """Money settled against one purchase.

    Payments linked to the purchase are the source of truth. ``amount_paid`` is
    a cache refreshed whenever a linked payment is written; it is only trusted
    for legacy rows that predate payment linking and have no rows at all.
    """
    entry = purchase_payment_rollup(db, [purchase.id]).get(purchase.id)
    return purchase_paid_from_rollup(purchase, entry)


def refresh_purchase_amount_paid(db, purchase) -> int:
    """Rewrite the cached ``amount_paid`` from the live linked payments.

    Flushes first: sessions here run with autoflush off, so pending changes
    (a new payment, or a payment being detached on reversal) must be written
    before the aggregate can see them.
    """
    db.flush()
    paid = db.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).filter(
        SupplierPayment.purchase_id == purchase.id,
        SupplierPayment.reversed_at.is_(None),
    ).scalar() or 0
    purchase.amount_paid = paid
    return paid


def purchase_settlement(db, purchase, paid: int | None = None, now: datetime | None = None) -> dict:
    """Paid / remaining / due state of one purchase."""
    total = purchase.total_cost or 0
    if purchase.is_draft:
        # A draft invoice is not a fact yet: no money can have been settled
        # against it and it can never be past due.
        return {
            "total": total,
            "paid": 0,
            "remaining": total,
            "due_date": as_utc(purchase.due_date),
            "overdue": False,
            "status": "draft",
        }
    if paid is None:
        paid = purchase_paid_amount(db, purchase)
    remaining = max(0, total - paid)
    due = as_utc(purchase.due_date)
    now = now or datetime.now(timezone.utc)
    overdue = bool(
        not purchase.is_reversed and remaining > 0 and due is not None and due < now
    )
    if purchase.is_reversed:
        status = "reversed"
    elif total > 0 and remaining <= 0:
        status = "paid"
    elif paid > 0:
        status = "partial"
    else:
        status = "unpaid"
    return {
        "total": total,
        "paid": paid,
        "remaining": remaining,
        "due_date": due,
        "overdue": overdue,
        "status": status,
    }


def purchase_payment_rollup(db, purchase_ids: list[int]) -> dict[int, dict]:
    """Rows and live settled total per purchase, in one query (no N+1).

    ``rows`` counts every linked payment including reversed ones, so a purchase
    whose payments were all reversed reports rows > 0 and paid = 0 instead of
    falling back to the cached ``amount_paid``.
    """
    ids = [pid for pid in (purchase_ids or []) if pid]
    if not ids:
        return {}
    rows = db.query(
        SupplierPayment.purchase_id,
        func.count(SupplierPayment.id),
        func.coalesce(func.sum(case((SupplierPayment.reversed_at.is_(None), SupplierPayment.amount), else_=0)), 0),
    ).filter(SupplierPayment.purchase_id.in_(ids)) \
        .group_by(SupplierPayment.purchase_id).all()
    return {
        purchase_id: {"rows": row_count or 0, "paid": paid or 0}
        for purchase_id, row_count, paid in rows
    }


def purchase_paid_from_rollup(purchase, entry: dict | None) -> int:
    """Live linked payments win; the cache only fills in for legacy rows."""
    if not entry or not entry.get("rows"):
        return max(0, purchase.amount_paid or 0)
    return entry.get("paid") or 0


def purchase_item_totals(db, purchase_ids: list[int]) -> dict[int, dict]:
    """Line count and total units per purchase (for the history table)."""
    if not purchase_ids:
        return {}
    rows = db.query(
        PurchaseItem.purchase_id,
        func.count(PurchaseItem.id),
        func.coalesce(func.sum(PurchaseItem.quantity), 0),
    ).filter(PurchaseItem.purchase_id.in_(purchase_ids)) \
        .group_by(PurchaseItem.purchase_id).all()
    return {
        purchase_id: {"lines": lines, "units": units or 0}
        for purchase_id, lines, units in rows
    }


def purchase_landed_unit_cost(
    unit_cost: int,
    quantity: int,
    items_subtotal: int,
    extra_cost: int,
    apply_extra: bool = True,
) -> int:
    """Unit cost with this line's share of shipping spread by line value."""
    if not apply_extra or extra_cost <= 0 or items_subtotal <= 0 or quantity <= 0:
        return unit_cost
    line_value = unit_cost * quantity
    share = extra_cost * line_value / items_subtotal
    return unit_cost + round(share / quantity)


def apply_purchase_cost_basis(db, purchase, actor_user_id: int | None = None, request_id: str | None = None) -> int:
    """Move a purchase's landed unit costs into the variants' cost basis.

    Runs when a draft invoice is finalised, so an invoice still being assembled
    cannot move a single cost. Returns how many lines actually changed.
    """
    from services.inventory import record_cost_adjustment

    items = db.query(PurchaseItem).filter(
        PurchaseItem.purchase_id == purchase.id
    ).order_by(PurchaseItem.id).all()
    items_subtotal = sum((item.unit_cost or 0) * (item.quantity or 0) for item in items)
    applied = 0
    for item in items:
        variant = item.variant
        if variant is None:
            continue
        # Shipping is spread across the lines, so the stored cost basis is the
        # landed cost the shop actually paid per unit.
        landed_unit = purchase_landed_unit_cost(
            item.unit_cost or 0, item.quantity or 0, items_subtotal,
            purchase.extra_cost or 0, purchase.extra_cost_in_landed,
        )
        # Cost basis before this line, restored if the purchase is reversed,
        # plus the value this line actually applied.
        item.prev_cost_price = variant.cost_price if item.unit_cost else None
        item.landed_unit_cost = landed_unit if landed_unit > 0 else None
        if landed_unit > 0:
            record_cost_adjustment(
                db,
                variant,
                variant.cost_price or 0,
                landed_unit,
                note=f"به‌روزرسانی بهای تمام‌شده از خرید #{purchase.id}",
                actor_user_id=actor_user_id,
                request_id=request_id,
                purchase_id=purchase.id,
            )
            if (variant.cost_price or 0) != landed_unit:
                variant.cost_price = landed_unit
                applied += 1
    return applied


def purchase_overview(db, start, end, now: datetime | None = None) -> dict:
    """Headline numbers for the purchases page: period spend, units and debt."""
    now = now or datetime.now(timezone.utc)
    in_range = [
        purchase_effective_column().between(start, end),
        Purchase.is_reversed == False,
        # Draft invoices are still being assembled and are not spend yet.
        Purchase.is_draft == False,
    ]
    spend = db.query(func.coalesce(func.sum(Purchase.total_cost), 0)).filter(*in_range).scalar() or 0
    count = db.query(func.count(Purchase.id)).filter(*in_range).scalar() or 0
    units = db.query(func.coalesce(func.sum(PurchaseItem.quantity), 0)) \
        .join(Purchase, PurchaseItem.purchase_id == Purchase.id) \
        .filter(*in_range).scalar() or 0

    balances = get_supplier_balances(db)
    owed = sum(row["owed"] for row in balances)

    open_purchases = db.query(Purchase).filter(
        Purchase.is_reversed == False,
        Purchase.is_draft == False,
        Purchase.due_date.isnot(None),
    ).all()
    rollup = purchase_payment_rollup(db, [p.id for p in open_purchases])
    overdue_count = 0
    overdue_amount = 0
    for purchase in open_purchases:
        paid = purchase_paid_from_rollup(purchase, rollup.get(purchase.id))
        settlement = purchase_settlement(db, purchase, paid=paid, now=now)
        if settlement["overdue"]:
            overdue_count += 1
            overdue_amount += settlement["remaining"]

    return {
        "period_spend": spend,
        "period_count": count,
        "period_units": units,
        "supplier_owed": owed,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
    }


def _drawer_filters(db, start, end, cash_session_id: int | None = None) -> dict:
    """The clauses that decide what the drawer did — one definition, two readers.

    The register totals (:func:`get_cashbox`) and the shift statement
    (:func:`cash_shift_summary`) both draw from this, so the page and the
    statement behind it cannot drift into two versions of one shift.

    Only real money movements belong. A purchase invoice is an accrual entry and
    stays out, because the money it represents leaves the till as a supplier
    payment — counting both would count it twice. A card-paid expense stays out
    for the same reason: it left the shop's account, not the drawer.

    Scoping to a shift mixes two mechanisms on purpose. Sales and نسیه receipts
    are matched by the **time window**, because rows written before a sale
    carried ``cash_session_id`` are still genuine till movements and must keep
    being counted; refunds, expenses, supplier payments and withdrawals carry
    the id, which survives the hand-correction a time window would lose.
    """
    filters = {
        "sale": [Sale.payment_confirmed == True, Sale.is_refunded == False,  # noqa: E712
                 Sale.payment_method == "cash", Sale.created_at.between(start, end)],
        "payment": [Payment.method == "cash", Payment.reversed_at.is_(None),
                    Payment.created_at.between(start, end)],
        "refund": [Sale.is_refunded == True, Sale.payment_method == "cash",  # noqa: E712
                   Sale.refund_date.between(start, end)],
        "expense": [Expense.reversed_at.is_(None), Expense.payment_method == "cash",
                    Expense.created_at.between(start, end)],
        "supplier": [SupplierPayment.method == "cash", SupplierPayment.reversed_at.is_(None),
                     SupplierPayment.created_at.between(start, end)],
        "entry": [CashSessionEntry.reversed_at.is_(None),
                  CashSessionEntry.created_at.between(start, end)],
    }
    if cash_session_id is not None:
        session = db.query(CashSession).filter(CashSession.id == cash_session_id).first()
        if session:
            filters["sale"].append(Sale.created_at >= session.opened_at)
            filters["payment"].append(Payment.created_at >= session.opened_at)
            filters["refund"].append(Sale.cash_session_id == cash_session_id)
            filters["expense"].append(Expense.cash_session_id == cash_session_id)
            filters["supplier"].append(SupplierPayment.cash_session_id == cash_session_id)
            filters["entry"].append(CashSessionEntry.cash_session_id == cash_session_id)
    return filters


def get_cashbox(db, start, end, opening_balance: int, cash_session_id: int | None = None) -> dict:
    """Cash register for a period, optionally scoped to one cash shift."""
    filters = _drawer_filters(db, start, end, cash_session_id)

    cash_in_sales = db.query(func.coalesce(func.sum(Sale.final_amount), 0)).filter(*filters["sale"]).scalar() or 0
    cash_in_payments = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(*filters["payment"]).scalar() or 0
    cash_out_refunds = db.query(func.coalesce(func.sum(Sale.refund_amount), 0)).filter(*filters["refund"]).scalar() or 0
    cash_out_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(*filters["expense"]).scalar() or 0
    cash_out_withdrawals = db.query(func.coalesce(func.sum(CashSessionEntry.amount), 0)).filter(*filters["entry"]).scalar() or 0
    # Informational only: invoices recorded in the period, regardless of payment.
    # Drafts are excluded: they are not invoices yet.
    invoice_purchases = db.query(func.coalesce(func.sum(Purchase.total_cost), 0)).filter(
        purchase_effective_column().between(start, end),
        Purchase.is_reversed == False,
        Purchase.is_draft == False,
    ).scalar() or 0
    cash_out_supplier_payments = db.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).filter(*filters["supplier"]).scalar() or 0

    # The closing identity, spelled out once so every reader of this dict adds
    # up the same way:
    #   expected = opening + فروش نقدی + دریافت نسیه
    #                     − مرجوعی − هزینه − پرداخت تأمین‌کننده − برداشت
    cash_in = cash_in_sales + cash_in_payments
    cash_out = (cash_out_refunds + cash_out_expenses + cash_out_supplier_payments
                + cash_out_withdrawals)
    closing = opening_balance + cash_in - cash_out
    return {
        "opening": opening_balance,
        "cash_sales": cash_in_sales,
        "credit_payments": cash_in_payments,
        "cash_in": cash_in,
        "refunds": cash_out_refunds,
        "expenses": cash_out_expenses,
        "withdrawals": cash_out_withdrawals,
        "purchases": invoice_purchases,
        "supplier_payments": cash_out_supplier_payments,
        "cash_out": cash_out,
        "closing": closing,
    }


def open_cash_session(db) -> CashSession | None:
    """The shift the drawer is currently in, or ``None`` when it is shut.

    One definition: six call sites used to write this query themselves, and a
    rule about which shift is current can only be kept in one place.
    """
    return (db.query(CashSession)
            .filter(CashSession.status == "open")
            .order_by(CashSession.opened_at.desc())
            .first())


def last_counted_balance(db) -> int | None:
    """What the last counted shift closed at — the next shift's opening float.

    A suggestion, not an assumption: the person opening the drawer writes down
    whatever is actually in it, and this only spares them re-typing yesterday's
    number. ``None`` when no shift has ever been counted, so the settings
    default is used rather than an invented figure.
    """
    last = (db.query(CashSession)
            .filter(CashSession.status == "closed",
                    CashSession.counted_closing_balance.isnot(None))
            .order_by(CashSession.closed_at.desc(), CashSession.id.desc())
            .first())
    return last.counted_closing_balance if last else None


def cash_shift_summary(db, session: CashSession) -> dict:
    """One shift: every movement, and the arithmetic they add up to.

    The same clauses the register uses (:func:`_drawer_filters`) produce the
    rows, so a difference on the page can always be traced to the invoice,
    expense, payment or withdrawal behind it — and the two cannot disagree.
    """
    start = session.opened_at
    end = session.closed_at or datetime.now(timezone.utc)
    filters = _drawer_filters(db, start, end, session.id)
    movements = []

    for sale in db.query(Sale).filter(*filters["sale"]).all():
        movements.append({
            "kind": "sale", "label": f"فروش نقدی #{sale.id}", "note": "",
            "amount": sale.final_amount or 0, "direction": "in",
            "href": f"/sales/invoice/{sale.id}", "when": sale.created_at,
        })

    payments = db.query(Payment).filter(*filters["payment"]).all()
    names = {c.id: c.full_name for c in db.query(Customer)
             .filter(Customer.id.in_([p.customer_id for p in payments if p.customer_id])).all()} \
        if payments else {}
    for payment in payments:
        who = names.get(payment.customer_id) or "مشتری حذف‌شده"
        movements.append({
            "kind": "payment", "label": f"دریافت نسیه از {who}",
            "note": payment.note or "", "amount": payment.amount or 0,
            "direction": "in", "when": payment.created_at,
            "href": f"/admin/customers/{payment.customer_id}" if payment.customer_id else None,
        })

    for sale in db.query(Sale).filter(*filters["refund"]).all():
        movements.append({
            "kind": "refund", "label": f"مرجوعی فاکتور #{sale.id}",
            "note": sale.refund_reason or "", "amount": sale.refund_amount or 0,
            "direction": "out", "href": f"/sales/invoice/{sale.id}",
            "when": sale.refund_date or sale.created_at,
        })

    for expense in db.query(Expense).filter(*filters["expense"]).all():
        movements.append({
            "kind": "expense", "label": expense.category or "هزینه بدون دسته",
            "note": expense.note or "", "amount": expense.amount or 0,
            "direction": "out", "href": "/admin/expenses", "when": expense.created_at,
        })

    supplier_payments = db.query(SupplierPayment).filter(*filters["supplier"]).all()
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(
        Supplier.id.in_([p.supplier_id for p in supplier_payments if p.supplier_id])).all()} \
        if supplier_payments else {}
    for payment in supplier_payments:
        movements.append({
            "kind": "supplier",
            "label": f"پرداخت به {suppliers.get(payment.supplier_id) or 'تأمین‌کننده حذف‌شده'}",
            "note": payment.note or "", "amount": payment.amount or 0,
            "direction": "out", "href": "/admin/suppliers", "when": payment.created_at,
        })

    for entry in db.query(CashSessionEntry).filter(*filters["entry"]).all():
        movements.append({
            "kind": "withdrawal", "label": "برداشت از صندوق", "note": entry.reason,
            "amount": entry.amount or 0, "direction": "out",
            "href": f"/admin/cashbox/sessions/{session.id}", "when": entry.created_at,
            # Carried so the statement can offer the reversal on the row itself.
            "entry_id": entry.id, "reversed": entry.reversed_at is not None,
        })

    movements.sort(key=lambda row: row["when"])
    return {
        "session": session,
        "register": get_cashbox(db, start, end, session.opening_balance, session.id),
        "movements": movements,
    }


def add_cash_withdrawal(db, session: CashSession, amount: int, reason: str,
                        operator_user_id: int, request_id: str | None = None) -> CashSessionEntry:
    """Record cash leaving the drawer before the count.

    Not an expense: the money is still the shop's, it is simply no longer in the
    drawer (a bank deposit, a small cash purchase). It therefore lowers what
    should be counted without touching profit and loss.
    """
    from services.events import append_event

    entry = CashSessionEntry(
        cash_session_id=session.id, entry_type="withdrawal",
        amount=amount, reason=reason, operator_user_id=operator_user_id,
    )
    db.add(entry)
    db.flush()
    append_event(
        db, "CashSessionEntryRecorded", "cash_session_entry", entry.id,
        idempotency_key=f"cash-session-entry:{entry.id}:recorded",
        actor_user_id=operator_user_id, request_id=request_id,
        payload={"cash_session_id": session.id, "entry_type": entry.entry_type,
                 "amount": entry.amount, "reason": entry.reason},
        occurred_at=entry.created_at,
    )
    return entry


def reverse_cash_withdrawal(db, entry: CashSessionEntry, operator_user_id: int,
                            request_id: str | None = None) -> CashSessionEntry:
    """Undo a withdrawal entered by mistake.

    Only while its shift is open: a closed shift's expected and counted figures
    are the frozen record of a count somebody actually performed, and adding
    money back afterwards would rewrite a difference they signed off.
    """
    from services.events import append_event

    entry.reversed_at = datetime.now(timezone.utc)
    db.flush()
    append_event(
        db, "CashSessionEntryReversed", "cash_session_entry", entry.id,
        idempotency_key=f"cash-session-entry:{entry.id}:reversed",
        actor_user_id=operator_user_id, request_id=request_id,
        payload={"cash_session_id": entry.cash_session_id, "amount": entry.amount,
                 "reason": entry.reason},
        occurred_at=entry.reversed_at,
    )
    return entry


def get_supplier_balances(db) -> list:
    suppliers = db.query(Supplier).order_by(Supplier.name.asc()).all()
    result = []
    for supplier in suppliers:
        purchases = db.query(Purchase).filter(
            Purchase.supplier_id == supplier.id,
            Purchase.is_reversed == False,
            Purchase.is_draft == False,
        ).all()
        invoiced = sum(p.total_cost or 0 for p in purchases)
        paid = db.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).filter(
            SupplierPayment.supplier_id == supplier.id,
            SupplierPayment.reversed_at.is_(None),
        ).scalar() or 0
        result.append({"supplier": supplier, "invoiced": invoiced, "paid": paid,
                       "owed": max(0, invoiced - paid), "purchases": purchases})
    return result


def get_opening_balance(db) -> int:
    return get_setting_int(db, "cash_opening_balance", 0)


def debt_totals(db) -> dict:
    """Overall debt summary for the accounting dashboard."""
    # Counts match the credit page's KPI row rather than being counted twice in
    # two different ways.
    overview = summarise_debts(build_debt_rows(db))
    total_debt = db.query(func.coalesce(func.sum(Customer.total_debt), 0)) \
        .scalar() or 0
    total_purchases = db.query(func.coalesce(func.sum(Purchase.total_cost), 0)).filter(
        Purchase.is_reversed == False,
        Purchase.is_draft == False,
    ).scalar() or 0
    total_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.reversed_at.is_(None)).scalar() or 0
    return {
        "total_debt": total_debt,
        "total_purchases": total_purchases,
        "total_expenses": total_expenses,
        "debtor_count": overview["debtor_count"],
        "overdue_amount": overview["overdue_amount"],
        "overdue_customers": overview["overdue_customers"],
        "over_limit_count": overview["over_limit_count"],
    }
