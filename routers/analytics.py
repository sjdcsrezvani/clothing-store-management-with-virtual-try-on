from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from database import get_db
from services._common import fmt, check_admin
from services.analytics import (
    get_date_range, get_revenue_summary, get_daily_revenue,
    get_revenue_by_category, get_top_products, get_revenue_by_payment,
    get_revenue_by_tier, get_monthly_comparison, get_discount_impact,
    get_top_customers,
)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/analytics", response_class=HTMLResponse)
async def admin_analytics(
    request: Request,
    period: str = "month",
    start_date: str = "",
    end_date: str = "",
    db = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    start, end = get_date_range(period, start_date or None, end_date or None)
    
    # Gather all analytics data
    summary = get_revenue_summary(db, start, end)
    daily = get_daily_revenue(db, start, end)
    categories = get_revenue_by_category(db, start, end)
    top_products_rev = get_top_products(db, start, end, 10, "revenue")
    top_products_profit = get_top_products(db, start, end, 10, "profit")
    payment_methods = get_revenue_by_payment(db, start, end)
    tier_revenue = get_revenue_by_tier(db, start, end)
    now = datetime.now(timezone.utc)
    monthly = get_monthly_comparison(db, now.year, now.month)
    discounts = get_discount_impact(db, start, end)
    top_customers = get_top_customers(db, start, end, 10)
    
    return templates.TemplateResponse(request, "admin/analytics.html", {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "summary": summary,
        "daily": daily,
        "categories": categories,
        "top_products_rev": top_products_rev,
        "top_products_profit": top_products_profit,
        "payment_methods": payment_methods,
        "tier_revenue": tier_revenue,
        "monthly": monthly,
        "discounts": discounts,
        "top_customers": top_customers,
        "fmt": fmt,
    })
