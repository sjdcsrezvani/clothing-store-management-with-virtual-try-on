"""Campaigns: the list, the form, the report, and the send.

A campaign is only real once it has an audience and the discount it promises
actually reaches an invoice, so this router is thin — every rule lives in
:mod:`services.campaigns`, which is also what the counter and the customers
page read. The two things this file owns are the HTTP shape (filters, forms,
redirects with a message) and the send itself, which resolves a real list of
people, gives each of them a real assignment row, and reports honestly how many
were queued and why the rest were skipped.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Campaign,
    CampaignAssignment,
    Customer,
    Sale,
    SaleCampaign,
    to_english_digits,
)
from services._common import (
    fmt,
    is_archived_customer,
    jalali_str,
    page_arg,
    parse_form_date,
    parse_form_date_end,
)
from services.campaigns import (
    ASSIGNMENT_STATUS_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    assign_campaign,
    audience_options,
    campaign_filtered,
    campaign_overview,
    campaign_recipients,
    campaign_stats,
    customer_campaign_map,
    normalise_code,
    unassign_campaign,
)
from services.security import log_action, require_html_role
from services.sms import queue_sms
from services.sms_templates import get_template
from services.templating import templates

router = APIRouter(prefix="/admin")

STATUS_FILTERS = (
    ("all", "همه"),
    ("live", STATUS_LABELS["live"]),
    ("scheduled", STATUS_LABELS["scheduled"]),
    ("expired", STATUS_LABELS["expired"]),
    ("inactive", STATUS_LABELS["inactive"]),
)

ORDER_LABELS = {
    "newest": "جدیدترین",
    "name": "نام",
    "discount": "بیشترین تخفیف",
    "used": "بیشترین استفاده",
}


def _safe_next(value: str, fallback: str) -> str:
    """Only ever bounce back inside the admin area."""
    target = (value or "").strip()
    if target.startswith("/admin") and "//" not in target[1:]:
        return target
    return fallback


def _guard(request, db):
    return require_html_role(request, db, "manager")


def _values_from_form(
    name, code, discount_percent, min_purchase, start_date, end_date,
    is_reusable, is_active, edit_mode,
):
    return {
        "name": name or "",
        "code": normalise_code(code),
        "discount_percent": discount_percent or "",
        "min_purchase": min_purchase or "0",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "is_reusable": bool(is_reusable),
        "is_active": bool(is_active) if edit_mode else True,
    }


def _values_from_campaign(campaign: Campaign) -> dict:
    return {
        "name": campaign.name or "",
        "code": campaign.code or "",
        "discount_percent": campaign.discount_percent or "",
        "min_purchase": campaign.min_purchase or 0,
        "start_date": jalali_str(campaign.start_date, with_time=False) if campaign.start_date else "",
        "end_date": jalali_str(campaign.end_date, with_time=False) if campaign.end_date else "",
        "is_reusable": bool(campaign.is_reusable),
        "is_active": bool(campaign.is_active),
    }


# The campaign form's numeric fields, with the bounds _validate enforces and
# the label it refuses by. One table, two readers: the form paints its min/max
# from it, so a percent the browser allows and the server refuses cannot exist.
CAMPAIGN_NUMERIC_RULES = {
    "discount_percent": (1, 100, "درصد تخفیف"),
    "min_purchase": (0, None, "حداقل خرید"),
}


def _validate(db, *, campaign_id=None, values, start_dt, end_dt) -> str:
    """Everything the form can get wrong, in the order a person would fix it."""
    if not values["name"].strip():
        return "نام کمپین را وارد کنید."
    code = normalise_code(values["code"])
    if not code:
        return "کد تخفیف را وارد کنید."
    clash = db.query(Campaign).filter(Campaign.code == code).first()
    if clash and clash.id != campaign_id:
        return f"کد «{code}» قبلاً برای کمپین «{clash.name}» ثبت شده است."
    try:
        percent = int(to_english_digits(str(values["discount_percent"])))
    except (TypeError, ValueError):
        return "درصد تخفیف باید عدد باشد."
    if not CAMPAIGN_NUMERIC_RULES["discount_percent"][0] <= percent <= CAMPAIGN_NUMERIC_RULES["discount_percent"][1]:
        return "درصد تخفیف باید بین ۱ تا ۱۰۰ باشد."
    try:
        min_purchase = int(to_english_digits(str(values["min_purchase"] or 0)))
    except (TypeError, ValueError):
        return "حداقل خرید باید عدد باشد."
    if min_purchase < 0:
        return "حداقل خرید نمی‌تواند منفی باشد."
    if values["start_date"] and start_dt is None:
        return "تاریخ شروع معتبر نیست."
    if values["end_date"] and end_dt is None:
        return "تاریخ پایان معتبر نیست."
    if start_dt and end_dt and end_dt < start_dt:
        return "تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد."
    return ""


# ── list ──────────────────────────────────────────────────────────────────────

@router.get("/campaigns", response_class=HTMLResponse)
async def admin_campaigns(
    request: Request,
    search: str = "",
    status: str = "all",
    order: str = "newest",
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    page = page_arg(page)
    listing = campaign_filtered(db, search=search, status=status, order=order, page=page)
    return templates.TemplateResponse(request, "admin/campaigns.html", {
        **listing,
        "overview": campaign_overview(db),
        "status_filters": STATUS_FILTERS,
        "order_labels": ORDER_LABELS,
        "sources": SOURCE_LABELS,
        "assignment_labels": ASSIGNMENT_STATUS_LABELS,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


# ── add ───────────────────────────────────────────────────────────────────────

def _form_response(
    request, campaign, edit_mode, values, *, error="", message="", status_code=200,
):
    return templates.TemplateResponse(request, "admin/campaign_form.html", {
        "campaign": campaign,
        "edit_mode": edit_mode,
        "values": values,
        "error": error,
        "msg": message,
        "fmt": fmt,
        "jalali_str": jalali_str,
        # The form paints its bounds from the same table _validate enforces.
        "numeric_rules": CAMPAIGN_NUMERIC_RULES,
    }, status_code=status_code)


@router.get("/campaigns/add", response_class=HTMLResponse)
async def admin_campaign_add_form(request: Request, db: Session = Depends(get_db)):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    values = _values_from_form(None, None, None, None, None, None, False, True, False)
    return _form_response(request, None, False, values)


@router.post("/campaigns/add", response_class=HTMLResponse)
async def admin_campaign_add(
    request: Request,
    name: str = Form(""),
    code: str = Form(""),
    discount_percent: str = Form(""),
    min_purchase: str = Form("0"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_reusable: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    values = _values_from_form(
        name, code, discount_percent, min_purchase, start_date, end_date,
        is_reusable == "on", False, False,
    )
    start_dt = parse_form_date(start_date)
    end_dt = parse_form_date_end(end_date)
    error = _validate(db, values=values, start_dt=start_dt, end_dt=end_dt)
    if error:
        return _form_response(request, None, False, values, error=error, status_code=400)

    campaign = Campaign(
        name=name.strip(),
        code=normalise_code(code),
        discount_percent=int(to_english_digits(discount_percent)),
        min_purchase=int(to_english_digits(min_purchase or 0)),
        start_date=start_dt,
        end_date=end_dt,
        is_reusable=is_reusable == "on",
        is_active=True,
    )
    db.add(campaign)
    db.commit()
    log_action(
        db, "campaign_create", f"کمپین «{campaign.name}» ساخته شد",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"code": campaign.code, "discount_percent": campaign.discount_percent},
    )
    return RedirectResponse(
        url=f"/admin/campaigns/{campaign.id}?msg=کمپین ساخته شد — اکنون مشتریان را مشخص کنید.",
        status_code=303,
    )


@router.post("/campaigns/assign", response_class=HTMLResponse)
async def admin_campaign_assign_from_profile(
    request: Request,
    campaign_id: int = Form(0),
    customer_id: int = Form(0),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    """Put a customer on a campaign from the customer's own file.

    Declared before ``/campaigns/{campaign_id}`` on purpose: a static path has to
    be matched first or ``assign`` would be read as a campaign id.
    """
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not campaign or not customer:
        raise HTTPException(status_code=404, detail="کمپین یا مشتری یافت نشد")

    assign_campaign(db, campaign, customer, source="manual")
    db.commit()
    log_action(
        db, "campaign_assign",
        f"کمپین «{campaign.name}» به {customer.full_name} اختصاص یافت",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"customer_id": customer.id},
    )
    return RedirectResponse(
        url=_safe_next(next, f"/admin/campaigns/{campaign.id}")
        + f"?msg={customer.full_name} به کمپین اضافه شد.",
        status_code=303,
    )


# ── detail & edit ─────────────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_campaign_detail(
    campaign_id: int, request: Request, db: Session = Depends(get_db),
):
    """The report: who has it, who used it, and what it earned."""
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")

    assignment_rows = db.query(CampaignAssignment, Customer).join(
        Customer, Customer.id == CampaignAssignment.customer_id,
    ).filter(CampaignAssignment.campaign_id == campaign.id).all()
    assignments = [{
        "customer": customer,
        "assignment": assignment,
        "status_label": ASSIGNMENT_STATUS_LABELS.get(assignment.status, assignment.status),
        "source_label": SOURCE_LABELS.get(assignment.source, assignment.source),
        "invite_sent_at": assignment.invite_sent_at,
        "used_at": assignment.used_at,
        "used_count": assignment.used_count or 0,
    } for assignment, customer in assignment_rows]
    assignments.sort(key=lambda row: (
        {"invited": 0, "used": 1, "removed": 2}.get(row["assignment"].status, 3),
        row["customer"].full_name,
    ))

    redemptions = db.query(SaleCampaign, Sale).join(
        Sale, Sale.id == SaleCampaign.sale_id,
    ).filter(SaleCampaign.campaign_id == campaign.id).order_by(Sale.created_at.desc()).all()

    # Hand-picking is the other half of the audience: a customer can be put on
    # the campaign without waiting for a blast, and then be messaged with it.
    assigned_ids = {row["customer"].id for row in assignments}
    customer_picker = [
        {"id": customer.id, "name": customer.full_name, "phone": customer.phone}
        for customer in db.query(Customer).order_by(Customer.first_name).all()
        if customer.id not in assigned_ids and not is_archived_customer(customer)
    ]

    return templates.TemplateResponse(request, "admin/campaign_detail.html", {
        "campaign": campaign,
        "stats": campaign_stats(db, campaign),
        "assignments": assignments,
        "customer_picker": customer_picker,
        "redemptions": [{"sale": sale, "link": link} for link, sale in redemptions],
        "audience_options": audience_options(db, campaign),
        "assignment_labels": ASSIGNMENT_STATUS_LABELS,
        "sources": SOURCE_LABELS,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def admin_campaign_edit_form(
    campaign_id: int, request: Request, db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")
    return _form_response(request, campaign, True, _values_from_campaign(campaign))


@router.post("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_campaign_update(
    campaign_id: int,
    request: Request,
    name: str = Form(""),
    code: str = Form(""),
    discount_percent: str = Form(""),
    min_purchase: str = Form("0"),
    is_active: str = Form(""),
    is_reusable: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")

    values = _values_from_form(
        name, code, discount_percent, min_purchase, start_date, end_date,
        is_reusable == "on", is_active == "on", True,
    )
    start_dt = parse_form_date(start_date)
    end_dt = parse_form_date_end(end_date)
    error = _validate(db, campaign_id=campaign.id, values=values, start_dt=start_dt, end_dt=end_dt)
    if error:
        return _form_response(request, campaign, True, values, error=error, status_code=400)

    campaign.name = name.strip()
    campaign.code = normalise_code(code)
    campaign.discount_percent = int(to_english_digits(discount_percent))
    campaign.min_purchase = int(to_english_digits(min_purchase or 0))
    campaign.is_active = is_active == "on"
    campaign.is_reusable = is_reusable == "on"
    if start_date:
        campaign.start_date = start_dt
    else:
        campaign.start_date = None
    if end_date:
        campaign.end_date = end_dt
    else:
        campaign.end_date = None
    db.commit()

    log_action(
        db, "campaign_update", f"کمپین «{campaign.name}» ویرایش شد",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"code": campaign.code, "is_active": campaign.is_active,
               "is_reusable": campaign.is_reusable},
    )
    return RedirectResponse(url=f"/admin/campaigns/{campaign.id}?msg=تغییرات ذخیره شد.", status_code=303)


# ── send ──────────────────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/send", response_class=HTMLResponse)
async def admin_campaign_send(
    campaign_id: int,
    request: Request,
    audience: str = Form("all"),
    db: Session = Depends(get_db),
):
    """Queue the campaign SMS for a real audience.

    Every recipient gets an assignment row carrying the send date, which is
    both the re-send guard (nobody is messaged twice by this campaign) and the
    record the report shows. Anyone archived, opted out, or deliberately taken
    off the campaign is skipped and counted rather than messaged again.
    """
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")

    # The campaign text lives in the new پیامک page now; a template switched off
    # (or written as an empty pattern) still means «nothing to send», exactly as
    # it did when this read the settings row directly.
    template = get_template(db, "campaign")
    pattern = (template.body or "") if template is not None and template.is_active else ""
    if not pattern:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign.id}?err="
                "ابتدا متن پیامک کمپین را در صفحه پیامک بنویسید و فعال کنید.",
            status_code=303,
        )

    plan = campaign_recipients(db, campaign, audience)
    queued = 0
    for customer in plan["recipients"]:
        assignment = assign_campaign(db, campaign, customer, source="sms")
        db.flush()
        job = await queue_sms(
            pattern,
            customer.phone,
            {
                "var1": customer.first_name or "مشتری",
                "var2": campaign.name,
                "var3": campaign.code,
                "var4": str(campaign.discount_percent),
            },
            db,
            template=template,
            source="campaign",
            customer=customer,
        )
        if job is None:
            continue
        assignment.invite_sent_at = datetime.now(timezone.utc)
        queued += 1

    db.commit()
    log_action(
        db, "campaign_sms",
        f"کمپین «{campaign.name}» ({plan['audience_label']}): {queued} در صف از {plan['matched']} مشتری",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"queued": queued, "matched": plan["matched"], "audience": plan["audience"],
               "skipped": plan["skipped"]},
    )

    message = f"پیامک کمپین برای {queued} مشتری در صف قرار گرفت."
    if plan["capped"]:
        message += f" (سقف هر ارسال {plan['limit']} مشتری است.)"
    skipped = plan["skipped"]
    if skipped["already_sent"]:
        message += f" {skipped['already_sent']} مشتری قبلاً این پیامک را گرفته بودند."
    if skipped["opted_out"]:
        message += f" {skipped['opted_out']} مشتری انصراف از پیامک تبلیغاتی داشتند."
    if skipped["archived"]:
        message += f" {skipped['archived']} مشتری بایگانی شده بودند."
    if skipped["removed"]:
        message += f" {skipped['removed']} مشتری از این کمپین برداشته شده بودند."
    if not queued and not plan["matched"]:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign.id}?err=کسی برای ارسال نمانده است — همه دیده‌شده یا انصراف‌داده‌اند.",
            status_code=303,
        )
    return RedirectResponse(url=f"/admin/campaigns/{campaign.id}?msg={message}", status_code=303)


# ── assignment ────────────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/assign", response_class=HTMLResponse)
async def admin_campaign_assign(
    campaign_id: int,
    request: Request,
    customer_id: int = Form(0),
    phone: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="کمپین یافت نشد")

    customer = None
    if customer_id:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
    elif phone.strip():
        customer = db.query(Customer).filter(
            Customer.phone == to_english_digits(phone.strip())
        ).first()
    if not customer:
        return RedirectResponse(
            url=_safe_next(next, f"/admin/campaigns/{campaign.id}") + "?err=مشتری پیدا نشد.",
            status_code=303,
        )

    assign_campaign(db, campaign, customer, source="manual")
    db.commit()
    log_action(
        db, "campaign_assign", f"کمپین «{campaign.name}» به {customer.full_name} اختصاص یافت",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"customer_id": customer.id},
    )
    return RedirectResponse(
        url=_safe_next(next, f"/admin/campaigns/{campaign.id}")
        + f"?msg={customer.full_name} به کمپین اضافه شد.",
        status_code=303,
    )


@router.post("/campaigns/{campaign_id}/unassign", response_class=HTMLResponse)
async def admin_campaign_unassign(
    campaign_id: int,
    request: Request,
    customer_id: int = Form(0),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not campaign or not customer:
        raise HTTPException(status_code=404, detail="کمپین یا مشتری یافت نشد")

    removed = unassign_campaign(db, campaign, customer)
    db.commit()
    if not removed:
        return RedirectResponse(
            url=_safe_next(next, f"/admin/campaigns/{campaign.id}") + "?err=این مشتری در کمپین نبود.",
            status_code=303,
        )
    log_action(
        db, "campaign_unassign", f"کمپین «{campaign.name}» از {customer.full_name} برداشته شد",
        request=request, target_type="campaign", target_id=campaign.id,
        after={"customer_id": customer.id},
    )
    return RedirectResponse(
        url=_safe_next(next, f"/admin/campaigns/{campaign.id}")
        + f"?msg={customer.full_name} از کمپین برداشته شد.",
        status_code=303,
    )


# ── delete ────────────────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/delete", response_class=HTMLResponse)
async def admin_campaign_delete(
    campaign_id: int, request: Request, db: Session = Depends(get_db),
):
    guard = _guard(request, db)
    if not hasattr(guard, "role"):
        return guard

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        return RedirectResponse(url="/admin/campaigns", status_code=303)

    redemptions = db.query(SaleCampaign).filter(SaleCampaign.campaign_id == campaign.id).count()
    if redemptions:
        return RedirectResponse(
            url=(f"/admin/campaigns/{campaign.id}?err="
                 f"این کمپین روی {redemptions} فاکتور ثبت شده و حذف آن سابقه فروش را از بین می‌برد — "
                 f"برای توقف آن را غیرفعال کنید."),
            status_code=303,
        )

    name = campaign.name
    db.delete(campaign)
    db.commit()
    log_action(db, "campaign_delete", f"کمپین «{name}» حذف شد",
               request=request, target_type="campaign", target_id=campaign_id)
    return RedirectResponse(url=f"/admin/campaigns?msg=کمپین «{name}» حذف شد.", status_code=303)
