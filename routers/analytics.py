from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db
from models import Settings, to_english_digits
from services._common import fmt, check_admin, get_setting_int, jalali_str, share
from services.security import log_action, require_html_role
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
    get_revenue_by_payment, get_return_rate, get_turnover, get_year_heatmap,
)

router = APIRouter(prefix="/admin")

ANALYTICS_TABS = {
    "sales": "فروش",
    "product": "محصول",
    "inventory": "انبار",
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

    # One load, one tab: every tab below reads only the queries its own
    # charts need, plus the headline summary every tab shares. A tab switch
    # is a real link and a full reload, so nothing inactive is ever missed —
    # it is simply read on its own visit instead of on everyone else's.
    # Anything unfilled stays an empty-but-fully-shaped default: hidden
    # sections still render (a missing attribute prints a name nothing
    # supplied, which the sweep fails; a missing number crashes fmt), and a
    # tab switch is a full reload that fills its own visit.
    data = {
        "price_stats": {"avg_price": 0, "avg_cost": 0, "avg_profit": 0},
        "color_stats": [], "size_stats": [],
        "daily": [], "prev_revenues": [], "categories": [],
        "tier_revenue": [], "revenue_trend": [], "trend_ma": [],
        "sales_pattern": {"weekdays": [], "hours": []},
        "price_dist": [], "margin_by_cat": [],
        "customer_health": {"repeat_rate": None, "one_timer_pct": None,
                            "avg_orders": None, "segments": []},
        "payment_mix": [], "discounts": [], "top_customers": [],
        "top_variants": [], "return_rate": {"count": 0, "amount": 0, "rate": None},
        "heatmap": {"cells": [], "weeks": 0, "max": 0},
        "goal": None, "all_categories": [],
        "inventory": {"total_cost": 0, "total_retail": 0, "units": 0,
                      "categories": [], "low_stock": []},
        "abc_products": {"a_count": 0, "a_pct": 0, "products": [], "total_rev": 0, "a_rev": 0},
        "sell_through": {"sold": 0, "stock": 0, "pct": None,
                         "stock_cost": 0, "stock_retail": 0},
        "dead_stock": {"count": 0, "total_value": 0, "variants": []},
        "turnover": {"rows": [], "total": 0, "omitted": 0, "low_count": 0, "threshold": 14},
        "basket": {"items_per_txn": 0},
        "color_size_matrix": {"colors": [], "rows": [], "max": 0, "max_revenue": 0},
    }

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

    if active_tab == "sales":
        data["daily"] = daily = get_daily_revenue(db, start, end)
        # The window before this one, equally long: the overlay aligns by
        # day index, not by date, so «then» reads against «now» point by point.
        span = end - start
        prev_daily = get_daily_revenue(db, start - span, start)
        data["prev_revenues"] = [d["revenue"] for d in prev_daily[:len(daily)]]
        data["sales_pattern"] = get_sales_pattern(db, start, end)
        data["price_dist"] = get_price_distribution(db, start, end)
        data["payment_mix"] = [
            {"label": {"cash": "نقدی", "card": "کارتی", "credit": "نسیه"}.get(row["method"], row["method"] or "—"),
             "revenue": row["revenue"], "count": row["count"]}
            for row in get_revenue_by_payment(db, start, end)
        ]
        data["basket"] = get_basket_stats(db, start, end)
        data["return_rate"] = get_return_rate(db, start, end)
        # The monthly goal, if the owner set one: month-to-date revenue
        # against it, so the sales tab opens with «where the month stands».
        goal_amount = get_setting_int(db, "sales_goal_amount", 0)
        if goal_amount > 0:
            month_window = period_range("month")
            month_revenue = canonical_report(db, month_window.start, month_window.end)["net_sales"]
            data["goal"] = {"amount": goal_amount, "revenue": month_revenue,
                            "pct": share(month_revenue, goal_amount)}

    if active_tab == "product":
        data["all_categories"] = get_categories(db)
        data["price_stats"] = get_price_stats(db, start, end, cat)
        data["color_stats"] = get_variant_stats(db, start, end, "color", cat)
        data["size_stats"] = get_variant_stats(db, start, end, "size", cat)
        data["color_size_matrix"] = get_color_size_matrix(db, start, end, cat)
        data["inventory"] = get_inventory_value(db)
        data["sell_through"] = get_sell_through(db, start, end)
        data["dead_stock"] = get_dead_stock(db, start, end)
        data["top_variants"] = get_top_selling_variants(db, start, end)

    if active_tab == "inventory":
        data["turnover"] = get_turnover(db, start, end)
        data["dead_stock"] = get_dead_stock(db, start, end)
        data["abc_products"] = get_abc_products(db, start, end)
        data["sell_through"] = get_sell_through(db, start, end)
        data["inventory"] = get_inventory_value(db)

    if active_tab == "customers":
        data["tier_revenue"] = get_revenue_by_tier(db, start, end)
        data["customer_health"] = get_customer_health(db, start, end)
        data["top_customers"] = get_top_customers(db, start, end, 10)

    if active_tab == "profit":
        data["categories"] = get_revenue_by_category(db, start, end)
        data["margin_by_cat"] = get_margin_by_category(db, start, end)
        data["discounts"] = get_discount_impact(db, start, end)

    if active_tab == "trends":
        data["revenue_trend"] = revenue_trend = get_revenue_trend(db)
        # Three-month moving average on the year line: the season still
        # shows, the noise does not.
        trend_revenues = [m["revenue"] for m in revenue_trend]
        data["trend_ma"] = [
            round(sum(trend_revenues[max(0, i - 2):i + 1]) / len(trend_revenues[max(0, i - 2):i + 1]))
            for i in range(len(revenue_trend))
        ]
        data["categories"] = get_revenue_by_category(db, start, end)
        data["heatmap"] = get_year_heatmap(db)

    # One sentence per chart, built from these same figures: a chart that only
    # reads by colour reads as nothing on a printout or to an owner who cannot
    # separate the accents.
    notes = chart_notes(
        price_stats=data["price_stats"], color_stats=data["color_stats"],
        size_stats=data["size_stats"],
        daily=data["daily"], categories=data["categories"],
        tier_revenue=data["tier_revenue"],
        revenue_trend=data["revenue_trend"],
        sales_pattern=data["sales_pattern"],
        price_dist=data["price_dist"], margin_by_cat=data["margin_by_cat"],
        customer_health=data["customer_health"], payment_mix=data["payment_mix"],
        discounts=data["discounts"], heatmap=data["heatmap"],
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
        "all_categories": data["all_categories"],
        "price_stats": data["price_stats"],
        "color_stats": data["color_stats"],
        "size_stats": data["size_stats"],
        "color_size_matrix": data["color_size_matrix"],
        "inventory": data["inventory"],
        "abc_products": data["abc_products"],
        "sales_pattern": data["sales_pattern"],
        "sell_through": data["sell_through"],
        "revenue_trend": data["revenue_trend"],
        "basket": data["basket"],
        "customer_health": data["customer_health"],
        "margin_by_cat": data["margin_by_cat"],
        "dead_stock": data["dead_stock"],
        "price_dist": data["price_dist"],
        "top_variants": data["top_variants"],
        "prev_revenues": data["prev_revenues"],
        "trend_ma": data["trend_ma"],
        "payment_mix": data["payment_mix"],
        "heatmap": data["heatmap"],
        "return_rate": data["return_rate"],
        "goal": data["goal"],
        "turnover": data["turnover"],
        "notes": notes,
        "summary": summary,
        "daily": data["daily"],
        "categories": data["categories"],
        "tier_revenue": data["tier_revenue"],
        "discounts": data["discounts"],
        "top_customers": data["top_customers"],
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


@router.post("/analytics/goal", response_class=HTMLResponse)
async def analytics_goal(request: Request, amount: str = Form(""), db=Depends(get_db)):
    """Set or clear the monthly revenue goal. Empty clears it; the sales tab
    goes back to no target rather than holding a stale one."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    try:
        cleaned = to_english_digits(str(amount or "")).replace(",", "").replace("٬", "").replace(" ", "").strip()
        amount_int = int(cleaned) if cleaned else 0
    except (TypeError, ValueError):
        amount_int = -1
    if amount_int < 0:
        return RedirectResponse(url="/admin/analytics?err=مبلغ هدف باید صفر یا بیشتر باشد.", status_code=303)
    row = db.query(Settings).filter(Settings.key == "sales_goal_amount").first()
    if row:
        row.value = str(amount_int)
    else:
        db.add(Settings(key="sales_goal_amount", value=str(amount_int)))
    db.commit()
    log_action(db, "sales_goal", f"هدف فروش ماهانه {amount_int:,}", request=request, target_type="settings", after={"amount": amount_int})
    if amount_int:
        return RedirectResponse(url="/admin/analytics?msg=هدف ماه ثبت شد.", status_code=303)
    return RedirectResponse(url="/admin/analytics?msg=هدف ماه برداشته شد.", status_code=303)
