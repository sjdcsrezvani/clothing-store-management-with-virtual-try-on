from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
from models import Customer, Sale, SaleItem, Product, ProductVariant, Referral, Expense, Payment, Purchase, Refund, SupplierPayment
from services._common import (
    parse_jalali_input,
    parse_jalali_input_end,
    gregorian_to_jalali,
    PERSIAN_DIGITS,
)

def get_date_range(period: str, start_date: str = None, end_date: str = None):
    """Get start and end dates based on period. User-supplied dates are Persian."""
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
    elif period == "custom" and start_date and end_date:
        # Persian dates from the form → Gregorian for SQL.
        start = parse_jalali_input(start_date)
        end = parse_jalali_input_end(end_date)
        if start is None or end is None:
            # Fallback: treat the whole history range so we don't 500 on bad input.
            start = datetime(2020, 1, 1, tzinfo=timezone.utc)
            end = now
    else:  # all
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = now

    return start, end

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
    margin = (gross_profit / net_revenue * 100) if net_revenue > 0 else 0
    
    invoice_count = len(sales)
    aov = (net_revenue / invoice_count) if invoice_count > 0 else 0
    # New customers in period
    new_customers = db.query(Customer).filter(
        Customer.created_at.between(start, end)
    ).count()
    
    return {
        "total_revenue": net_revenue,
        "gross_profit": gross_profit,
        "margin": round(margin, 1),
        "invoice_count": max(0, invoice_count),
        "aov": round(aov),
        "new_customers": new_customers,
    }

def get_daily_revenue(db: Session, start: datetime, end: datetime) -> list:
    """Get daily revenue and profit."""
    results = []
    current = start.date()
    end_date = end.date()
    
    while current <= end_date:
        day_start = datetime.combine(current, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end = datetime.combine(current, datetime.max.time()).replace(tzinfo=timezone.utc)
        
        sales = db.query(Sale).filter(
            Sale.payment_confirmed == True,
            Sale.is_refunded == False,
            Sale.created_at.between(day_start, day_end)
        ).all()
        
        revenue = sum(s.final_amount for s in sales)
        
        cost = 0
        for sale in sales:
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            cost += sum(item.unit_cost * item.quantity for item in items)
        
        profit = revenue - cost
        
        # Persian MM/DD label for the daily revenue chart.
        import jdatetime
        jd = jdatetime.date.fromgregorian(date=current)
        mm = jd.strftime("%m").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        dd = jd.strftime("%d").translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        results.append({
            "date": f"{mm}/{dd}",
            "revenue": revenue,
            "profit": profit,
            "count": len(sales),
        })
        
        current += timedelta(days=1)
    
    return results

def get_revenue_by_category(db: Session, start: datetime, end: datetime) -> list:
    """Get revenue by product category."""
    results = db.query(
        Product.category,
        func.sum(SaleItem.total_price).label("revenue"),
        func.sum(SaleItem.quantity).label("quantity"),
    ).join(SaleItem, SaleItem.product_id == Product.id) \
     .join(Sale, Sale.id == SaleItem.sale_id) \
     .filter(
        Sale.payment_confirmed == True,
        Sale.is_refunded == False,
        Sale.created_at.between(start, end)
    ).group_by(Product.category).all()
    
    categories = []
    for r in results:
        cat = r.category or "بدون دسته"
        revenue = r.revenue or 0
        
        # Calculate cost for this category
        cost = db.query(func.sum(SaleItem.unit_cost * SaleItem.quantity)) \
            .join(Sale, Sale.id == SaleItem.sale_id) \
            .join(Product, Product.id == SaleItem.product_id) \
            .filter(
                Sale.payment_confirmed == True,
                Sale.is_refunded == False,
                Sale.created_at.between(start, end),
                Product.category == r.category
            ).scalar() or 0
        
        categories.append({
            "category": cat,
            "revenue": revenue,
            "profit": revenue - cost,
            "quantity": r.quantity or 0,
        })
    
    return sorted(categories, key=lambda x: x["revenue"], reverse=True)

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
        margin = (profit / revenue * 100) if revenue > 0 else 0
        
        products.append({
            "name": r.name,
            "category": r.category or "—",
            "qty_sold": r.qty_sold or 0,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "margin": round(margin, 1),
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
    
    return [{"tier": r.tier, "revenue": r.revenue, "orders": r.orders, "customers": r.customers} for r in results]

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
    
    return [{"type": k, "total_amount": v, "pct_of_revenue": round(v / total_revenue * 100, 1) if total_revenue > 0 else 0}
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
        share = cum / total_rev * 100
        cls = "A" if share <= 80 else ("B" if share <= 95 else "C")
        items.append({
            "name": r.name,
            "category": r.category or "—",
            "qty": r.qty or 0,
            "revenue": rev,
            "profit": rev - cost,
            "margin": round((rev - cost) / rev * 100) if rev else 0,
            "share": round(share, 1),
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
        "hours": [{"hour": h % 12 or 12, "revenue": hours[h]} for h in range(8, 22)]
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

    pct = round(sold / (total_stock + sold) * 100) if (total_stock + sold) else 0
    return {
        "sold": sold,
        "stock": total_stock,
        "pct": pct,
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
        return {"repeat_rate": 0, "one_timer_pct": 0, "avg_orders": 0, "segments": []}
    total = len(customers_in)
    repeats = sum(1 for c in customers_in if c.orders > 1)
    avg_orders = sum(c.orders for c in customers_in) / total
    # Revenue segments
    segments = {"0-500k": 0, "500k-1m": 0, "1m-2m": 0, "2m+": 0}
    for c in customers_in:
        s = c.spent or 0
        if s < 500000: segments["0-500k"] += 1
        elif s < 1000000: segments["500k-1m"] += 1
        elif s < 2000000: segments["1m-2m"] += 1
        else: segments["2m+"] += 1
    return {
        "repeat_rate": round(repeats / total * 100),
        "one_timer_pct": round((total - repeats) / total * 100),
        "avg_orders": round(avg_orders, 1),
        "segments": [{"label": k, "count": v} for k, v in segments.items()],
    }

def get_margin_by_category(db, start, end):
    """Margin % per category — which category actually makes money."""
    rows = db.query(
        Product.category,
        func.sum(SaleItem.total_price).label("rev"),
        func.sum(SaleItem.unit_cost * SaleItem.quantity).label("cost"),
        func.sum(SaleItem.quantity).label("qty"),
    ).join(Sale, Sale.id == SaleItem.sale_id) \
     .join(Product, Product.id == SaleItem.product_id) \
     .filter(
        Sale.payment_confirmed == True, Sale.is_refunded == False,
        Sale.created_at.between(start, end),
    ).group_by(Product.category).all()
    result = []
    for r in rows:
        rev = r.rev or 0
        cost = r.cost or 0
        margin = round((rev - cost) / rev * 100) if rev else 0
        result.append({
            "category": r.category or "بدون دسته",
            "revenue": rev,
            "cost": cost,
            "profit": rev - cost,
            "margin": margin,
            "qty": r.qty or 0,
        })
    return sorted(result, key=lambda x: x["margin"], reverse=True)

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
