from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func

from models import Customer, Expense, Payment, Purchase, ProductVariant, Refund, Sale, SaleItem, StockMovement


def canonical_report(db, start: datetime, end: datetime) -> dict:
    sales = db.query(Sale).filter(Sale.payment_confirmed.is_(True), Sale.created_at.between(start, end)).all()
    active_sales = [sale for sale in sales if not sale.is_refunded]
    gross_sales = sum(sale.total_amount or 0 for sale in sales)
    discounts = sum(sale.discount_amount or 0 for sale in sales)
    refunds = db.query(func.coalesce(func.sum(Refund.total_amount), 0)).filter(Refund.created_at.between(start, end)).scalar() or 0
    net_sales = gross_sales - discounts - refunds
    sale_ids = [sale.id for sale in sales]
    cogs = db.query(func.coalesce(func.sum(SaleItem.unit_cost * SaleItem.quantity), 0)).filter(SaleItem.sale_id.in_(sale_ids)).scalar() if sale_ids else 0
    cogs = (cogs or 0) - sum(sum(item.unit_cost * item.quantity for item in sale.items) for sale in sales if sale.is_refunded)
    expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.reversed_at.is_(None), Expense.created_at.between(start, end)).scalar() or 0
    cash_collected = sum(sale.final_amount or 0 for sale in active_sales if sale.payment_method in {"cash", "card"})
    credit_issued = sum(sale.final_amount or 0 for sale in active_sales if sale.payment_method == "credit")
    credit_collected = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.reversed_at.is_(None), Payment.created_at.between(start, end)).scalar() or 0
    debt = db.query(func.coalesce(func.sum(Customer.total_debt), 0)).scalar() or 0
    inventory = db.query(ProductVariant).filter(ProductVariant.is_active.is_(True)).all()
    inventory_value = sum((variant.cost_price or 0) * (variant.stock_quantity or 0) for variant in inventory)
    gross_profit = net_sales - cogs
    return {
        "gross_sales": gross_sales, "discounts": discounts, "refunds": refunds,
        "net_sales": net_sales, "cogs": cogs, "gross_profit": gross_profit,
        "operating_expenses": expenses, "net_profit": gross_profit - expenses,
        "cash_collected": cash_collected, "credit_issued": credit_issued,
        "credit_collected": credit_collected, "outstanding_debt": debt,
        "inventory_value": inventory_value, "sale_count": len(active_sales),
        "gross_margin": round(gross_profit / net_sales * 100, 1) if net_sales else 0,
    }


def reconciliation_checks(db, start: datetime, end: datetime) -> dict:
    report = canonical_report(db, start, end)
    credit_outstanding = report["credit_issued"] - report["credit_collected"]
    sales_balance = report["cash_collected"] + credit_outstanding - report["refunds"]
    movement_balance = db.query(func.coalesce(func.sum(StockMovement.quantity_delta), 0)).scalar() or 0
    inventory_balance = db.query(func.coalesce(func.sum(ProductVariant.stock_quantity), 0)).scalar() or 0
    credit_sales = db.query(func.coalesce(func.sum(Sale.final_amount), 0)).filter(Sale.payment_method == "credit", Sale.payment_confirmed.is_(True)).scalar() or 0
    credit_payments = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.reversed_at.is_(None)).scalar() or 0
    expected_debt = max(0, credit_sales - credit_payments)
    checks = {
        "sales_balance": {"expected": report["net_sales"], "actual": sales_balance, "difference": report["net_sales"] - sales_balance},
        "inventory_balance": {"expected": movement_balance, "actual": inventory_balance, "difference": movement_balance - inventory_balance},
        "customer_debt": {"expected": expected_debt, "actual": report["outstanding_debt"], "difference": expected_debt - report["outstanding_debt"]},
    }
    return {"checks": checks, "has_discrepancies": any(item["difference"] != 0 for item in checks.values())}
