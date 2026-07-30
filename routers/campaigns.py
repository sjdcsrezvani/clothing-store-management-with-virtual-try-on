from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Campaign, to_english_digits
from services._common import fmt, check_admin
from services.sms import send_campaign_sms

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/campaigns", response_class=HTMLResponse)
async def admin_campaigns(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin/campaigns.html", {
        "campaigns": campaigns,
        "fmt": fmt,
    })


@router.get("/campaigns/add", response_class=HTMLResponse)
async def admin_campaign_add_form(request: Request):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    return templates.TemplateResponse(request, "admin/campaign_form.html", {
        "campaign": None,
        "edit_mode": False,
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
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    try:
        discount_int = int(to_english_digits(discount_percent))
    except ValueError:
        discount_int = 0
    
    try:
        min_purchase_int = int(to_english_digits(min_purchase))
    except ValueError:
        min_purchase_int = 0
    
    # Parse dates
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.strptime(to_english_digits(start_date), "%Y-%m-%d")
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(to_english_digits(end_date), "%Y-%m-%d")
        except ValueError:
            pass
    
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
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    
    return templates.TemplateResponse(request, "admin/campaign_form.html", {
        "campaign": campaign,
        "edit_mode": True,
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
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
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
        try:
            campaign.start_date = datetime.strptime(to_english_digits(start_date), "%Y-%m-%d")
        except ValueError:
            pass
    if end_date:
        try:
            campaign.end_date = datetime.strptime(to_english_digits(end_date), "%Y-%m-%d")
        except ValueError:
            pass
    
    db.commit()
    
    return RedirectResponse(url="/admin/campaigns", status_code=303)


@router.post("/campaigns/{campaign_id}/send", response_class=HTMLResponse)
async def admin_campaign_send(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    """Send campaign SMS to all diamond customers."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    
    # Get all diamond customers
    diamond_customers = db.query(Customer).filter(Customer.tier == "diamond").all()
    
    sent_count = 0
    for customer in diamond_customers:
        success = await send_campaign_sms(
            customer.phone,
            customer.first_name or "",
            campaign.name,
            campaign.code,
            campaign.discount_percent,
            db,
        )
        if success:
            sent_count += 1
    
    return templates.TemplateResponse(request, "admin/campaigns.html", {
        "campaigns": db.query(Campaign).order_by(Campaign.created_at.desc()).all(),
        "fmt": fmt,
        "message": f"پیامک کمپین به {sent_count} مشتری الماس ارسال شد.",
    })


@router.post("/campaigns/{campaign_id}/delete", response_class=HTMLResponse)
async def admin_campaign_delete(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign:
        db.delete(campaign)
        db.commit()

    return RedirectResponse(url="/admin/campaigns", status_code=303)
