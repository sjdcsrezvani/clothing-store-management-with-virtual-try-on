"""Accounting-lite: net profit & loss, cash box register, customer debt
(نسیه) ledger, FIFO settlement of credit-sale payments, credit surcharge,
and aged-receivables (collection) dashboard."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import func

from models import (
    Customer, Sale, SaleItem, Expense, Purchase, PurchaseItem, Payment, Supplier, SupplierPayment, CashSession,
)
from services._common import get_setting_int


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
    by_cat: dict[str, int] = {}
    for e in expenses:
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
        "gross_margin": round(gross / revenue * 100, 1) if revenue else 0,
        "expenses": total_expenses,
        "expense_cats": expense_cats,
        "net": gross - total_expenses,
        "net_margin": round((gross - total_expenses) / revenue * 100, 1) if revenue else 0,
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


def get_customer_debts(db) -> list:
    """Customers who owe money (نسیه), plus their unpaid credit sales."""
    customers = db.query(Customer).filter(
        Customer.total_debt > 0
    ).order_by(Customer.total_debt.desc()).all()
    result = []
    for customer in customers:
        unpaid = db.query(Sale).filter(
            Sale.customer_id == customer.id,
            Sale.payment_method == "credit",
            Sale.is_refunded == False,
            Sale.credit_settled == False,
        ).order_by(Sale.created_at.asc()).all()
        limit = get_credit_limit(db, customer)
        debt = customer.total_debt or 0
        result.append({
            "customer": customer,
            "debt": debt,
            "limit": limit,
            "over_limit": limit > 0 and debt > limit,
            "unpaid_sales": unpaid,
            "payments": db.query(Payment).filter(Payment.customer_id == customer.id)
                .order_by(Payment.created_at.desc()).limit(5).all(),
        })
    return result


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
    request_id: str | None = None,
) -> int:
    """Record a payment toward a customer's نسیه debt.

    Applied FIFO across their oldest unpaid credit sales; a `payments` row is
    created for the total actually applied. Returns the applied amount (0 when
    the customer had no debt)."""
    amount = max(0, amount)
    if amount <= 0:
        return 0

    unpaid = db.query(Sale).filter(
        Sale.customer_id == customer.id,
        Sale.payment_method == "credit",
        Sale.is_refunded == False,
        Sale.credit_settled == False,
    ).order_by(Sale.created_at.asc()).all()

    applied = 0
    for sale in unpaid:
        remaining = sale.final_amount - (sale.credit_paid_amount or 0)
        if remaining <= 0:
            sale.credit_settled = True
            continue
        pay = min(remaining, amount - applied)
        if pay > 0:
            sale.credit_paid_amount = (sale.credit_paid_amount or 0) + pay
            applied += pay
            if sale.credit_paid_amount >= sale.final_amount:
                sale.credit_settled = True
        if applied >= amount:
            break

    if applied > 0:
        open_session = db.query(CashSession).filter(CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
        payment = Payment(
            customer_id=customer.id,
            sale_id=sale_id,
            amount=applied,
            method=method,
            cash_session_id=open_session.id if open_session and method == "cash" else None,
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

    remaining_payments = db.query(Payment).filter(
        Payment.customer_id == customer.id,
        Payment.reversed_at.is_(None),
    ).order_by(Payment.created_at.asc(), Payment.id.asc()).all()

    for pay_row in remaining_payments:
        applied = 0
        for sale in sales:
            rem = sale.final_amount - (sale.credit_paid_amount or 0)
            if rem <= 0:
                sale.credit_settled = True
                continue
            take = min(rem, (pay_row.amount or 0) - applied)
            if take > 0:
                sale.credit_paid_amount = (sale.credit_paid_amount or 0) + take
                applied += take
                if sale.credit_paid_amount >= sale.final_amount:
                    sale.credit_settled = True
            if applied >= (pay_row.amount or 0):
                break

    customer.total_debt = max(0, sum(
        s.final_amount - (s.credit_paid_amount or 0) for s in sales
    ))
    return amount


def get_cashbox(db, start, end, opening_balance: int, cash_session_id: int | None = None) -> dict:
    """Cash register for a period, optionally scoped to one cash session."""
    sale_filter = [Sale.payment_confirmed == True, Sale.is_refunded == False, Sale.payment_method == "cash", Sale.created_at.between(start, end)]
    payment_filter = [Payment.method == "cash", Payment.reversed_at.is_(None), Payment.created_at.between(start, end)]
    refund_filter = [Sale.is_refunded == True, Sale.payment_method == "cash", Sale.refund_date.between(start, end)]
    expense_filter = [Expense.reversed_at.is_(None), Expense.created_at.between(start, end)]
    supplier_filter = [SupplierPayment.method == "cash", SupplierPayment.reversed_at.is_(None), SupplierPayment.created_at.between(start, end)]
    if cash_session_id is not None:
        session = db.query(CashSession).filter(CashSession.id == cash_session_id).first()
        if session:
            sale_filter.append(Sale.created_at >= session.opened_at)
            payment_filter.append(Payment.created_at >= session.opened_at)
            refund_filter.append(Sale.cash_session_id == cash_session_id)
            expense_filter.append(Expense.cash_session_id == cash_session_id)
            supplier_filter.append(SupplierPayment.cash_session_id == cash_session_id)

    cash_in_sales = db.query(func.coalesce(func.sum(Sale.final_amount), 0)).filter(*sale_filter).scalar() or 0
    cash_in_payments = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(*payment_filter).scalar() or 0
    cash_out_refunds = db.query(func.coalesce(func.sum(Sale.refund_amount), 0)).filter(*refund_filter).scalar() or 0
    cash_out_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(*expense_filter).scalar() or 0
    cash_out_purchases = db.query(func.coalesce(func.sum(Purchase.total_cost), 0)).filter(
        Purchase.created_at.between(start, end), Purchase.is_reversed == False,
    ).scalar() or 0
    cash_out_supplier_payments = db.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).filter(*supplier_filter).scalar() or 0

    cash_in = cash_in_sales + cash_in_payments
    cash_out = cash_out_refunds + cash_out_expenses + cash_out_purchases + cash_out_supplier_payments
    closing = opening_balance + cash_in - cash_out
    return {
        "opening": opening_balance,
        "cash_sales": cash_in_sales,
        "credit_payments": cash_in_payments,
        "cash_in": cash_in,
        "refunds": cash_out_refunds,
        "expenses": cash_out_expenses,
        "purchases": cash_out_purchases,
        "supplier_payments": cash_out_supplier_payments,
        "cash_out": cash_out,
        "closing": closing,
    }


def get_aged_receivables(db, now: datetime | None = None) -> dict:
    """Aged-receivables (collection) report: bucket every customer's
    outstanding نسیه balance by how long the oldest unpaid invoice has aged.

    Buckets: current (≤30d), 31–60, 61–90, 90+ days. Each customer appears in
    exactly one bucket (their oldest unpaid invoice's age decides it), with
    their full outstanding balance and the invoice count.
    """
    now = now or datetime.now(timezone.utc)
    d30 = now - timedelta(days=30)
    d60 = now - timedelta(days=60)
    d90 = now - timedelta(days=90)

    customers = db.query(Customer).filter(Customer.total_debt > 0).all()
    buckets = {"current": [], "31_60": [], "61_90": [], "over_90": []}
    totals = {"current": 0, "31_60": 0, "61_90": 0, "over_90": 0}

    for customer in customers:
        unpaid = db.query(Sale).filter(
            Sale.customer_id == customer.id,
            Sale.payment_method == "credit",
            Sale.is_refunded == False,
            Sale.credit_settled == False,
        ).order_by(Sale.created_at.asc()).all()
        if not unpaid:
            continue
        # Age by the oldest unsettled invoice.
        oldest = unpaid[0].created_at
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        debt = customer.total_debt or 0
        entry = {
            "customer": customer,
            "debt": debt,
            "invoice_count": len(unpaid),
            "oldest_date": oldest,
            "oldest_unpaid": unpaid,
        }
        if oldest >= d30:
            buckets["current"].append(entry)
            totals["current"] += debt
        elif oldest >= d60:
            buckets["31_60"].append(entry)
            totals["31_60"] += debt
        elif oldest >= d90:
            buckets["61_90"].append(entry)
            totals["61_90"] += debt
        else:
            buckets["over_90"].append(entry)
            totals["over_90"] += debt

    grand_total = sum(totals.values())
    return {
        "buckets": buckets,
        "totals": totals,
        "grand_total": grand_total,
        "now": now,
    }


def get_supplier_balances(db) -> list:
    suppliers = db.query(Supplier).order_by(Supplier.name.asc()).all()
    result = []
    for supplier in suppliers:
        purchases = db.query(Purchase).filter(
            Purchase.supplier_id == supplier.id,
            Purchase.is_reversed == False,
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
    total_debt = db.query(func.coalesce(func.sum(Customer.total_debt), 0)) \
        .scalar() or 0
    total_purchases = db.query(func.coalesce(func.sum(Purchase.total_cost), 0)).filter(
        Purchase.is_reversed == False,
    ).scalar() or 0
    total_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.reversed_at.is_(None)).scalar() or 0
    return {
        "total_debt": total_debt,
        "total_purchases": total_purchases,
        "total_expenses": total_expenses,
    }
