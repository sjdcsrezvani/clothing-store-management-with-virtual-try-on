from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
from models import Customer, Sale, SaleItem, Product, ProductVariant, Referral, Expense, Payment, Purchase, Refund, SupplierPayment
from services._common import (
    parse_jalali_input,
    parse_jalali_input_end,
    gregorian_to_jalali,
    share,
    PERSIAN_DIGITS,
)
from services.tier import TIER_LABELS

DEFAULT_PERIOD = "month"
KNOWN_PERIODS = ("today", "week", "month", "year", "all", "custom")
# The shop's first day, used by «همه» and as the lower bound of an open range.
ALL_TIME_START = datetime(2020, 1, 1, tzinfo=timezone.utc)


class UnreadableRange(ValueError):
    """A range that was asked for and could not be read.

    Raised rather than answered with a different range. A request reading
    «banana» used to come back as the whole history — five years of trade under
    a filter that said something else — which is a figure that renders, looks
    deliberate and answers a question nobody asked.
    """


class PeriodWindow(NamedTuple):
    """What a range filter resolved to, and what the page must say about it."""
    start: datetime
    end: datetime
    period: str
    notice: str


def get_date_range(period: str, start_date: str = None, end_date: str = None):
    """Get start and end dates based on period. User-supplied dates are Persian.

    Refuses anything it cannot read rather than substituting a range: see
    :class:`UnreadableRange`, and use :func:`period_range` on a page, which
    answers with the default range *and* the sentence explaining why.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    if period == "today":
        start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "week":
        # Persian "week" starts on Saturday (weekday 5 in jdatetime Persian week order).
        # Convert via jdatetime so the boundary lines up with the user's calendar.
        import jdatetime
        jnow = jdatetime.date.today()
        start_greg = jdatetime.date(jnow.year, jnow.month, jnow.day) - jdatetime.timedelta(days=jnow.weekday())
        start = datetime.combine(start_greg.togregorian(), datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "month":
        # Persian month boundary (current Persian month → its Gregorian start).
        import jdatetime
        jnow = jdatetime.datetime.now()
        start_greg = jdatetime.date(jnow.year, jnow.month, 1).togregorian()
        start = datetime.combine(start_greg, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "year":
        import jdatetime
        jnow = jdatetime.datetime.now()
        start_greg = jdatetime.date(jnow.year, 1, 1).togregorian()
        start = datetime.combine(start_greg, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "custom":
        # Persian dates from the form → Gregorian for SQL.
        start = parse_jalali_input(start_date) if start_date else None
        end = parse_jalali_input_end(end_date) if end_date else None
        if start is None or end is None:
            raise UnreadableRange("تاریخ بازه خوانده نشد؛ این ماه نشان داده شده است.")
    elif period == "all":
        start = ALL_TIME_START
        end = now
    else:
        raise UnreadableRange(
            f"بازه «{period}» شناخته نشد؛ این ماه نشان داده شده است.")

    return start, end


def period_range(period: str, start_date: str = None, end_date: str = None):
    """The range a page should show, and what to say about the range asked for.

    Returns a :class:`PeriodWindow`: the dates, the period the page's own filter
    should show as selected — which is the default, not the value nobody could
    read — and a notice that is empty when nothing was refused. A page that
    shows a range without saying it was not the one requested is the same bug in
    a quieter form, and a filter whose select matches no option silently shows
    its first one.
    """
    try:
        start, end = get_date_range(period, start_date, end_date)
        return PeriodWindow(start, end, period, "")
    except UnreadableRange as problem:
        start, end = get_date_range(DEFAULT_PERIOD)
        return PeriodWindow(start, end, DEFAULT_PERIOD, str(problem))

def get_revenue_summary(db: Session, start: datetime, end: datetime) -> dict:
    """Get revenue summary for a date range."""
    # Get confirmed, non-refunded sales
    sales = db.query(Sale).filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).all()
    
    # Get refunded sales in period
    refunded = db.query(Sale).filter(
        Sale.is_refunded == True,
        Sale.refund_date.between(start, end)
    ).all()
    
    total_revenue = sum(s.final_amount for s in sales)
    total_refunded = sum(s.refund_amount for s in refunded)
    net_revenue = total_revenue - total_refunded
    
    # Calculate cost from sale items
    total_cost = 0
    for sale in sales:
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        total_cost += sum(item.unit_cost * item.quantity for item in items)
    
    # Refunded cost
    for sale in refunded:
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        total_cost -= sum(item.unit_cost * item.quantity for item in items)
    
    gross_profit = net_revenue - total_cost
    margin = share(gross_profit, net_revenue)
    
    invoice_count = len(sales)
    aov = (net_revenue / invoice_count) if invoice_count > 0 else 0
    # New customers in period
    new_customers = db.query(Customer).filter(
        Customer.created_at.between(start, end)
    ).count()
    
    return {
        "total_revenue": net_revenue,
        "gross_profit": gross_profit,
        # `share` already rounds and answers `None` for a period that sold nothing.
        "margin": margin,
        "invoice_count": max(0, invoice_count),
        "aov": round(aov),
        "new_customers": new_customers,
    }

def get_new_customers(db: Session, start: datetime, end: datetime) -> int:
    """Customers whose first confirmed, non-refunded purchase falls in range.

    A customer counts as new when they buy here for the first time in the
    window — not when their account row was created, which may predate any
    purchase by months.
    """
    bought_before = set(
        cid for (cid,) in db.query(Sale.customer_id).filter(
            Sale.payment_confirmed == True,  # noqa: E712
            Sale.is_refunded == False,  # noqa: E712
            Sale.customer_id != None,  # noqa: E711
            Sale.created_at < start,
        ).distinct().all()
    )
    bought_in = set(
        cid for (cid,) in db.query(Sale.customer_id).filter(
            Sale.payment_confirmed == True,  # noqa: E712
            Sale.is_refunded == False,  # noqa: E712
            Sale.customer_id != None,  # noqa: E711
            Sale.created_at.between(start, end),
        ).distinct().all()
    )
    return len([cid for cid in bought_in if cid not in bought_before])

def _net_sale_lines(db: Session, start: datetime, end: datetime):
    """Per-item net revenue and cost sharing canonical_report's exact semantics.

    One base the daily, category and margin readings all draw from, so the
    three cannot disagree with each other — or with سود و زیان:
    * the sale set is confirmed sales created in range, refunded ones included,
      exactly like canonical_report;
    * revenue is total_amount minus discount_amount (final_amount would smuggle
      the نسیه surcharge back in, which سود و زیان does not count as sales);
    * a matched refund subtracts Refund.total_amount — the same rows and the
      same window canonical_report's refund total reads;
    * cost drops flagged sales' items entirely, mirroring canonical cogs.
    A discount is spread across its sale's items pro-rata by item gross (even
    split on a zero gross), the integer remainder going to the largest item;
    a refund is spread the same way by item gross. The lines therefore add up
    to their sale's own net to the toman, and the readings add up to the books.
    Returns (item_lines, refund_lines, sales).
    """
    sales = db.query(Sale).filter(
        Sale.payment_confirmed == True,  # noqa: E712
        Sale.created_at.between(start, end),
    ).all()
    if not sales:
        return [], [], []
    sale_ids = [s.id for s in sales]
    refunds = db.query(Refund).filter(
        Refund.sale_id.in_(sale_ids),
        Refund.created_at.between(start, end),
    ).all()
    refund_by_sale = {}
    for refund in refunds:
        refund_by_sale.setdefault(refund.sale_id, []).append(refund)
    rows = db.query(
        SaleItem.sale_id, SaleItem.total_price,
        SaleItem.unit_cost, SaleItem.quantity,
        Product.category,
    ).join(Product, Product.id == SaleItem.product_id
           ).filter(SaleItem.sale_id.in_(sale_ids)).all()
    by_sale: dict[int, list[dict]] = {}
    for sid, gross, unit_cost, qty, cat in rows:
        by_sale.setdefault(sid, []).append({
            "gross": gross or 0,
            "cost": (unit_cost or 0) * (qty or 0),
            "qty": qty or 0,
            "category": cat,
        })

    def _split(amount: int, weights: list[int]) -> list[int]:
        total = sum(weights)
        if not weights:
            return []
        if total <= 0:
            base, remainder = divmod(amount, len(weights))
            shares = [base] * len(weights)
        else:
            shares = [amount * w // total for w in weights]
            remainder = amount - sum(shares)
        order = sorted(range(len(weights)), key=lambda k: weights[k], reverse=True)
        for position in range(remainder):
            shares[order[position % len(order)]] += 1
        return shares

    item_lines, refund_lines = [], []
    for sale in sales:
        sale_items = by_sale.get(sale.id, [])
        discount = sale.discount_amount or 0
        day = sale.created_at.date() if sale.created_at else start.date()
        if sale_items:
            grosses = [i["gross"] for i in sale_items]
            discounts = _split(discount, grosses)
            nets = [i["gross"] - d for i, d in zip(sale_items, discounts)]
            cost_off = bool(sale.is_refunded)
            for item, net in zip(sale_items, nets):
                item_lines.append({
                    "sale_id": sale.id, "day": day,
                    "category": item["category"],
                    "net": net,
                    "cost": 0 if cost_off else item["cost"],
                    "qty": 0 if cost_off else item["qty"],
                })
            refund_weights = grosses
        else:
            # A confirmed sale with no lines is pathological; its net still
            # belongs to the day, under no category.
            nets = [(sale.total_amount or 0) - discount]
            item_lines.append({
                "sale_id": sale.id, "day": day,
                "category": None,
                "net": nets[0],
                "cost": 0,
                "qty": 0,
            })
            refund_weights = []
        for refund in refund_by_sale.get(sale.id, []):
            amount = refund.total_amount or 0
            refund_day = refund.created_at.date() if refund.created_at else day
            parts = _split(amount, refund_weights) if refund_weights else [amount]
            cats = ([i["category"] for i in sale_items] if sale_items else [None])
            for cat, part in zip(cats, parts):
                refund_lines.append({
                    "sale_id": sale.id, "day": refund_day,
                    "category": cat, "amount": part,
                })
    return item_lines, refund_lines, sales


def get_daily_revenue(db: Session, start: datetime, end: datetime) -> list:
    """Get daily net revenue and profit.

    Two grouped reads (the sale set, its lines) no matter how long the range
    is, bucketed in Python: a day with no sales still gets its row, so the
    chart's axis stays continuous and a quiet day reads as quiet rather than
    missing. Refunds land on the day the money left; the range still adds up
    to سود و زیان by construction (see :func:`_net_sale_lines`).
    """
    item_lines, refund_lines, sales = _net_sale_lines(db, start, end)
    revenue: dict = {}
    cost: dict = {}
    for line in item_lines:
        revenue[line["day"]] = revenue.get(line["day"], 0) + line["net"]
        cost[line["day"]] = cost.get(line["day"], 0) + line["cost"]
    for line in refund_lines:
        revenue[line["day"]] = revenue.get(line["day"], 0) - line["amount"]
    counts: dict = {}
    for sale in sales:
        if sale.is_refunded or not sale.created_at:
            continue
        day = sale.created_at.date()
        counts[day] = counts.get(day, 0) + 1

    import jdatetime
    results = []
    current = start.date()
    end_date = end.date()
    while current <= end_date:
        day_revenue = revenue.get(current, 0)
        # Persian MM/DD label for the daily revenue chart.
        jd = jdatetime.date.fromgregorian(date=current)
        mm = jd.strftime("%m").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        dd = jd.strftime("%d").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        results.append({
            "date": f"{mm}/{dd}",
            "revenue": day_revenue,
            "profit": day_revenue - cost.get(current, 0),
            "count": counts.get(current, 0),
        })
        current += timedelta(days=1)
    return results

def _category_totals(db: Session, start: datetime, end: datetime) -> list[dict]:
    """Net revenue, cost and quantity per category — one aggregation both the
    category doughnut and the margin table draw from (see :func:`_net_sale_lines`
    for the semantics both share with سود و زیان)."""
    item_lines, refund_lines, _sales = _net_sale_lines(db, start, end)
    agg: dict[str, dict] = {}

    def _bucket(category):
        key = category or "بدون دسته"
        return agg.setdefault(key, {"category": key, "revenue": 0, "cost": 0, "quantity": 0})

    for line in item_lines:
        bucket = _bucket(line["category"])
        bucket["revenue"] += line["net"]
        bucket["cost"] += line["cost"]
        bucket["quantity"] += line["qty"]
    for line in refund_lines:
        _bucket(line["category"])["revenue"] -= line["amount"]
    for bucket in agg.values():
        bucket["profit"] = bucket["revenue"] - bucket["cost"]
    return sorted(agg.values(), key=lambda b: b["revenue"], reverse=True)


def get_revenue_by_category(db: Session, start: datetime, end: datetime) -> list:
    """Get net revenue by product category (discounts spread pro-rata,
    matched refunds subtracted — the range adds up to سود و زیان)."""
    return [
        {"category": b["category"], "revenue": b["revenue"],
         "profit": b["profit"], "quantity": b["quantity"]}
        for b in _category_totals(db, start, end)
    ]

def get_top_products(db: Session, start: datetime, end: datetime, limit: int = 10, sort_by: str = "revenue") -> list:
    """Get top products by revenue or profit."""
    results = db.query(
        Product.name,
        Product.category,
        func.sum(SaleItem.quantity).label("qty_sold"),
        func.sum(SaleItem.total_price).label("revenue"),
        func.sum(SaleItem.unit_cost * SaleItem.quantity).label("cost"),
    ).join(SaleItem, SaleItem.product_id == Product.id) \
     .join(Sale, Sale.id == SaleItem.sale_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).group_by(Product.id).all()
    
    products = []
    for r in results:
        revenue = r.revenue or 0
        cost = r.cost or 0
        profit = revenue - cost
        margin = share(profit, revenue)
        
        products.append({
            "name": r.name,
            "category": r.category or "—",
            "qty_sold": r.qty_sold or 0,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "margin": margin,
        })
    
    if sort_by == "profit":
        products.sort(key=lambda x: x["profit"], reverse=True)
    else:
        products.sort(key=lambda x: x["revenue"], reverse=True)
    
    return products[:limit]

def get_revenue_by_payment(db: Session, start: datetime, end: datetime) -> list:
    """Get revenue by payment method."""
    results = db.query(
        Sale.payment_method,
        func.sum(Sale.final_amount).label("revenue"),
        func.count(Sale.id).label("count"),
    ).filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).group_by(Sale.payment_method).all()
    
    return [{"method": r.payment_method, "revenue": r.revenue, "count": r.count} for r in results]

def get_revenue_by_tier(db: Session, start: datetime, end: datetime) -> list:
    """Get revenue by customer tier."""
    results = db.query(
        Customer.tier,
        func.sum(Sale.final_amount).label("revenue"),
        func.count(Sale.id).label("orders"),
        func.count(func.distinct(Sale.customer_id)).label("customers"),
    ).join(Customer, Customer.id == Sale.customer_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).group_by(Customer.tier).all()
    
    return [{"tier": r.tier, "label": TIER_LABELS.get(r.tier, r.tier or "بدون سطح"),
             "revenue": r.revenue, "orders": r.orders, "customers": r.customers} for r in results]

def get_monthly_comparison(db: Session, year: int, month: int) -> dict:
    """Compare current month with previous month."""
    current_start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        current_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        current_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_start = datetime(prev_year, prev_month, 1, tzinfo=timezone.utc)
    prev_end = current_start
    
    current = get_revenue_summary(db, current_start, current_end)
    previous = get_revenue_summary(db, prev_start, prev_end)
    
    change_pct = 0
    if previous["total_revenue"] > 0:
        change_pct = round((current["total_revenue"] - previous["total_revenue"]) / previous["total_revenue"] * 100, 1)
    
    return {
        "current": current,
        "previous": previous,
        "change_pct": change_pct,
    }

def get_discount_impact(db: Session, start: datetime, end: datetime) -> list:
    """Analyze discount impact."""
    sales = db.query(Sale).filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.discount_amount > 0,
        Sale.created_at.between(start, end)
    ).all()
    
    total_revenue = sum(s.final_amount for s in sales) if sales else 0
    total_discounts = sum(s.discount_amount for s in sales) if sales else 0
    
    # Parse discount details to categorize
    discount_types = {}
    for sale in sales:
        if sale.discount_details:
            try:
                import json
                details = json.loads(sale.discount_details)
                for detail in details:
                    # Simple categorization based on text
                    if "معرفی شده" in detail:
                        key = "تخفیف معرفی شده"
                    elif "معرفی دیگران" in detail:
                        key = "تخفیف معرفی"
                    elif "تولد" in detail:
                        key = "تخفیف تولد"
                    else:
                        key = "تخفیف سطح"
                    
                    discount_types[key] = discount_types.get(key, 0) + sale.discount_amount // len(details)
            except:
                pass
    
    return [{"type": k, "total_amount": v, "pct_of_revenue": share(v, total_revenue)}
            for k, v in sorted(discount_types.items(), key=lambda x: x[1], reverse=True)]

def get_top_customers(db: Session, start: datetime, end: datetime, limit: int = 10) -> list:
    """Get top customers by spending."""
    results = db.query(
        Customer.first_name,
        Customer.last_name,
        Customer.phone,
        Customer.tier,
        func.sum(Sale.final_amount).label("total_spent"),
        func.count(Sale.id).label("orders"),
    ).join(Customer, Customer.id == Sale.customer_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).group_by(Customer.id) \
     .order_by(func.sum(Sale.final_amount).desc()) \
     .limit(limit).all()
    
    return [{"name": f"{r.first_name or ''} {r.last_name or ''}".strip() or "—", "phone": r.phone, "tier": r.tier, "total_spent": r.total_spent, "orders": r.orders} for r in results]

# ----- Garment floor — pricing / color / size / stock -----------------------
#
# These are the charts a clothes shop reads every day: what do I actually sell
# in each colour and size, what's my real margin, and what's sitting in stock.

def get_categories(db):
    """Distinct product categories, for the filter dropdown."""
    rows = db.query(Product.category).filter(Product.category != None).distinct().all()
    return [r[0] for r in rows if r[0]]

def get_price_stats(db, start, end, category=None):
    """Actual average selling price, cost, and profit per unit sold, in range.
    Uses the real sold line prices (unit_price / unit_cost), not the price list."""
    q = db.query(
        func.sum(SaleItem.total_price).label("rev"),
        func.sum(SaleItem.unit_cost * SaleItem.quantity).label("cost"),
        func.sum(SaleItem.quantity).label("qty"),
    ).join(Sale, Sale.id == SaleItem.sale_id) \
     .join(Product, Product.id == SaleItem.product_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    )
    if category:
        q = q.filter(Product.category == category)
    row = q.first()
    qty = row.qty or 0
    avg_price = (row.rev or 0) / qty if qty else 0
    avg_cost = (row.cost or 0) / qty if qty else 0
    return {
        "avg_price": round(avg_price),
        "avg_cost": round(avg_cost),
        "avg_profit": round(avg_price - avg_cost),
    }

def get_variant_stats(db, start, end, attr, category=None):
    """Units sold broken down by variant attribute ('size' or 'color'), sorted.
    Which colour/size actually sells for a category (or everything)."""
    col = ProductVariant.color if attr == "color" else ProductVariant.size
    q = db.query(
        col.label("label"),
        func.sum(SaleItem.quantity).label("quantity"),
        func.sum(SaleItem.total_price).label("revenue"),
    ).join(Sale, Sale.id == SaleItem.sale_id) \
     .join(ProductVariant, ProductVariant.id == SaleItem.variant_id) \
     .join(Product, Product.id == SaleItem.product_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
        col != None,
    )
    if category:
        q = q.filter(Product.category == category)
    rows = q.group_by(col).all()
    rows = sorted(rows, key=lambda r: r.quantity or 0, reverse=True)
    return [{"label": r.label or "—", "quantity": r.quantity or 0, "revenue": r.revenue or 0    } for r in rows]


def get_color_size_matrix(db, start, end, category=None):
    """Units sold as a colour×size matrix (heatmap) — the garment restock view."""
    rows = db.query(
        ProductVariant.color, ProductVariant.size,
        func.sum(SaleItem.quantity).label("quantity"),
    ).join(Sale, Sale.id == SaleItem.sale_id) \
     .join(ProductVariant, ProductVariant.id == SaleItem.variant_id) \
     .join(Product, Product.id == SaleItem.product_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
        ProductVariant.color != None,
        ProductVariant.size != None,
    )
    if category:
        rows = rows.filter(Product.category == category)
    # Grouped, or this is not a matrix. Without it SQLite answers an aggregate
    # query with one synthesised row — the arbitrary colour and size of some row
    # in the table, and the total quantity of everything joined — so the heatmap
    # showed a single cell however much the shop had sold, and on a shop that had
    # sold nothing it showed «None» down the page: a table that renders, looks
    # deliberate and is wrong twice over. Every other reading in this module
    # groups; this one did not, because one row still looks like a plausible
    # matrix.
    rows = rows.group_by(ProductVariant.color, ProductVariant.size)
    cells = {}
    colors, sizes = [], []
    for color, size, quantity in rows.all():
        if color not in cells:
            cells[color] = {}
            colors.append(color)
        if size not in sizes:
            sizes.append(size)
        cells[color][size] = quantity or 0
    max_qty = max((q for cell in cells.values() for q in cell.values()), default=0)
    matrix_rows = []
    for size in sizes:
        matrix_rows.append({
            "size": size,
            "cells": [{
                "qty": cells.get(color, {}).get(size, 0),
                "pct": round(cells[color].get(size, 0) / max_qty * 100) if max_qty else 0,
            } for color in colors],
        })
    return {"colors": colors, "rows": matrix_rows, "max": max_qty}

def get_inventory_value(db):
    """Total stock cost & retail value, and per-category breakdown."""
    variants = db.query(ProductVariant, Product.category).join(Product).filter(
        ProductVariant.is_active == True,
        Product.is_active == True,
    ).all()
    total_cost = sum(v.cost_price * v.stock_quantity for v, _ in variants)
    total_retail = sum(v.price * v.stock_quantity for v, _ in variants)
    by_cat = {}
    for v, cat in variants:
        key = cat or "بدون دسته"
        d = by_cat.setdefault(key, {"cost": 0, "retail": 0, "units": 0})
        d["cost"] += v.cost_price * v.stock_quantity
        d["retail"] += v.price * v.stock_quantity
        d["units"] += v.stock_quantity
    cat_list = [{"category": k, **v} for k, v in sorted(by_cat.items(), key=lambda x: x[1]["retail"], reverse=True)]
    # Low stock: active variants with less than 5 units left.
    low = db.query(ProductVariant, Product.name).join(Product).filter(
        ProductVariant.is_active == True,
        Product.is_active == True,
        ProductVariant.stock_quantity > 0,
        ProductVariant.stock_quantity <= 5,
    ).order_by(ProductVariant.stock_quantity.asc()).limit(12).all()
    low_list = [{
        "name": name,
        "size": v.size or "",
        "color": v.color or "",
        "stock": v.stock_quantity,
    } for v, name in low]
    return {
        "total_cost": total_cost,
        "total_retail": total_retail,
        "units": sum(v.stock_quantity for v, _ in variants),
        "categories": cat_list,
        "low_stock": low_list,
    }

# ----- Expert tier — where the money actually is ------------------------------
#
# ABC paradox (Pareto), inventory sell-through, sales-time patterns and customer
# health. These are the four numbers a merchandiser reads before ordering stock.

def get_abc_products(db, start, end):
    """Classify products into A/B/C by revenue contribution in range.
    A = the handful driving ~80% of revenue — protect stock, never run out.
    C = long tail — don't reorder until demanded."""
    rows = db.query(
        Product.id,
        Product.name,
        Product.category,
        func.sum(SaleItem.quantity).label("qty"),
        func.sum(SaleItem.total_price).label("revenue"),
        func.sum(SaleItem.unit_cost * SaleItem.quantity).label("cost"),
    ).join(SaleItem, SaleItem.product_id == Product.id) \
     .join(Sale, Sale.id == SaleItem.sale_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).group_by(Product.id).all()

    total_rev = sum(r.revenue or 0 for r in rows) or 1
    rows = sorted(rows, key=lambda r: r.revenue or 0, reverse=True)
    items, cum = [], 0
    for r in rows:
        rev = r.revenue or 0
        cost = r.cost or 0
        cum += rev
        cumulative = cum / total_rev * 100
        cls = "A" if cumulative <= 80 else ("B" if cumulative <= 95 else "C")
        items.append({
            "name": r.name,
            "category": r.category or "—",
            "qty": r.qty or 0,
            "revenue": rev,
            "profit": rev - cost,
            "margin": share(rev - cost, rev),
            "share": round(cumulative, 1),
            "class": cls,
        })
    a_count = sum(1 for i in items if i["class"] == "A")
    a_rev = sum(i["revenue"] for i in items if i["class"] == "A")
    return {
        "products": items,
        "total_rev": total_rev,
        "a_count": a_count,
        "a_rev": a_rev,
        "a_pct": round(a_rev / total_rev * 100),
    }

def get_sales_pattern(db, start, end):
    """Sales split by weekday and by hour of day."""
    from jdatetime import datetime as jdt
    weekday_names = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
    sales = db.query(Sale.created_at, Sale.final_amount).filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).all()
    weekdays = [0] * 7
    hours = dict((h, 0) for h in range(24))
    for created, amount in sales:
        if created is None: continue
        jd = jdt.fromtimestamp(created.timestamp())
        weekdays[jd.weekday()] += amount or 0
        hours[jd.hour] += amount or 0
    return {
        "weekdays": [{"day": weekday_names[i], "revenue": weekdays[i]} for i in range(7)],
        # The hour is the shop's clock, not a clock face twice round: «8:00» used
        # to stand for both the morning and the evening on the same axis, so a
        # peak at eight was two different bars with one name between them.
        "hours": [{"hour": h, "label": f"{h}:00", "revenue": hours[h]} for h in range(8, 22)]
    }

# ----- Expert tier — deep analytics ----------------------------------------

def get_sell_through(db, start, end):
    """What % of stock actually sold. The single number that tells you
    if the shop is moving clothes or hoarding them."""
    variants = db.query(
        ProductVariant.id,
        ProductVariant.stock_quantity,
        ProductVariant.cost_price,
        ProductVariant.price,
    ).join(Product, Product.id == ProductVariant.product_id).filter(
        ProductVariant.is_active == True,
        Product.is_active == True,
        ProductVariant.stock_quantity > 0,
    ).all()
    total_stock = sum(v.stock_quantity for v in variants)
    stock_cost = sum(v.cost_price * v.stock_quantity for v in variants)
    stock_retail = sum(v.price * v.stock_quantity for v in variants)
    sold = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).scalar() or 0

    # Named for what it is: the local used to be called `pct` and shadow the
    # helper, which is how a share of nothing came to be written out by hand.
    sold_share = share(sold, total_stock + sold, 0)
    return {
        "sold": sold,
        "stock": total_stock,
        "pct": sold_share,
        "stock_cost": stock_cost,
        "stock_retail": stock_retail,
    }

def get_revenue_trend(db):
    """Monthly revenue for the last 12 months — the growth line."""
    import jdatetime
    now = jdatetime.datetime.now()
    months = []
    for i in range(11, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        g_start = jdatetime.date(y, m, 1).togregorian()
        if m == 12:
            g_end = jdatetime.date(y + 1, 1, 1).togregorian()
        else:
            g_end = jdatetime.date(y, m + 1, 1).togregorian()
        s = datetime.combine(g_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        e = datetime.combine(g_end, datetime.min.time()).replace(tzinfo=timezone.utc)
        rev = db.query(func.sum(Sale.final_amount)).filter(
            Sale.payment_confirmed == True, Sale.is_refunded == False,
            Sale.created_at.between(s, e),
        ).scalar() or 0
        profit = db.query(
            func.sum(SaleItem.total_price) - func.sum(SaleItem.unit_cost * SaleItem.quantity)
        ).join(Sale).filter(
            Sale.payment_confirmed == True, Sale.is_refunded == False,
            Sale.created_at.between(s, e),
        ).scalar() or 0
        months.append({"month": f"{m}/{y}", "revenue": rev, "profit": profit})
    return months

def get_basket_stats(db, start, end):
    """Average items per transaction and revenue per item.
    Tells you if people buy one thing or fill a bag."""
    sales = db.query(
        Sale.id,
        Sale.final_amount,
    ).filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).all()
    if not sales:
        return {"aov": 0, "items_per_txn": 0, "rev_per_item": 0, "total_items": 0}
    sale_ids = [s.id for s in sales]
    items = db.query(
        SaleItem.sale_id,
        func.sum(SaleItem.quantity).label("qty"),
    ).filter(SaleItem.sale_id.in_(sale_ids)).group_by(SaleItem.sale_id).all()
    qty_map = {sid: q for sid, q in items}
    total_items = sum(qty_map.values())
    total_rev = sum(s.final_amount for s in sales)
    txn_count = len(sales)
    return {
        "aov": round(total_rev / txn_count) if txn_count else 0,
        "items_per_txn": round(total_items / txn_count, 1) if txn_count else 0,
        "rev_per_item": round(total_rev / total_items) if total_items else 0,
        "total_items": total_items,
    }

def get_customer_health(db, start, end):
    """Repeat purchase rate and customer distribution.
    A shop survives on repeat customers, not one-timers."""
    customers_in = db.query(
        Sale.customer_id,
        func.count(Sale.id).label("orders"),
        func.sum(Sale.final_amount).label("spent"),
    ).filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
        Sale.customer_id != None,
    ).group_by(Sale.customer_id).all()
    if not customers_in:
        # No customers in the period means no rate to report: `None` asks the page
        # for «—», where a nought would claim the shop measured something.
        return {"repeat_rate": None, "one_timer_pct": None, "avg_orders": None,
                "segments": []}
    total = len(customers_in)
    repeats = sum(1 for c in customers_in if c.orders > 1)
    avg_orders = sum(c.orders for c in customers_in) / total
    # Revenue segments. The bands are named in the shop's own words because what
    # the ring's legend shows the owner is the band, not the bucket.
    segment_labels = {"0-500k": "تا 500 هزار", "500k-1m": "تا 1 میلیون",
                      "1m-2m": "1 تا 2 میلیون", "2m+": "بیش از 2 میلیون"}
    segments = {"0-500k": 0, "500k-1m": 0, "1m-2m": 0, "2m+": 0}
    for c in customers_in:
        s = c.spent or 0
        if s < 500000: segments["0-500k"] += 1
        elif s < 1000000: segments["500k-1m"] += 1
        elif s < 2000000: segments["1m-2m"] += 1
        else: segments["2m+"] += 1
    return {
        "repeat_rate": share(repeats, total, 0),
        "one_timer_pct": share(total - repeats, total, 0),
        "avg_orders": round(avg_orders, 1),
        "segments": [{"label": segment_labels[k], "count": v} for k, v in segments.items()],
    }

def get_margin_by_category(db, start, end):
    """Margin % per category — which category actually makes money, net of
    discounts and matched refunds like the category doughnut beside it."""
    result = []
    for b in _category_totals(db, start, end):
        rev, cost = b["revenue"], b["cost"]
        result.append({
            "category": b["category"],
            "revenue": rev,
            "cost": cost,
            "profit": b["profit"],
            "margin": share(b["profit"], rev),
            "qty": b["quantity"],
        })
    # A category that sold only free items has no margin to sort by; `None` sorts
    # below every figure rather than raising the page away.
    return sorted(result, key=lambda x: -1e9 if x["margin"] is None else x["margin"],
                  reverse=True)

def get_dead_stock(db, start, end, days_threshold=90):
    """Variants sitting in stock with zero sales in the period.
    These are tying up capital with zero return."""
    sold_vids = db.query(SaleItem.variant_id).join(Sale).filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
        SaleItem.variant_id != None,
    ).distinct().all()
    sold_set = {vid for (vid,) in sold_vids}
    variants = db.query(ProductVariant, Product.name, Product.category).join(Product).filter(
        ProductVariant.is_active == True,
        Product.is_active == True,
        ProductVariant.stock_quantity > 0,
    ).all()
    dead = []
    for v, name, cat in variants:
        if v.id not in sold_set and v.stock_quantity > 0:
            dead.append({
                "name": name,
                "category": cat or "—",
                "size": v.size or "",
                "color": v.color or "",
                "stock": v.stock_quantity,
                "value": v.cost_price * v.stock_quantity,
                "retail": v.price * v.stock_quantity,
            })
    dead.sort(key=lambda x: x["value"], reverse=True)
    total_dead_value = sum(d["value"] for d in dead)
    return {"variants": dead[:20], "total_value": total_dead_value, "count": len(dead)}

def get_price_distribution(db, start, end):
    """What price points are customers actually paying."""
    rows = db.query(
        SaleItem.unit_price,
        func.sum(SaleItem.quantity).label("qty"),
        func.sum(SaleItem.total_price).label("rev"),
    ).join(Sale).filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).group_by(SaleItem.unit_price).order_by(SaleItem.unit_price).all()
    buckets = {}
    for price, qty, rev in rows:
        bucket = round((price or 0) / 100000) * 100000
        if bucket not in buckets:
            buckets[bucket] = {"price": bucket, "quantity": 0, "revenue": 0}
        buckets[bucket]["quantity"] += qty or 0
        buckets[bucket]["revenue"] += rev or 0
    return sorted(buckets.values(), key=lambda x: x["price"])

def get_top_selling_variants(db, start, end, limit=10):
    """Top selling specific variants (product + size + color).
    The actual items flying off the shelves."""
    rows = db.query(
        Product.name,
        ProductVariant.size,
        ProductVariant.color,
        func.sum(SaleItem.quantity).label("qty"),
        func.sum(SaleItem.total_price).label("revenue"),
    ).join(ProductVariant, ProductVariant.id == SaleItem.variant_id) \
     .join(Product, Product.id == SaleItem.product_id) \
     .join(Sale, Sale.id == SaleItem.sale_id) \
     .filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).group_by(ProductVariant.id).order_by(func.sum(SaleItem.quantity).desc()) \
     .limit(limit).all()
    return [{
        "name": r.name,
        "size": r.size or "",
        "color": r.color or "",
        "qty": r.qty or 0,
        "revenue": r.revenue or 0,
        } for r in rows]
