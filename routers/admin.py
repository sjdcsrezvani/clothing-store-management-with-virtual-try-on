from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models import Customer, Referral
from config import ADMIN_PASSWORD
from services.sms import get_balance
from services._common import fmt, check_admin, get_setting_int as get_discount_setting
from services.tier import get_tier_config, get_customers_for_birthday_check

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin/login.html")


@router.post("/login", response_class=HTMLResponse)
async def admin_login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(key="is_admin", value="true", httponly=True)
        return response
    return templates.TemplateResponse(request, "admin/login.html", {"error": "رمز عبور اشتباه است"})


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    total_customers = db.query(Customer).count()
    total_referrals = db.query(Referral).count()
    customers_with_discount = db.query(Customer).filter(Customer.referrer_discount > 0).count()
    sms_balance = await get_balance(db)
    
    # Count by tier
    silver_count = db.query(Customer).filter(Customer.tier == "silver").count()
    gold_count = db.query(Customer).filter(Customer.tier == "gold").count()
    diamond_count = db.query(Customer).filter(Customer.tier == "diamond").count()

    top_referrers = (
        db.query(Customer, func.count(Referral.id).label("ref_count"))
        .join(Referral, Referral.referrer_id == Customer.id)
        .group_by(Customer.id)
        .order_by(func.count(Referral.id).desc())
        .limit(10)
        .all()
    )
    
    # Top customers by spending
    top_spenders = (
        db.query(Customer)
        .filter(Customer.total_spent > 0)
        .order_by(Customer.total_spent.desc())
        .limit(10)
        .all()
    )
    
    # Check for action messages
    birthday_msg = request.query_params.get("birthday_msg")
    downgrade_msg = request.query_params.get("downgrade_msg")
    action_message = None
    if birthday_msg is not None:
        eligible = get_customers_for_birthday_check(db, get_tier_config(db)["birthday_sms_days_before"])
        action_message = f"بررسی تولد انجام شد. {len(eligible)} مشتری تولد نزدیک دارند."
        if birthday_msg != "0":
            action_message = f"{birthday_msg} پیامک تولد ارسال شد."
    if downgrade_msg is not None:
        action_message = f"بررسی کاهش سطح انجام شد. {downgrade_msg} مشتری کاهش سطح یافتند."

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "total_customers": total_customers,
        "total_referrals": total_referrals,
        "customers_with_discount": customers_with_discount,
        "sms_balance": sms_balance,
        "top_referrers": top_referrers,
        "top_spenders": top_spenders,
        "silver_count": silver_count,
        "gold_count": gold_count,
        "diamond_count": diamond_count,
        "referrer_discount": get_discount_setting(db, "default_referrer_discount", 50000),
        "referred_discount": get_discount_setting(db, "default_referred_discount", 30000),
        "min_purchase": get_discount_setting(db, "min_purchase_for_discount", 500000),
        "monthly_limit": get_discount_setting(db, "monthly_referral_limit", 10),
        "action_message": action_message,
        "fmt": fmt,
    })


@router.get("/customers", response_class=HTMLResponse)
async def admin_customers(
    request: Request,
    search: str = "",
    sort: str = "date",
    tier: str = "",
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    query = db.query(Customer)
    
    # Apply search filter
    if search:
        query = query.filter(
            Customer.phone.contains(search) | 
            Customer.first_name.contains(search) | 
            Customer.last_name.contains(search) | 
            Customer.referral_code.contains(search)
        )
    
    # Apply tier filter
    if tier and tier in ("silver", "gold", "diamond"):
        query = query.filter(Customer.tier == tier)
    
    # Apply sorting
    if sort == "tier":
        # Custom tier order: diamond > gold > silver
        from sqlalchemy import case
        tier_order = case(
            (Customer.tier == "diamond", 1),
            (Customer.tier == "gold", 2),
            (Customer.tier == "silver", 3),
            else_=4
        )
        query = query.order_by(tier_order, Customer.total_points.desc())
    elif sort == "purchase_desc":
        query = query.order_by(Customer.total_spent.desc())
    elif sort == "purchase_asc":
        query = query.order_by(Customer.total_spent.asc())
    elif sort == "points":
        query = query.order_by(Customer.total_points.desc())
    else:  # date (default)
        query = query.order_by(Customer.created_at.desc())
    
    customers = query.all()
    tier_config = get_tier_config(db)

    return templates.TemplateResponse(request, "admin/customers.html", {
        "customers": customers,
        "search": search,
        "sort": sort,
        "tier_filter": tier,
        "tier_config": tier_config,
        "fmt": fmt,
    })


@router.post("/customers/{customer_id}/delete", response_class=HTMLResponse)
async def admin_delete_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        db.query(Referral).filter(
            (Referral.referrer_id == customer_id) | (Referral.referred_id == customer_id)
        ).delete()
        db.delete(customer)
        db.commit()

    return RedirectResponse(url="/admin/customers", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    settings = {s.key: s.value for s in db.query(Settings).all()}
    tier_config = get_tier_config(db)
    return templates.TemplateResponse(request, "admin/settings.html", {
        "settings": settings,
        "tier_config": tier_config,
    })


@router.post("/settings", response_class=HTMLResponse)
async def admin_update_settings(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    for key, value in form.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = str(value)
        else:
            db.add(Settings(key=key, value=str(value)))
    db.commit()

    return RedirectResponse(url="/admin/settings", status_code=303)


@router.post("/reset-database", response_class=HTMLResponse)
async def admin_reset_database(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    db.query(Referral).delete()
    db.query(Customer).delete()
    db.query(Settings).delete()
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/check-birthdays", response_class=HTMLResponse)
async def admin_check_birthdays(request: Request, db: Session = Depends(get_db)):
    """Manually trigger birthday check."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    from services.sms import send_pattern_sms
    
    config = get_tier_config(db)
    days_before = config["birthday_sms_days_before"]
    eligible = get_customers_for_birthday_check(db, days_before)
    
    birthday_pattern = db.query(Settings).filter(Settings.key == "birthday_sms_pattern_code").first()
    pattern_code = birthday_pattern.value if birthday_pattern else ""
    
    sent_count = 0
    if pattern_code:
        for customer, days_until in eligible:
            log_key = f"birthday_sms_{customer.id}_{datetime.now(timezone.utc).year}_{customer.child_birthday}"
            already_sent = db.query(Settings).filter(Settings.key == log_key).first()
            if already_sent:
                continue
            attributes = {
                "var1": customer.child_name or "فرزند شما",
                "var2": str(days_until),
                "var3": customer.first_name or "مشتری گرامی",
            }
            success = await send_pattern_sms(pattern_code, customer.phone, attributes)
            if success:
                db.add(Settings(key=log_key, value="sent"))
                sent_count += 1
        db.commit()
    
    return RedirectResponse(url="/admin?birthday_msg=" + str(sent_count), status_code=303)


@router.post("/check-downgrades", response_class=HTMLResponse)
async def admin_check_downgrades(request: Request, db: Session = Depends(get_db)):
    """Manually trigger tier downgrade check."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    from services.tier import get_tier_config, check_tier_downgrade, get_customers_for_downgrade_check
    
    config = get_tier_config(db)
    customers = get_customers_for_downgrade_check(db)
    downgraded = 0
    
    for customer in customers:
        was_downgraded = check_tier_downgrade(customer, config, db)
        if was_downgraded:
            downgraded += 1
    
    db.commit()
    
    return RedirectResponse(url="/admin?downgrade_msg=" + str(downgraded), status_code=303)


@router.get("/sales", response_class=HTMLResponse)
async def admin_sales_redirect(request: Request):
    """Redirect admin sales to sales list."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return RedirectResponse(url="/sales/", status_code=303)
