from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
from models import Customer, Sale, SaleItem, Product, Referral


def get_date_range(period: str, start_date: str = None, end_date: str = None):
    """Get start and end dates based on period."""
    now = datetime.now(timezone.utc)
    today = now.date()
    
    if period == "today":
        start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "week":
        start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "month":
        start = datetime.combine(today.replace(day=1), datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "year":
        start = datetime.combine(today.replace(month=1, day=1), datetime.min.time()).replace(tzinfo=timezone.utc)
        end = now
    elif period == "custom" and start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
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
    
    invoice_count = len(sales) - len(refunded)
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
        
        results.append({
            "date": current.strftime("%m/%d"),
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
