from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db
from services._common import fmt, check_admin, jalali_str
from services.security import require_html_role
from services.templating import templates
from services.analytics import (
    get_date_range, get_revenue_summary, get_daily_revenue,
    get_revenue_by_category, get_top_products,
    get_revenue_by_tier, get_monthly_comparison, get_discount_impact,
    get_top_customers, get_categories, get_price_stats, get_variant_stats,
    get_color_size_matrix, get_inventory_value,
    get_abc_products, get_sales_pattern,
    get_sell_through, get_revenue_trend, get_basket_stats,
    get_customer_health, get_margin_by_category, get_dead_stock,
    get_price_distribution, get_top_selling_variants,
)

router = APIRouter(prefix="/admin")


@router.get("/analytics", response_class=HTMLResponse)
async def admin_analytics(
    request: Request,
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    category: str = "",
    db = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    start, end = get_date_range(period, start_date or None, end_date or None)
    cat = category or None

    # Gather all analytics data
    summary = get_revenue_summary(db, start, end)
    daily = get_daily_revenue(db, start, end)
    categories = get_revenue_by_category(db, start, end)
    top_products_rev = get_top_products(db, start, end, 10, "revenue")
    top_products_profit = get_top_products(db, start, end, 10, "profit")
    tier_revenue = get_revenue_by_tier(db, start, end)
    now = datetime.now(timezone.utc)
    monthly = get_monthly_comparison(db, now.year, now.month)
    discounts = get_discount_impact(db, start, end)
    top_customers = get_top_customers(db, start, end, 10)

    # Garment floor views (pricing / colour / size / matrix / stock)
    all_categories = get_categories(db)
    price_stats = get_price_stats(db, start, end, cat)
    color_stats = get_variant_stats(db, start, end, "color", cat)
    size_stats = get_variant_stats(db, start, end, "size", cat)
    color_size_matrix = get_color_size_matrix(db, start, end, cat)
    inventory = get_inventory_value(db)

    # Expert tier
    abc_products = get_abc_products(db, start, end)
    sales_pattern = get_sales_pattern(db, start, end)
    sell_through = get_sell_through(db, start, end)
    revenue_trend = get_revenue_trend(db)
    basket = get_basket_stats(db, start, end)
    customer_health = get_customer_health(db, start, end)
    margin_by_cat = get_margin_by_category(db, start, end)
    dead_stock = get_dead_stock(db, start, end)
    price_dist = get_price_distribution(db, start, end)
    top_variants = get_top_selling_variants(db, start, end)

    return templates.TemplateResponse(request, "admin/analytics.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "category": category,
        "all_categories": all_categories,
        "price_stats": price_stats,
        "color_stats": color_stats,
        "size_stats": size_stats,
        "color_size_matrix": color_size_matrix,
        "inventory": inventory,
        "abc_products": abc_products,
        "sales_pattern": sales_pattern,
        "sell_through": sell_through,
        "revenue_trend": revenue_trend,
        "basket": basket,
        "customer_health": customer_health,
        "margin_by_cat": margin_by_cat,
        "dead_stock": dead_stock,
        "price_dist": price_dist,
        "top_variants": top_variants,
        "summary": summary,
        "daily": daily,
        "categories": categories,
        "top_products_rev": top_products_rev,
        "top_products_profit": top_products_profit,
        "tier_revenue": tier_revenue,
        "monthly": monthly,
        "discounts": discounts,
        "top_customers": top_customers,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })
