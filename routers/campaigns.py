from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Campaign, Settings, to_english_digits
from services._common import (
    fmt,
    check_admin,
    get_setting_int,
    is_archived_customer,
    jalali_str,
    marketing_opt_in,
    parse_form_date,
    parse_form_date_end,
)
from services.security import log_action, require_html_role
from services.sms import queue_sms
from services.templating import templates

router = APIRouter(prefix="/admin")


@router.get("/campaigns", response_class=HTMLResponse)
async def admin_campaigns(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/campaigns.html", {
        "campaigns": campaigns,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/campaigns/add", response_class=HTMLResponse)
async def admin_campaign_add_form(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    return templates.TemplateResponse(request, "admin/campaign_form.html", {
        "campaign": None,
        "edit_mode": False,
        "jalali_str": jalali_str,
    })


@router.post("/campaigns/add", response_class=HTMLResponse)
async def admin_campaign_add(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    discount_percent: str = Form(...),
    min_purchase: str = Form("0"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    try:
        discount_int = int(to_english_digits(discount_percent))
    except ValueError:
        discount_int = 0
    
    try:
        min_purchase_int = int(to_english_digits(min_purchase))
    except ValueError:
        min_purchase_int = 0
    
    # Parse Persian dates → Gregorian for storage.
    start_dt = parse_form_date(start_date)
    end_dt = parse_form_date_end(end_date)

    campaign = Campaign(
        name=name,
        code=code.upper(),
        discount_percent=discount_int,
        min_purchase=min_purchase_int,
        start_date=start_dt,
        end_date=end_dt,
    )
    db.add(campaign)
    db.commit()
    
    return RedirectResponse(url="/admin/campaigns", status_code=303)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_campaign_edit_form(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    
    return templates.TemplateResponse(request, "admin/campaign_form.html", {
        "campaign": campaign,
        "edit_mode": True,
        "jalali_str": jalali_str,
    })


@router.post("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_campaign_update(
    campaign_id: int,
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    discount_percent: str = Form(...),
    min_purchase: str = Form("0"),
    is_active: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    
    try:
        campaign.discount_percent = int(to_english_digits(discount_percent))
    except ValueError:
        pass
    
    try:
        campaign.min_purchase = int(to_english_digits(min_purchase))
    except ValueError:
        pass
    
    campaign.name = name
    campaign.code = code.upper()
    campaign.is_active = is_active == "on"
    
    if start_date:
        new_start = parse_form_date(start_date)
        if new_start:
            campaign.start_date = new_start
    if end_date:
        new_end = parse_form_date_end(end_date)
        if new_end:
            campaign.end_date = new_end

    db.commit()
    
    return RedirectResponse(url="/admin/campaigns", status_code=303)


@router.post("/campaigns/{campaign_id}/send", response_class=HTMLResponse)
async def admin_campaign_send(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    """Send campaign SMS to diamond customers who consented to marketing.

    Safety: each customer gets a per-campaign dedup marker (like birthday SMS),
    so double-clicks never re-send; a configurable cap stops runaway blasts;
    and anyone who opted out of marketing SMS — or was archived — is skipped and
    counted, rather than being messaged again."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    
    pattern = db.query(Settings).filter(Settings.key == "sms_pattern_campaign").first()
    if not pattern or not pattern.value:
        return templates.TemplateResponse(request, "admin/campaigns.html", {
            "campaigns": db.query(Campaign).order_by(Campaign.created_at.desc()).all(),
            "fmt": fmt,
            "jalali_str": jalali_str,
            "message": "⚠️ ابتدا متن پیامک کمپین را در صفحه تنظیمات بنویسید.",
        })

    limit = get_setting_int(db, "campaign_sms_limit", 100)
    diamond_customers = db.query(Customer).filter(Customer.tier == "diamond").all()
    
    queued_count = 0
    skipped = 0
    opted_out = 0
    for customer in diamond_customers:
        if is_archived_customer(customer) or not marketing_opt_in(customer):
            opted_out += 1
            continue
        if queued_count >= limit:
            skipped += 1
            continue
        marker_key = f"campaign_sms_{campaign.id}_{customer.id}"
        if db.query(Settings).filter(Settings.key == marker_key).first():
            skipped += 1
            continue
        await queue_sms(
            pattern.value,
            customer.phone,
            {"var1": customer.first_name or "مشتری", "var2": campaign.name, "var3": campaign.code, "var4": str(campaign.discount_percent)},
            db,
        )
        success = True
        if success:
            db.add(Settings(key=marker_key, value="sent"))
            queued_count += 1
    
    db.commit()
    log_action(
        db, "campaign_sms",
        f"کمپین «{campaign.name}»: {queued_count} در صف، {skipped} رد شد، {opted_out} انصراف",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"queued_count": queued_count, "skipped": skipped, "opted_out": opted_out},
    )

    message = f"پیامک کمپین برای {queued_count} مشتری الماس در صف قرار گرفت."
    if skipped:
        message += f" ({skipped} مشتری به دلیل ارسال قبلی یا سقف {limit} رد شدند.)"
    if opted_out:
        message += f" ({opted_out} مشتری انصراف از پیامک تبلیغاتی یا بایگانی داشتند.)"
    return templates.TemplateResponse(request, "admin/campaigns.html", {
        "campaigns": db.query(Campaign).order_by(Campaign.created_at.desc()).all(),
        "fmt": fmt,
        "jalali_str": jalali_str,
        "message": message,
    })


@router.post("/campaigns/{campaign_id}/delete", response_class=HTMLResponse)
async def admin_campaign_delete(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign:
        db.delete(campaign)
        db.commit()

    return RedirectResponse(url="/admin/campaigns", status_code=303)
