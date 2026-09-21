from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db
from services._common import fmt, check_admin, jalali_str, share
from services.security import require_html_role
from services.templating import templates
from services.reporting import canonical_report
from services.chart_notes import chart_notes
from services.analytics import (
    period_range, get_daily_revenue,
    get_revenue_by_category,
    get_revenue_by_tier, get_discount_impact,
    get_top_customers, get_categories, get_price_stats, get_variant_stats,
    get_color_size_matrix, get_inventory_value,
    get_abc_products, get_sales_pattern,
    get_sell_through, get_revenue_trend, get_basket_stats,
    get_customer_health, get_margin_by_category, get_dead_stock,
    get_price_distribution, get_top_selling_variants, get_new_customers,
)

router = APIRouter(prefix="/admin")

ANALYTICS_TABS = {
    "sales": "فروش",
    "product": "محصول",
    "customers": "مشتریان",
    "profit": "سود",
    "trends": "روند",
}


@router.get("/analytics", response_class=HTMLResponse)
async def admin_analytics(
    request: Request,
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    category: str = "",
    tab: str = "sales",
    db = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    # The range the page shows, and what to say when the range that was asked for
    # could not be read — a request reading «banana» is not the whole history.
    window = period_range(period, start_date or None, end_date or None)
    start, end = window.start, window.end
    cat = category or None
    active_tab = tab if tab in ANALYTICS_TABS else "sales"

    # Gather all analytics data
    report = canonical_report(db, start, end)
    summary = {
        "total_revenue": report["net_sales"],
        "gross_profit": report["gross_profit"],
        "margin": report["gross_margin"],
        "invoice_count": report["sale_count"],
        "aov": round(report["net_sales"] / report["sale_count"]) if report["sale_count"] else 0,
        "new_customers": get_new_customers(db, start, end),
    }
    daily = get_daily_revenue(db, start, end)
    categories = get_revenue_by_category(db, start, end)
    tier_revenue = get_revenue_by_tier(db, start, end)
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

    # One sentence per chart, built from these same figures: a chart that only
    # reads by colour reads as nothing on a printout or to an owner who cannot
    # separate the accents.
    notes = chart_notes(
        price_stats=price_stats, color_stats=color_stats, size_stats=size_stats,
        daily=daily, categories=categories, tier_revenue=tier_revenue,
        revenue_trend=revenue_trend, sales_pattern=sales_pattern,
        price_dist=price_dist, margin_by_cat=margin_by_cat,
        customer_health=customer_health,
    )

    return templates.TemplateResponse(request, "admin/analytics.html", {
        "show_charts": True,
        "tab": active_tab,
        "tabs": ANALYTICS_TABS,
        "period": window.period,
        "start_date": start_date,
        "end_date": end_date,
        "range_notice": window.notice,
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
        "notes": notes,
        "summary": summary,
        "daily": daily,
        "categories": categories,
        "tier_revenue": tier_revenue,
        "discounts": discounts,
        "top_customers": top_customers,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


def _analytics_csv(filename: str, rows: list[list]):
    import csv
    import io
    from fastapi.responses import Response
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics/export")
async def analytics_export(
    request: Request,
    kind: str = "daily",
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    category: str = "",
    db = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    window = period_range(period, start_date or None, end_date or None)
    start, end = window.start, window.end
    cat = category or None
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    if kind == "categories":
        rows = [["دسته", "تعداد", "فروش", "سود", "حاشیه"]]
        for c in get_revenue_by_category(db, start, end):
            margin = share(c["profit"], c["revenue"], 1)
            rows.append([c["category"], c["quantity"], c["revenue"], c["profit"],
                         "" if margin is None else margin])
        return _analytics_csv(f"analytics_categories_{stamp}.csv", rows)

    if kind == "customers":
        rows = [["نام", "تلفن", "سطح", "مجموع خرید", "سفارش"]]
        for c in get_top_customers(db, start, end, 100):
            rows.append([c["name"], c["phone"], c["tier"], c["total_spent"], c["orders"]])
        return _analytics_csv(f"analytics_customers_{stamp}.csv", rows)

    if kind == "variants":
        rows = [["نام", "سایز", "رنگ", "تعداد", "فروش"]]
        for v in get_top_selling_variants(db, start, end, limit=100):
            rows.append([v["name"], v["size"], v["color"], v["qty"], v["revenue"]])
        return _analytics_csv(f"analytics_variants_{stamp}.csv", rows)

    if kind == "discounts":
        rows = [["نوع", "مبلغ کل"]]
        for d in get_discount_impact(db, start, end):
            rows.append([d["type"], d["total_amount"]])
        return _analytics_csv(f"analytics_discounts_{stamp}.csv", rows)

    if kind == "matrix":
        matrix = get_color_size_matrix(db, start, end, cat)
        rows = [["سایز \\ رنگ"] + matrix["colors"]]
        for row in matrix["rows"]:
            rows.append([row["size"]] + [cell["qty"] for cell in row["cells"]])
        return _analytics_csv(f"analytics_matrix_{stamp}.csv", rows)

    # default: daily
    rows = [["تاریخ", "فروش", "سود", "تعداد فاکتور"]]
    for d in get_daily_revenue(db, start, end):
        rows.append([d["date"], d["revenue"], d["profit"], d["count"]])
    return _analytics_csv(f"analytics_daily_{stamp}.csv", rows)
