from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Referral, generate_referral_code, to_english_digits
from services._common import (
    current_year_month,
    fmt,
    get_setting_int as get_discount_setting,
    jalali_str,
)
from services.customers import (
    birthday_fields,
    customer_birthdays,
    signup_birthday_fields,
    update_customer_meta,
)
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
    birth_date: str = Form(""),
    buys_for: str = Form(""),
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
        return templates.TemplateResponse(request, "customer.html", {
            "customer": existing,
            "message": "این شماره قبلاً ثبت شده است.",
            "fmt": fmt,
            "jalali_str": jalali_str,
            **_panel_context(db, existing),
        })

    code = generate_referral_code()
    while db.query(Customer).filter(Customer.referral_code == code).first():
        code = generate_referral_code()

    # Who this customer buys for is their own choice, asked here; the fields
    # that get stored follow from it, and the server is what enforces that.
    customer = Customer(
        phone=phone,
        first_name=first_name if first_name else None,
        last_name=last_name if last_name else None,
        referral_code=code,
        **signup_birthday_fields(
            db, buys_for=buys_for, birth_value=birth_date,
            child_name=child_name, child_birth_value=child_birthday,
        ),
    )
    db.add(customer)
    db.commit()
    await queue_welcome_sms(phone, first_name, code, db)

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "message": "ثبت‌نام با موفقیت انجام شد!",
        "fmt": fmt,
        "jalali_str": jalali_str,
        **_panel_context(db, customer),
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

    return templates.TemplateResponse(request, "customer.html", {
        "customer": customer,
        "referrals": referred_customers,
        "fmt": fmt,
        "jalali_str": jalali_str,
        **_panel_context(db, customer),
    })


@router.post("/customers/{customer_id}/update-details", response_class=HTMLResponse)
async def update_customer_details(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Save the counter card: who this customer buys for, and that side's details.

    One route because it is one decision — the choice decides which fields the
    form asked for, and `update_customer_meta` writes only that side's fields, so
    a hand-posted child birthday in «برای خودم» mode changes nothing. The other
    side's stored data is kept rather than cleared, so switching back is safe.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    form = await request.form()
    update_customer_meta(
        db,
        customer,
        buys_for=form.get("buys_for"),
        birth_value=form.get("birth_date"),
        child_name=form.get("child_name"),
        child_birth_value=form.get("child_birthday"),
    )
    db.commit()
    return _customer_panel(request, customer, db, message="پرونده مشتری به‌روزرسانی شد.")


def _admin_next(value) -> str | None:
    """A posted `next` path is honoured only when it stays inside the admin panel.

    The discount buttons live on both the cashier-facing customer page (which
    answers with a render) and the admin profile (which wants a redirect back),
    so the caller chooses by posting `next` — and anything that isn't a plain
    admin path is ignored rather than trusted.
    """
    if not value:
        return None
    target = str(value).strip()
    if not target.startswith("/admin/") or "//" in target or "\\" in target:
        return None
    return target


def _back_or_panel(request, customer, db, next_url, message: str = "", error: str = ""):
    """Answer the caller: the admin page that sent us, or the customer panel."""
    if next_url:
        param = "msg" if message else "err"
        value = message or error
        separator = "&" if "?" in next_url else "?"
        return RedirectResponse(
            url=f"{next_url}{separator}{param}={quote_plus(value)}", status_code=303,
        )
    return _customer_panel(request, customer, db, message=message, error=error)


def _panel_context(db, customer) -> dict:
    """Everything `customer.html` needs, whatever route rendered it.

    The panel edits the same «for whom» choice the signup form asked, so every
    render has to prefill those fields and show the birthdays this customer is
    actually wished on — resolved per customer, not per store.
    """
    return {
        "birthday_fields": birthday_fields(customer, db),
        "birthdays": customer_birthdays(db, customer),
        "min_purchase": get_discount_setting(db, "min_purchase_for_discount", 500000),
        "monthly_limit": get_discount_setting(db, "monthly_referral_limit", 10),
        "tier_config": get_tier_config(db),
    }


def _customer_panel(request, customer, db, message: str = "", error: str = ""):
    """The cashier-facing customer panel, with the shared context it needs."""
    context = {
        "customer": customer,
        "fmt": fmt,
        "jalali_str": jalali_str,
        **_panel_context(db, customer),
    }
    if message:
        context["message"] = message
    if error:
        context["error"] = error
    return templates.TemplateResponse(request, "customer.html", context)


@router.post("/customers/{customer_id}/use-referred-discount", response_class=HTMLResponse)
async def use_referred_discount(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    form = await request.form()
    next_url = _admin_next(form.get("next"))

    if customer.has_used_referred_discount:
        return _back_or_panel(request, customer, db, next_url, error="تخفیف معرفی قبلاً استفاده شده است.")
    if customer.referred_discount <= 0:
        return _back_or_panel(request, customer, db, next_url, error="تخفیفی موجود نیست.")

    customer.has_used_referred_discount = True
    db.commit()
    log_action(db, "customer_discount", f"اعمال تخفیف معرفی‌شده برای {customer.phone}",
               request=request, target_type="customer", target_id=customer.id,
               after={"amount": customer.referred_discount})
    return _back_or_panel(
        request, customer, db, next_url,
        message=f"تخفیف {fmt(customer.referred_discount)} تومان با موفقیت اعمال شد!",
    )


@router.post("/customers/{customer_id}/use-referrer-discount", response_class=HTMLResponse)
async def use_referrer_discount(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    form = await request.form()
    next_url = _admin_next(form.get("next"))

    if customer.referrer_discount <= 0:
        return _back_or_panel(request, customer, db, next_url, error="تخفیف معرفی موجود نیست.")

    customer.referrer_discount = 0
    db.commit()
    log_action(db, "customer_discount", f"اعمال تخفیف معرف برای {customer.phone}",
               request=request, target_type="customer", target_id=customer.id)
    return _back_or_panel(
        request, customer, db, next_url,
        message="تخفیف معرفی با موفقیت اعمال شد و به صفر بازگشت. اکنون می‌توانید دوباره معرفی کنید!",
    )
