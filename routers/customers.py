from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Referral, generate_referral_code, to_english_digits
from services._common import fmt, get_setting_int as get_discount_setting, current_year_month, parse_persian_birthday, jalali_str
from services.sms import queue_welcome_sms
from services.templating import templates
from services.tier import get_tier_config
from services.security import require_html_role, log_action
from services.security import require_html_role, log_action

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return RedirectResponse(url="/sales/new", status_code=303)


@router.post("/customers", response_class=HTMLResponse)
async def create_customer(
    request: Request,
    phone: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    child_name: str = Form(""),
    child_birthday: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    phone = to_english_digits(phone.strip())
    if not phone.startswith("09") or len(phone) != 11:
        return templates.TemplateResponse(request, "index.html", {
            "error": "شماره موبایل نامعتبر است. فرمت صحیح: 09xxxxxxxxx",
            "fmt": fmt,
            "jalali_str": jalali_str,
        })

    existing = db.query(Customer).filter(Customer.phone == phone).first()
    if existing:
        tier_config = get_tier_config(db)
        return templates.TemplateResponse(request, "customer.html", {
            "customer": existing,
            "message": "این شماره قبلاً ثبت شده است.",
            "fmt": fmt,
            "jalali_str": jalali_str,
            "tier_config": tier_config,
        })

    code = generate_referral_code()
    while db.query(Customer).filter(Customer.referral_code == code).first():
        code = generate_referral_code()

    customer = Customer(
        phone=phone,
        first_name=first_name if first_name else None,
        last_name=last_name if last_name else None,
        referral_code=code,
        child_name=child_name if child_name else None,
        child_birthday=parse_persian_birthday(child_birthday),
    )
    db.add(customer)
    db.commit()
    await queue_welcome_sms(phone, first_name, code, db)

    tier_config = get_tier_config(db)
    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "message": "ثبت‌نام با موفقیت انجام شد!",
        "fmt": fmt,
        "jalali_str": jalali_str,
        "tier_config": tier_config,
    })


@router.get("/customers/lookup", response_class=HTMLResponse)
async def lookup_customer(request: Request, phone: str = "", db: Session = Depends(get_db)):
    if not phone:
        referrer_discount = get_discount_setting(db, "default_referrer_discount", 50000)
        referred_discount = get_discount_setting(db, "default_referred_discount", 30000)
        min_purchase = get_discount_setting(db, "min_purchase_for_discount", 500000)
        monthly_limit = get_discount_setting(db, "monthly_referral_limit", 10)
        tier_config = get_tier_config(db)
        return templates.TemplateResponse(request, "index.html", {
            "referrer_discount": referrer_discount,
            "referred_discount": referred_discount,
            "min_purchase": min_purchase,
            "monthly_limit": monthly_limit,
            "tier_config": tier_config,
            "fmt": fmt,
            "jalali_str": jalali_str,
        })

    phone = to_english_digits(phone.strip())
    customer = db.query(Customer).filter(Customer.phone == phone).first()
    if not customer:
        return templates.TemplateResponse(request, "index.html", {
            "error": "مشتری با این شماره یافت نشد.",
            "referrer_discount": get_discount_setting(db, "default_referrer_discount", 50000),
            "referred_discount": get_discount_setting(db, "default_referred_discount", 30000),
            "min_purchase": get_discount_setting(db, "min_purchase_for_discount", 500000),
            "monthly_limit": get_discount_setting(db, "monthly_referral_limit", 10),
            "tier_config": get_tier_config(db),
            "fmt": fmt,
            "jalali_str": jalali_str,
        })

    referrals = db.query(Referral).filter(Referral.referrer_id == customer.id).all()
    referred_customers = []
    for r in referrals:
        referred = db.query(Customer).filter(Customer.id == r.referred_id).first()
        if referred:
            referred_customers.append({"customer": referred, "referral": r})

    min_purchase = get_discount_setting(db, "min_purchase_for_discount", 500000)
    monthly_limit = get_discount_setting(db, "monthly_referral_limit", 10)
    tier_config = get_tier_config(db)

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "referrals": referred_customers,
        "fmt": fmt,
        "jalali_str": jalali_str,
        "min_purchase": min_purchase,
        "monthly_limit": monthly_limit,
        "tier_config": tier_config,
    })


@router.post("/customers/{customer_id}/update-child", response_class=HTMLResponse)
async def update_child_info(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Update child information for a customer."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    form = await request.form()
    child_name = form.get("child_name", "")
    child_birthday = form.get("child_birthday", "")

    customer.child_birthday = parse_persian_birthday(child_birthday)
    customer.child_name = child_name if child_name else None
    db.commit()

    tier_config = get_tier_config(db)
    min_purchase = get_discount_setting(db, "min_purchase_for_discount", 500000)
    monthly_limit = get_discount_setting(db, "monthly_referral_limit", 10)

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "message": "اطلاعات فرزند با موفقیت به‌روزرسانی شد.",
        "fmt": fmt,
        "jalali_str": jalali_str,
        "min_purchase": min_purchase,
        "monthly_limit": monthly_limit,
        "tier_config": tier_config,
    })


@router.post("/customers/{customer_id}/use-referred-discount", response_class=HTMLResponse)
async def use_referred_discount(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    min_purchase = get_discount_setting(db, "min_purchase_for_discount", 500000)
    monthly_limit = get_discount_setting(db, "monthly_referral_limit", 10)
    tier_config = get_tier_config(db)

    if customer.has_used_referred_discount:
        return templates.TemplateResponse(request, "customer.html", {
            "customer": customer,
            "error": "تخفیف معرفی قبلاً استفاده شده است.",
            "fmt": fmt,
            "jalali_str": jalali_str,
            "min_purchase": min_purchase,
            "monthly_limit": monthly_limit,
            "tier_config": tier_config,
        })
    if customer.referred_discount <= 0:
        return templates.TemplateResponse(request, "customer.html", {
            "customer": customer,
            "error": "تخفیفی موجود نیست.",
            "fmt": fmt,
            "jalali_str": jalali_str,
            "min_purchase": min_purchase,
            "monthly_limit": monthly_limit,
            "tier_config": tier_config,
        })

    customer.has_used_referred_discount = True
    db.commit()

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "message": f"تخفیف {fmt(customer.referred_discount)} تومان با موفقیت اعمال شد!",
        "fmt": fmt,
        "jalali_str": jalali_str,
        "min_purchase": min_purchase,
        "monthly_limit": monthly_limit,
        "tier_config": tier_config,
    })


@router.post("/customers/{customer_id}/use-referrer-discount", response_class=HTMLResponse)
async def use_referrer_discount(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    min_purchase = get_discount_setting(db, "min_purchase_for_discount", 500000)
    monthly_limit = get_discount_setting(db, "monthly_referral_limit", 10)
    tier_config = get_tier_config(db)

    if customer.referrer_discount <= 0:
        return templates.TemplateResponse(request, "customer.html", {
            "customer": customer,
            "error": "تخفیف معرفی موجود نیست.",
            "fmt": fmt,
            "jalali_str": jalali_str,
            "min_purchase": min_purchase,
            "monthly_limit": monthly_limit,
            "tier_config": tier_config,
        })

    customer.referrer_discount = 0
    db.commit()

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "message": "تخفیف معرفی با موفقیت اعمال شد و به صفر بازگشت. اکنون می‌توانید دوباره معرفی کنید!",
        "fmt": fmt,
        "jalali_str": jalali_str,
        "min_purchase": min_purchase,
        "monthly_limit": monthly_limit,
        "tier_config": tier_config,
    })
