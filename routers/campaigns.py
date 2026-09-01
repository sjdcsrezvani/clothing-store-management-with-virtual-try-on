from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Campaign, Settings, to_english_digits
from services._common import fmt, check_admin, get_setting_int, parse_jalali_input, parse_jalali_input_end, jalali_str
from services.security import log_action, require_html_role
from services.sms import send_campaign_sms
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
async def admin_campaign_add_form(request: Request):
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
    start_dt = parse_jalali_input(start_date)
    end_dt = parse_jalali_input_end(end_date)

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
        new_start = parse_jalali_input(start_date)
        if new_start:
            campaign.start_date = new_start
    if end_date:
        new_end = parse_jalali_input_end(end_date)
        if new_end:
            campaign.end_date = new_end

    db.commit()
    
    return RedirectResponse(url="/admin/campaigns", status_code=303)


@router.post("/campaigns/{campaign_id}/send", response_class=HTMLResponse)
async def admin_campaign_send(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    """Send campaign SMS to all diamond customers.

    Safety: each customer gets a per-campaign dedup marker (like birthday SMS),
    so double-clicks never re-send; a configurable cap stops runaway blasts."""
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
    
    sent_count = 0
    skipped = 0
    for customer in diamond_customers:
        if sent_count >= limit:
            skipped += 1
            continue
        marker_key = f"campaign_sms_{campaign.id}_{customer.id}"
        if db.query(Settings).filter(Settings.key == marker_key).first():
            skipped += 1
            continue
        success = await send_campaign_sms(
            customer.phone,
            customer.first_name or "",
            campaign.name,
            campaign.code,
            campaign.discount_percent,
            db,
        )
        if success:
            db.add(Settings(key=marker_key, value="sent"))
            sent_count += 1
    
    db.commit()
    log_action(db, "campaign_sms", f"کمپین «{campaign.name}»: {sent_count} ارسال، {skipped} رد شد")

    message = f"پیامک کمپین به {sent_count} مشتری الماس ارسال شد."
    if skipped:
        message += f" ({skipped} مشتری به دلیل ارسال قبلی یا سقف {limit} رد شدند.)"
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
