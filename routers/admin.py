from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from database import get_db
from datetime import datetime, timezone
from models import (
    Campaign, Customer, Referral, Settings, Sale, SaleItem, SaleCampaign, GeneratedImage, AdminLog, POSTransaction, StockMovement,
    BusinessEvent, StaffUser, SalaryPayment,
)
from config import ADMIN_PASSWORD, API_TOKEN
from deployment import OWNER_MODE
from services.sms import (
    device_status_label,
    send_tier_up_gold_sms,
    send_tier_up_diamond_sms,
)
from services._common import (
    BIRTHDAY_SUBJECT_LABELS,
    BIRTHDAY_TARGET_KEY,
    BIRTHDAY_TARGET_LABELS,
    BIRTHDAY_TARGETS,
    CHILD_PROFILE_KEY,
    _to_persian_digits as to_persian_digits,
    birthday_display,
    birthday_subjects,
    child_profile_enabled,
    fmt,
    check_admin,
    get_birthday_target,
    get_setting_int as get_discount_setting,
    jalali_age,
    jalali_str,
    parse_jalali_input,
)
from services.customers import (
    ACTIVE_DAYS,
    INACTIVE_DAYS,
    SORTS,
    SORT_LABELS,
    STATUSES,
    STATUS_LABELS,
    TAG_KEYS,
    TAG_LABELS,
    TAG_PALETTE,
    archive_customer,
    birthday_fields,
    build_customer_rows,
    can_delete_customer,
    customer_overview,
    customer_profile,
    delete_customer,
    invalidate_customer_cache,
    is_archived_customer,
    list_customers,
    marketing_opt_in,
    parse_tags,
    serialize_tags,
    update_customer_meta,
)
from models import to_english_digits
from services.backup import create_backup, list_backups, backup_download_path
from services.operations import verify_sqlite_backup
from services.security import (
    check_admin_password,
    login_locked,
    login_failure,
    login_success,
    log_action,
    set_admin_password,
    authenticate_staff,
    ensure_owner_account,
    require_role,
    current_staff_user,
    hash_password,
    require_html_role,
)
from services.store import invalidate_store_cache, get_store
from services.templating import templates
from services.tier import (
    get_tier_config,
    get_customers_for_birthday_check,
    tier_up_candidates,
    tier_up_marker_key,
    tier_up_sent_rank,
    TIER_RANK,
)
from services.events import event_history, event_payload, append_event
from services.payroll import create_salary_payment, current_period_key
from services.themes import THEMES, DEFAULT_THEME_ID, THEME_SETTING_KEY, CUSTOM_PRIMARY_KEY, CUSTOM_SECONDARY_KEY, all_theme_previews, validate_hex, contrast_ratio, get_theme

router = APIRouter(prefix="/admin")


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin/login.html")


@router.post("/login", response_class=HTMLResponse)
async def admin_login(request: Request, username: str = Form("owner"), password: str = Form(...), db: Session = Depends(get_db)):
    if login_locked(request):
        log_action(db, "login_blocked", "بیش از حد تلاش ناموفق", request=request)
        return templates.TemplateResponse(request, "admin/login.html", {
            "error": "تلاش‌های ناموفق زیاد بود. چند دقیقه بعد دوباره امتحان کنید."
        })
    user = authenticate_staff(db, username or "owner", password)
    if user:
        login_success(request)
        request.session.clear()
        request.session["staff_user_id"] = user.id
        request.session["staff_role"] = user.role
        request.session["api_token"] = API_TOKEN
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        log_action(db, "login", "ورود موفق", request=request, target_type="staff_user", target_id=user.id)
        return RedirectResponse(url="/admin", status_code=303)
    login_failure(request)
    log_action(db, "login_failed", "رمز عبور اشتباه", request=request)
    return templates.TemplateResponse(request, "admin/login.html", {"error": "رمز عبور اشتباه است", "username": username})


@router.post("/logout", response_class=HTMLResponse)
async def admin_logout(request: Request, db: Session = Depends(get_db)):
    if current_staff_user(db, request):
        log_action(db, "logout", "خروج", request=request)
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


# ── First-run setup wizard ─────────────────────────────────────────────────

def _setup_completed(db) -> bool:
    """True when an admin password hash already exists in the settings table."""
    from services.security import _PASSWORD_SETTING_KEY
    row = db.query(Settings).filter(Settings.key == _PASSWORD_SETTING_KEY).first()
    return bool(row and row.value)


@router.get("/setup", response_class=HTMLResponse)
async def admin_setup_page(request: Request, db: Session = Depends(get_db)):
    """First-run setup wizard for an explicitly developer-enabled demo build.
    It is never available in the owner's private build."""
    if OWNER_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    if _setup_completed(db):
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(request, "admin/setup.html", {
        "error": "",
    })


@router.post("/setup", response_class=HTMLResponse)
async def admin_setup(
    request: Request,
    store_name: str = Form(...),
    admin_password: str = Form(...),
    admin_password_confirm: str = Form(...),
    store_tagline: str = Form(""),
    store_instagram: str = Form(""),
    sms_api_key: str = Form(""),
    sms_device_id: str = Form(""),
    tryon_api_key: str = Form(""),
    db: Session = Depends(get_db),
):
    """Complete first-run setup in a developer-enabled sales/demo build.
    Owner mode rejects provisioning entirely."""
    if OWNER_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    if _setup_completed(db):
        return RedirectResponse(url="/admin/login", status_code=303)

    errors = []
    if not store_name.strip():
        errors.append("نام فروشگاه الزامی است.")
    if len(admin_password) < 6:
        errors.append("رمز عبور باید حداقل ۶ کاراکتر باشد.")
    if admin_password != admin_password_confirm:
        errors.append("رمز عبور و تکرار آن یکسان نیستند.")
    if errors:
        return templates.TemplateResponse(request, "admin/setup.html", {
            "error": " • ".join(errors),
            "store_name": store_name,
            "store_tagline": store_tagline,
            "store_instagram": store_instagram,
        })

    # 1. Hash and seed the admin password.
    set_admin_password(db, admin_password)

    # 2. Save store branding to the settings table.
    for key, value in [("store_name", store_name.strip()),
                       ("store_tagline", store_tagline.strip()),
                       ("store_instagram", store_instagram.strip())]:
        row = db.query(Settings).filter(Settings.key == key).first()
        if row:
            row.value = value
        else:
            db.add(Settings(key=key, value=value))

    # 3. Save SMS/try-on settings to the settings table (these override .env
    #    at runtime; .env is the fallback for first boot).
    if sms_api_key.strip():
        row = db.query(Settings).filter(Settings.key == "sms_api_key").first()
        if row:
            row.value = sms_api_key.strip()
        else:
            db.add(Settings(key="sms_api_key", value=sms_api_key.strip()))
    if sms_device_id.strip():
        row = db.query(Settings).filter(Settings.key == "sms_device_id").first()
        if row:
            row.value = sms_device_id.strip()
        else:
            db.add(Settings(key="sms_device_id", value=sms_device_id.strip()))
    if tryon_api_key.strip():
        row = db.query(Settings).filter(Settings.key == "tryon_api_key").first()
        if row:
            row.value = tryon_api_key.strip()
        else:
            db.add(Settings(key="tryon_api_key", value=tryon_api_key.strip()))

    # 4. Write a marker file so desktop_entry knows setup is done.
    import os
    marker = os.path.join(os.getcwd(), ".setup_done")
    with open(marker, "w") as f:
        f.write("1")

    db.commit()
    invalidate_store_cache()
    log_action(db, "setup_complete", f"راه‌اندازی اولیه: {store_name.strip()}", request=request, target_type="settings", after={"store_name": store_name.strip()})

    return RedirectResponse(url="/admin/login?msg=راه‌اندازی تکمیل شد. اکنون وارد شوید.", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    total_customers = db.query(Customer).count()
    total_referrals = db.query(Referral).count()
    customers_with_discount = db.query(Customer).filter(Customer.referrer_discount > 0).count()
    sms_balance = device_status_label(db)
    
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
        sent = int(birthday_msg or 0)
        skipped = int(request.query_params.get("birthday_skip", 0) or 0)
        eligible = int(request.query_params.get("birthday_eligible", 0) or 0)
        if eligible == 0:
            action_message = "🎂 هیچ مشتری با تولد ۷ روز آینده وجود ندارد."
        elif sent == 0 and skipped == 0:
            action_message = "🎂 متن پیامک تولد در تنظیمات نوشته نشده است."
        elif sent == 0:
            action_message = f"🎂 پیامکی ارسال نشد — {skipped} مشتری قبلاً ارسال شده بودند."
        elif skipped:
            action_message = f"🎂 {sent} پیامک تولد ارسال شد • {skipped} مشتری قبلاً ارسال شده بود (رد شد)."
        else:
            action_message = f"🎂 {sent} پیامک تولد ارسال شد."
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
        "jalali_str": jalali_str,
    })


@router.get("/customers", response_class=HTMLResponse)
async def admin_customers(
    request: Request,
    search: str = "",
    sort: str = "date",
    tier: str = "",
    status: str = "all",
    tag: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    """The customer list: who they are, what they bought, and what is due."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    listing = list_customers(
        db, search=search, tier=tier, status=status, tag=tag, sort=sort, page=page,
    )

    return templates.TemplateResponse(request, "admin/customers.html", {
        **listing,
        "rows": build_customer_rows(db, listing["customers"]),
        "overview": customer_overview(db),
        "active_days": ACTIVE_DAYS,
        "inactive_days": INACTIVE_DAYS,
        "status_labels": STATUS_LABELS,
        "sort_labels": SORT_LABELS,
        "tag_palette": TAG_PALETTE,
        "tag_labels": TAG_LABELS,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "tier_config": get_tier_config(db),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
async def admin_customer_profile(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """One customer's file: purchases, discounts, referrals, debt and details."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    may_delete, sale_count = can_delete_customer(db, customer)
    # The campaign card: which campaigns this customer holds, plus the ones the
    # owner can still put them on from right here.
    from services.campaigns import (
        ASSIGNMENT_STATUS_LABELS as CAMPAIGN_ASSIGNMENT_LABELS,
        SOURCE_LABELS as CAMPAIGN_SOURCE_LABELS,
        STATUS_LABELS as CAMPAIGN_STATUS_LABELS,
        campaign_is_live,
        campaign_status,
        customer_campaign_history,
    )

    profile = customer_profile(db, customer)
    held_ids = {row["campaign"].id for row in profile["campaign_history"]
                if row["status"] != "removed"}
    assignable = [
        {
            "campaign": campaign,
            "live": campaign_is_live(campaign),
            "campaign_status_label": CAMPAIGN_STATUS_LABELS[campaign_status(campaign)],
        }
        for campaign in db.query(Campaign).order_by(Campaign.created_at.desc()).all()
        if campaign.id not in held_ids
    ]
    return templates.TemplateResponse(request, "admin/customer_detail.html", {
        "customer": customer,
        "profile": profile,
        "campaign_history": profile["campaign_history"],
        "assignable_campaigns": assignable,
        "campaign_source_labels": CAMPAIGN_SOURCE_LABELS,
        "campaign_assignment_labels": CAMPAIGN_ASSIGNMENT_LABELS,
        "birthday_fields": birthday_fields(customer, db),
        "birthday_display": birthday_display,
        "jalali_age": jalali_age,
        "tag_palette": TAG_PALETTE,
        "tag_labels": TAG_LABELS,
        "tag_keys": TAG_KEYS,
        "may_delete": may_delete,
        "sale_count": sale_count,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "tier_config": get_tier_config(db),
        "monthly_limit": get_discount_setting(db, "monthly_referral_limit", 10),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/customers/{customer_id}/meta", response_class=HTMLResponse)
async def admin_customer_meta(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """Save the profile card: birthdays, note, tags and SMS consent."""
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
        # Only send the fields the form actually owns, so an absent checkbox
        # means "off" rather than "leave as it was".
        notes=form.get("notes", None),
        tags=form.getlist("tags") or [],
        sms_opt_in="sms_opt_in" in form,
        # The «for whom» choice decides which of the two birthday groups is
        # written, so the card can post both and the server still stores one.
        buys_for=form.get("buys_for"),
        birth_value=form.get("birth_date"),
        child_name=form.get("child_name"),
        child_birth_value=form.get("child_birthday"),
    )
    db.commit()
    log_action(
        db, "customer_update", f"به‌روزرسانی پرونده {customer.phone}", request=request,
        target_type="customer", target_id=customer.id,
        after={"tags": customer.tags, "sms_opt_in": marketing_opt_in(customer),
               "birth_month_day": customer.birth_month_day},
    )
    return RedirectResponse(url=f"/admin/customers/{customer.id}?msg=پرونده ذخیره شد.", status_code=303)


@router.post("/customers/{customer_id}/archive", response_class=HTMLResponse)
async def admin_archive_customer(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """Archive or restore a customer — the alternative to deleting a history."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    archived = archive_customer(db, customer, not is_archived_customer(customer))
    db.commit()
    log_action(
        db, "customer_archive" if archived else "customer_restore",
        f"{'بایگانی' if archived else 'بازگردانی'} مشتری {customer.phone}",
        request=request, target_type="customer", target_id=customer.id,
    )
    message = "مشتری بایگانی شد." if archived else "مشتری از بایگانی بازگشت."
    return RedirectResponse(url=f"/admin/customers/{customer.id}?msg={message}", status_code=303)


@router.post("/customers/{customer_id}/delete", response_class=HTMLResponse)
async def admin_delete_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(url="/admin/customers", status_code=303)

    # Deleting a customer nulls Sale.customer_id, which silently detaches the
    # purchase history the reports are built on. Archive instead.
    may_delete, sale_count = can_delete_customer(db, customer)
    if not may_delete:
        message = f"این مشتری {sale_count} خرید ثبت‌شده دارد؛ به‌جای حذف، بایگانی کنید."
        return RedirectResponse(url=f"/admin/customers/{customer.id}?err={message}", status_code=303)

    phone = customer.phone
    delete_customer(db, customer)
    db.commit()
    log_action(db, "customer_delete", f"مشتری {phone}", request=request, target_type="customer", before={"phone": phone})
    return RedirectResponse(url="/admin/customers?msg=مشتری حذف شد.", status_code=303)


def _parse_staff_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) >= 4 and value[:4].isdigit() and int(value[:4]) >= 1900:
            parsed = datetime.fromisoformat(value[:10])
            return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return parse_jalali_input(value)


def _staff_amount(value: str, default: int = 0) -> int:
    try:
        amount = int(to_english_digits((value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default
    return amount


def _staff_profile_data(form):
    employment_type = str(form.get("employment_type", "full_time")).strip()
    if employment_type not in {"full_time", "part_time", "contractor"}:
        raise ValueError("نوع همکاری نامعتبر است.")
    salary_amount = _staff_amount(str(form.get("salary_amount", "0")))
    if salary_amount < 0:
        raise ValueError("حقوق ماهانه نمی‌تواند منفی باشد.")
    salary_day_value = to_english_digits(str(form.get("salary_payment_day", "")).strip())
    salary_day = None
    if salary_day_value:
        try:
            salary_day = int(salary_day_value)
        except ValueError:
            raise ValueError("روز پرداخت حقوق نامعتبر است.")
        if not 1 <= salary_day <= 31:
            raise ValueError("روز پرداخت حقوق باید بین ۱ تا ۳۱ باشد.")
    return {
        "full_name": str(form.get("full_name", "")).strip()[:200] or None,
        "employee_code": str(form.get("employee_code", "")).strip()[:50] or None,
        "national_id": str(form.get("national_id", "")).strip()[:30] or None,
        "phone": str(form.get("phone", "")).strip()[:30] or None,
        "email": str(form.get("email", "")).strip()[:150] or None,
        "job_title": str(form.get("job_title", "")).strip()[:100] or None,
        "employment_type": employment_type,
        "hire_date": _parse_staff_date(str(form.get("hire_date", ""))),
        "birth_date": _parse_staff_date(str(form.get("birth_date", ""))),
        "contract_end_date": _parse_staff_date(str(form.get("contract_end_date", ""))),
        "education": str(form.get("education", "")).strip()[:200] or None,
        "work_schedule": str(form.get("work_schedule", "")).strip()[:200] or None,
        "salary_payment_day": salary_day,
        "address": str(form.get("address", "")).strip()[:2000] or None,
        "emergency_contact": str(form.get("emergency_contact", "")).strip()[:200] or None,
        "bank_account": str(form.get("bank_account", "")).strip()[:80] or None,
        "iban": str(form.get("iban", "")).strip()[:40] or None,
        "salary_amount": salary_amount,
        "notes": str(form.get("notes", "")).strip()[:4000] or None,
    }


@router.get("/staff", response_class=HTMLResponse)
async def admin_staff(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    return templates.TemplateResponse(request, "admin/staff.html", {
        "staff_users": db.query(StaffUser).order_by(StaffUser.created_at.desc()).all(),
        "owner_settings": {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()},
        "current_period": current_period_key(),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/staff", response_class=HTMLResponse)
async def admin_staff_create(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    role = str(form.get("role", "cashier")).strip()
    if not username or len(password) < 6 or role not in {"cashier", "manager", "owner"}:
        return RedirectResponse(url="/admin/staff?err=اطلاعات ورود کاربر نامعتبر است.", status_code=303)
    if db.query(StaffUser).filter(StaffUser.username == username).first():
        return RedirectResponse(url="/admin/staff?err=نام کاربری تکراری است.", status_code=303)
    try:
        profile = _staff_profile_data(form)
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff?err={error}", status_code=303)
    user = StaffUser(username=username, password_hash=hash_password(password), role=role, **profile)
    db.add(user)
    db.commit()
    log_action(db, "staff_create", f"ایجاد کاربر {username}", request=request, target_type="staff_user", target_id=user.id, after={"username": username, "role": role, "full_name": user.full_name, "salary_amount": user.salary_amount})
    return RedirectResponse(url="/admin/staff?msg=کاربر و اطلاعات پرسنلی ثبت شد.", status_code=303)


@router.post("/staff/{staff_id}", response_class=HTMLResponse)
async def admin_staff_update(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    form = await request.form()
    role = str(form.get("role", user.role)).strip()
    if role not in {"cashier", "manager", "owner"}:
        return RedirectResponse(url="/admin/staff?err=نقش کاربر نامعتبر است.", status_code=303)
    try:
        profile = _staff_profile_data(form)
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff?err={error}", status_code=303)
    before = {"role": user.role, "full_name": user.full_name, "salary_amount": user.salary_amount, "is_active": user.is_active}
    user.role = role
    for key, value in profile.items():
        setattr(user, key, value)
    password = str(form.get("password", ""))
    if password:
        if len(password) < 6:
            return RedirectResponse(url="/admin/staff?err=رمز عبور باید حداقل ۶ کاراکتر باشد.", status_code=303)
        user.password_hash = hash_password(password)
    db.commit()
    log_action(db, "staff_update", f"ویرایش کاربر {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after={"role": user.role, "full_name": user.full_name, "salary_amount": user.salary_amount, "is_active": user.is_active})
    return RedirectResponse(url="/admin/staff?msg=اطلاعات کارمند ذخیره شد.", status_code=303)


@router.post("/staff/{staff_id}/salary", response_class=HTMLResponse)
async def admin_staff_salary(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    form = await request.form()
    try:
        payment = create_salary_payment(
            db,
            user,
            guard,
            str(form.get("period_key", "")),
            deductions=_staff_amount(str(form.get("deductions", "0"))),
            payment_method=str(form.get("payment_method", "cash")),
            note=str(form.get("note", "")),
            request_id=request.headers.get("X-Request-ID"),
        )
        db.commit()
    except (ValueError, IntegrityError) as error:
        db.rollback()
        message = str(error) if isinstance(error, ValueError) else "حقوق این کارمند برای این ماه قبلاً ثبت شده است."
        return RedirectResponse(url=f"/admin/staff?err={message}", status_code=303)
    log_action(db, "salary_payment", f"پرداخت حقوق {user.username} برای {payment.period_key}", request=request, target_type="salary_payment", target_id=payment.id, after={"net_amount": payment.net_amount, "expense_id": payment.expense_id})
    return RedirectResponse(url=f"/admin/payroll/{payment.id}/receipt?msg=پرداخت حقوق ثبت شد.", status_code=303)


@router.get("/payroll/{payment_id}/receipt", response_class=HTMLResponse)
async def admin_salary_receipt(payment_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="رسید حقوق یافت نشد")
    return templates.TemplateResponse(request, "admin/salary_receipt.html", {
        "payment": payment,
        "staff_user": payment.staff_user,
        "owner_settings": {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()},
        "store": get_store(db),
        "msg": request.query_params.get("msg", ""),
        "jalali_str": jalali_str,
        "fmt": fmt,
    })


@router.get("/staff/{staff_id}/contract", response_class=HTMLResponse)
async def admin_staff_contract(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    staff_user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="کارمند یافت نشد")
    return templates.TemplateResponse(request, "admin/employment_contract.html", {
        "staff_user": staff_user,
        "owner_settings": {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()},
        "store": get_store(db),
        "jalali_str": jalali_str,
        "fmt": fmt,
    })


@router.get("/owner-profile", response_class=HTMLResponse)
async def admin_owner_profile(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    settings = {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()}
    return templates.TemplateResponse(request, "admin/owner_profile.html", {
        "owner_settings": settings,
        "store": get_store(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/owner-profile", response_class=HTMLResponse)
async def admin_owner_profile_update(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    allowed = {"owner_full_name", "owner_national_id", "owner_phone", "owner_email", "owner_address", "owner_business_name", "owner_business_registration", "owner_signatory_title"}
    updates = {key: str(form.get(key, "")).strip()[:1000] for key in allowed}
    for key, value in updates.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    log_action(db, "owner_profile_update", "به‌روزرسانی اطلاعات مالک و قرارداد", request=request, target_type="owner_profile")
    return RedirectResponse(url="/admin/owner-profile?msg=اطلاعات مالک ذخیره شد.", status_code=303)


@router.post("/staff/{staff_id}/disable", response_class=HTMLResponse)
async def admin_staff_disable(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.username == "owner":
        return RedirectResponse(url="/admin/staff?err=کاربر مالک اصلی را نمی‌توان غیرفعال کرد.", status_code=303)
    before = {"is_active": user.is_active}
    user.is_active = False
    db.commit()
    log_action(db, "staff_disable", f"غیرفعال‌سازی کاربر {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after={"is_active": False})
    return RedirectResponse(url="/admin/staff?msg=کاربر غیرفعال شد.", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    settings = {s.key: s.value for s in db.query(Settings).all()}
    tier_config = get_tier_config(db)
    return templates.TemplateResponse(request, "admin/settings.html", {
        "settings": settings,
        "tier_config": tier_config,
        "store": get_store(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


def get_theme_id(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
    return row.value if row and row.value in THEMES else DEFAULT_THEME_ID


@router.get("/settings/appearance", response_class=HTMLResponse)
async def admin_settings_appearance(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    settings = {s.key: s.value for s in db.query(Settings).all()}
    return templates.TemplateResponse(request, "admin/settings_appearance.html", {
        "settings": settings,
        "store": get_store(db),
        "themes": all_theme_previews(db),
        "active_theme_id": get_theme_id(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/settings/appearance", response_class=HTMLResponse)
async def admin_update_appearance(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    theme_id = str(form.get(THEME_SETTING_KEY, DEFAULT_THEME_ID)).strip()
    primary = str(form.get(CUSTOM_PRIMARY_KEY, "#C94B68")).strip().upper()
    secondary = str(form.get(CUSTOM_SECONDARY_KEY, "#197A8C")).strip().upper()
    if theme_id not in THEMES:
        return RedirectResponse(url="/admin/settings/appearance?err=تم انتخاب نامعتبر است.", status_code=303)
    if theme_id == "custom-brand":
        if not validate_hex(primary) or not validate_hex(secondary):
            return RedirectResponse(url="/admin/settings/appearance?err=رنگ‌ها باید به صورت HEX شش‌رقمی باشند.", status_code=303)
        if contrast_ratio(primary, "#FFFFFF") < 4.5 and contrast_ratio(primary, "#000000") < 4.5:
            return RedirectResponse(url="/admin/settings/appearance?err=رنگ اصلی کنتراست کافی ندارد.", status_code=303)
        if contrast_ratio(secondary, "#FFFFFF") < 3 and contrast_ratio(secondary, "#000000") < 3:
            return RedirectResponse(url="/admin/settings/appearance?err=رنگ دوم کنتراست کافی ندارد.", status_code=303)

    updates = {THEME_SETTING_KEY: theme_id, CUSTOM_PRIMARY_KEY: primary, CUSTOM_SECONDARY_KEY: secondary}
    for key, value in updates.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    invalidate_store_cache()
    log_action(db, "theme_update", "به‌روزرسانی ظاهر فروشگاه", request=request, target_type="settings", after={"theme": theme_id})

    return RedirectResponse(url="/admin/settings/appearance?msg=ظاهر فروشگاه ذخیره شد.", status_code=303)


@router.post("/settings", response_class=HTMLResponse)
async def admin_update_settings(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    updates = {}
    # Later values win, which is what makes an unchecked checkbox work: the form
    # posts a hidden companion (0) before the box itself (1).
    for key, value in form.items():
        if key in {"csrf_token", THEME_SETTING_KEY, CUSTOM_PRIMARY_KEY, CUSTOM_SECONDARY_KEY}:
            continue
        updates[key] = str(value)
    # A birthday target that names a module the store switched off would
    # contradict itself, so it falls back rather than silently doing nothing.
    child_off = str(updates.get(CHILD_PROFILE_KEY, "1")).strip().lower() in (
        "0", "false", "no", "off",
    )
    if child_off and updates.get(BIRTHDAY_TARGET_KEY) == "child":
        updates[BIRTHDAY_TARGET_KEY] = "customer"
    raw_length = str(form.get("barcode_code_length", "")).strip()
    if raw_length:
        try:
            code_length = int(to_english_digits(raw_length))
        except (TypeError, ValueError):
            code_length = 0
        if code_length < 4 or code_length > 12:
            return RedirectResponse(url="/admin/settings?err=تعداد رقم کد بارکد باید بین ۴ تا ۱۲ باشد.", status_code=303)
    for key, value in updates.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    invalidate_store_cache()
    invalidate_customer_cache()
    log_action(db, "settings_update", "به‌روزرسانی تنظیمات", request=request, target_type="settings")

    return RedirectResponse(url="/admin/settings?msg=تنظیمات ذخیره شد.", status_code=303)


@router.post("/change-password", response_class=HTMLResponse)
async def admin_change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    if not check_admin_password(db, current_password):
        return RedirectResponse(url="/admin/settings?err=رمز عبور فعلی اشتباه است.", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse(url="/admin/settings?err=رمز جدید باید حداقل ۶ کاراکتر باشد.", status_code=303)
    if new_password != new_password_confirm:
        return RedirectResponse(url="/admin/settings?err=رمز جدید و تکرار آن یکسان نیستند.", status_code=303)

    set_admin_password(db, new_password)
    log_action(db, "change_password", "تغییر رمز عبور مدیریت", request=request, target_type="settings")
    return RedirectResponse(url="/admin/settings?msg=رمز عبور با موفقیت تغییر کرد.", status_code=303)


@router.post("/backup", response_class=HTMLResponse)
async def admin_backup_now(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    path = create_backup()
    log_action(db, "backup", f"پشتیبان‌گیری دستی: {path or 'ناموفق'}", request=request, target_type="backup")
    if path:
        return RedirectResponse(url="/admin/backups?msg=پشتیبان‌گیری انجام شد.", status_code=303)
    return RedirectResponse(url="/admin/backups?err=پشتیبان‌گیری انجام نشد (فایل دیتابیس موجود نیست).", status_code=303)


@router.get("/backups", response_class=HTMLResponse)
async def admin_backups(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    backups = list_backups()
    for backup in backups:
        backup.update(verify_sqlite_backup(Path("backups") / backup["name"]))
    return templates.TemplateResponse(request, "admin/backups.html", {
        "backups": backups,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "jalali_str": jalali_str,
    })


@router.get("/backups/download", response_class=HTMLResponse)
async def admin_backup_download(request: Request, name: str = "", db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    path = backup_download_path(name)
    if not path:
        return RedirectResponse(url="/admin/backups?err=فایل یافت نشد.", status_code=303)
    return FileResponse(path, filename=Path(name).name)


@router.get("/pos-reconciliation", response_class=HTMLResponse)
async def admin_pos_reconciliation(request: Request, db: Session = Depends(get_db)):
    """Show terminal attempts that need local reconciliation."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    transactions = db.query(POSTransaction).order_by(POSTransaction.created_at.desc()).limit(200).all()
    unresolved_count = sum(
        1 for transaction in transactions
        if transaction.status in {"created", "sent", "uncertain", "approved"}
        and transaction.sale_id is None
        and not transaction.reconciled
    )
    return templates.TemplateResponse(request, "admin/pos_reconciliation.html", {
        "transactions": transactions,
        "unresolved_count": unresolved_count,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "jalali_str": jalali_str,
        "fmt": fmt,
    })


@router.post("/pos-reconciliation/{transaction_id}/review", response_class=HTMLResponse)
async def admin_pos_reconciliation_review(
    transaction_id: int,
    request: Request,
    resolution_type: str = Form(""),
    evidence: str = Form(""),
    note: str = Form(""),
    provider_reference: str = Form(""),
    terminal_transaction_number: str = Form(""),
    retrieval_reference_number: str = Form(""),
    masked_card: str = Form(""),
    db: Session = Depends(get_db),
):
    """Record an evidence-backed reconciliation decision."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    transaction = db.query(POSTransaction).filter(POSTransaction.id == transaction_id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="تراکنش کارت‌خوان یافت نشد")
    allowed = {"confirmed_paid", "confirmed_cancelled", "reversed_externally", "duplicate", "terminal_error", "provider_investigation"}
    if not resolution_type and note.strip():
        resolution_type = "terminal_error"
    if not resolution_type:
        return RedirectResponse(url="/admin/pos-reconciliation?err=نوع نتیجه بررسی الزامی است.", status_code=303)
    if not evidence.strip() and note.strip():
        evidence = note
    if resolution_type not in allowed or not evidence.strip():
        return RedirectResponse(url="/admin/pos-reconciliation?err=نوع نتیجه و مدرک بررسی الزامی است.", status_code=303)
    if resolution_type == "confirmed_paid":
        raise HTTPException(status_code=409, detail="تأیید پرداخت باید از مسیر ایجاد فاکتور انجام شود.")
    if masked_card and not masked_card.startswith("****"):
        raise HTTPException(status_code=400, detail="فقط اطلاعات کارت ماسک‌شده مجاز است.")

    if transaction.reconciled:
        raise HTTPException(status_code=409, detail="این تراکنش قبلاً تطبیق داده شده است.")

    operator = guard
    transaction.provider_reference = provider_reference.strip()[:100] or None
    transaction.terminal_transaction_number = terminal_transaction_number.strip()[:100] or None
    transaction.retrieval_reference_number = retrieval_reference_number.strip()[:100] or None
    transaction.masked_card = masked_card.strip()[:32] or None
    transaction.resolution_type = resolution_type
    transaction.resolution_evidence = evidence.strip()[:2000]
    transaction.reconciled = True
    transaction.reconciliation_note = transaction.resolution_evidence
    transaction.operator_user_id = operator.id
    transaction.reconciled_at = datetime.now(timezone.utc)
    transaction.last_retry_at = datetime.now(timezone.utc)
    append_event(
        db,
        "POSReconciled",
        "pos_transaction",
        transaction.id,
        idempotency_key=f"pos:{transaction.id}:reconciled",
        actor_user_id=operator.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "resolution_type": resolution_type,
            "provider_reference": transaction.provider_reference,
            "terminal_transaction_number": transaction.terminal_transaction_number,
            "retrieval_reference_number": transaction.retrieval_reference_number,
            "masked_card": transaction.masked_card,
            "evidence_recorded": True,
        },
        occurred_at=transaction.reconciled_at,
    )
    db.commit()
    log_action(db, "pos_reconciliation", f"تطبیق تراکنش کارت‌خوان #{transaction.id}", request=request, target_type="pos_transaction", target_id=transaction.id, after={"resolution_type": resolution_type, "operator_user_id": operator.id})
    return RedirectResponse(url="/admin/pos-reconciliation?msg=نتیجه تطبیق ثبت شد.", status_code=303)


@router.get("/events", response_class=HTMLResponse)
async def admin_events(
    request: Request,
    aggregate_type: str = "",
    event_type: str = "",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    events = event_history(
        db,
        aggregate_type=aggregate_type.strip() or None,
        event_type=event_type.strip() or None,
        limit=limit,
    )
    return templates.TemplateResponse(request, "admin/events.html", {
        "events": events,
        "event_payload": event_payload,
        "aggregate_type": aggregate_type,
        "event_type": event_type,
        "limit": limit,
    })


@router.get("/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    logs = db.query(AdminLog).order_by(AdminLog.created_at.desc()).limit(100).all()
    return templates.TemplateResponse(request, "admin/logs.html", {
        "logs": logs,
        "jalali_str": jalali_str,
    })


@router.post("/reset-database", response_class=HTMLResponse)
async def admin_reset_database(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from models import (
        CheckoutEvent,
        CheckoutSession,
        FinancialEntry,
        Payment,
        PaymentReversal,
        Refund,
        ProductVariant,
        RefundLine,
        StockReservation,
    )
    refund_ids = [row.id for row in db.query(Refund.id).all()]
    reversal_ids = [row.id for row in db.query(PaymentReversal.id).all()]

    append_event(
        db,
        "DatabaseReset",
        "database",
        None,
        idempotency_key=f"database-reset:{datetime.now(timezone.utc).isoformat()}",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
        payload={
            "sales": db.query(Sale).count(),
            "customers": db.query(Customer).count(),
            "pos_transactions": db.query(POSTransaction).count(),
        },
    )

    if refund_ids:
        db.query(FinancialEntry).filter(FinancialEntry.refund_id.in_(refund_ids)).delete(synchronize_session=False)
    if reversal_ids:
        db.query(FinancialEntry).filter(FinancialEntry.payment_reversal_id.in_(reversal_ids)).delete(synchronize_session=False)
    db.query(RefundLine).delete(synchronize_session=False)
    db.query(PaymentReversal).delete(synchronize_session=False)
    db.query(Refund).delete(synchronize_session=False)
    db.query(CheckoutEvent).delete(synchronize_session=False)
    db.query(StockReservation).delete(synchronize_session=False)
    db.query(ProductVariant).update(
        {ProductVariant.reserved_quantity: 0},
        synchronize_session=False,
    )
    db.query(CheckoutSession).delete(synchronize_session=False)
    db.query(SaleCampaign).delete(synchronize_session=False)
    db.query(SaleItem).delete(synchronize_session=False)
    db.query(StockMovement).filter(StockMovement.sale_id.isnot(None)).delete(synchronize_session=False)
    db.query(POSTransaction).delete(synchronize_session=False)
    db.query(Payment).delete(synchronize_session=False)
    db.query(Sale).delete(synchronize_session=False)
    db.query(GeneratedImage).delete(synchronize_session=False)
    db.query(Referral).delete(synchronize_session=False)
    db.query(Customer).update(
        {Customer.referred_by: None},
        synchronize_session=False,
    )
    db.query(Customer).delete(synchronize_session=False)
    db.commit()
    log_action(db, "reset_database", "ریست کامل دیتابیس", request=request, target_type="database")

    return RedirectResponse(url="/admin", status_code=303)


def _birthday_marker(db: Session, customer, occasion: str) -> Settings | None:
    """This year's marker row for one customer's one occasion, if it exists."""
    year = datetime.now(timezone.utc).year
    month_day = customer.birth_month_day if occasion == "customer" else customer.child_birthday
    key = f"birthday_sms_{customer.id}_{year}_{occasion}_{month_day}"
    return db.query(Settings).filter(Settings.key == key).first()


@router.get("/birthdays", response_class=HTMLResponse)
async def admin_birthdays(request: Request, db: Session = Depends(get_db)):
    """Review the due birthdays before anything is queued.

    The dashboard's old one-click button queued every wish sight-unseen; this
    page is the same list tier-up has: who is due, whose birthday it is, how
    soon, and who already received this year's wish — nothing sends until the
    owner ticks and confirms.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms import get_sms_config
    from services.tier import get_customers_for_birthday_check, get_tier_config

    days_before = get_tier_config(db)["birthday_sms_days_before"]
    result = get_customers_for_birthday_check(db, days_before)
    pattern_ready = bool(get_sms_config(db)["birthday_pattern"])

    rows = []
    for customer, days_until, occasion in result["eligible"]:
        month_day = customer.birth_month_day if occasion == "customer" else customer.child_birthday
        year = None if occasion == "customer" else customer.child_birth_year
        rows.append({
            "customer": customer,
            "occasion": occasion,
            "occasion_label": BIRTHDAY_SUBJECT_LABELS.get(occasion, occasion),
            "celebrated": (customer.first_name or "مشتری") if occasion == "customer"
                          else (customer.child_name or "فرزند"),
            "date": birthday_display(month_day, year),
            "days_until": days_until,
            "days_label": ("امروز" if days_until == 1
                           else (f"{to_persian_digits(str(days_until - 1))} روز دیگر" if days_until > 1 else "")),
            "already_sent": _birthday_marker(db, customer, occasion) is not None,
        })
    rows.sort(key=lambda row: row["days_until"])

    return templates.TemplateResponse(request, "admin/birthdays.html", {
        "rows": rows,
        "blocked": result["blocked"],
        "days_before": days_before,
        "pattern_ready": pattern_ready,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/birthdays/send", response_class=HTMLResponse)
async def admin_birthdays_send(request: Request, customer_ids: list[int] = Form([]),
                               db: Session = Depends(get_db)):
    """Queue the wishes the owner ticked on the review page.

    Only listed, still-eligible customers are honoured — a stale form cannot
    message someone whose window has passed. The per-customer marker (one per
    occasion, per year) still guards a double-send, so a re-submit of the same
    selection reports skips instead of repeat wishes.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms import birthday_sms_vars, get_sms_config, queue_sms
    from services.tier import get_customers_for_birthday_check, get_tier_config

    pattern = get_sms_config(db)["birthday_pattern"]
    if not pattern:
        return RedirectResponse(url="/admin/birthdays?err=ابتدا متن پیامک تولد را در صفحه پیامک بنویسید و فعال کنید.",
                                status_code=303)

    wanted = {int(value) for value in customer_ids if str(value).strip().isdigit()}
    if not wanted:
        return RedirectResponse(url="/admin/birthdays?err=هیچ مشتری انتخاب نشده است.", status_code=303)

    days_before = get_tier_config(db)["birthday_sms_days_before"]
    eligible = {customer.id: (customer, occasion)
                for customer, _days, occasion in get_customers_for_birthday_check(db, days_before)["eligible"]}

    sent = 0
    skipped = 0
    rejected = 0
    for customer_id in wanted:
        pair = eligible.get(customer_id)
        if pair is None:                     # window passed or no longer eligible
            rejected += 1
            continue
        customer, occasion = pair
        if _birthday_marker(db, customer, occasion) is not None:
            skipped += 1
            continue

        job = await queue_sms(
            pattern,
            customer.phone,
            birthday_sms_vars(customer.first_name, customer.child_name, occasion),
            db,
            # The log can name the person and which kind of birthday it was.
            template_key="birthday",
            source="birthday",
            customer=customer,
        )
        if job is not None:
            year = datetime.now(timezone.utc).year
            month_day = customer.birth_month_day if occasion == "customer" else customer.child_birthday
            db.add(Settings(key=f"birthday_sms_{customer.id}_{year}_{occasion}_{month_day}", value="sent"))
            sent += 1
    db.commit()
    log_action(db, "birthday_sms", f"{sent} پیامک تولد ارسال شد ({skipped} تکراری، {rejected} خارج از پنجره)",
               request=request, target_type="customer")

    message = f"{sent} پیامک تولد در صف قرار گرفت."
    if skipped:
        message += f" {skipped} مورد قبلاً ارسال شده بود."
    if rejected:
        message += f" {rejected} مورد دیگر در پنجره تولد نیست و رد شد."
    return RedirectResponse(url=f"/admin/birthdays?msg={message}", status_code=303)


@router.post("/check-downgrades", response_class=HTMLResponse)
async def admin_check_downgrades(request: Request, db: Session = Depends(get_db)):
    """Manually trigger tier downgrade check."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    
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


TIER_UP_SMS_LIMIT = 10


@router.get("/tier-up", response_class=HTMLResponse)
async def admin_tier_up(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    customers = sorted(
        tier_up_candidates(db),
        key=lambda c: TIER_RANK[c.tier],
        reverse=True,
    )

    return templates.TemplateResponse(request, "admin/tier_up.html", {
        "customers": customers,
        "limit": TIER_UP_SMS_LIMIT,
        "sent_msg": request.query_params.get("sent"),
        "skipped_msg": request.query_params.get("skipped"),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/tier-up/send", response_class=HTMLResponse)
async def admin_tier_up_send(request: Request, customer_ids: list[int] = Form([]), db: Session = Depends(get_db)):
    """Send tier-up SMS to selected customers, capped so we never blast >10 at once."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms import get_sms_config

    sms_config = get_sms_config(db)
    gold_pattern = sms_config["tier_up_gold_pattern"]
    diamond_pattern = sms_config["tier_up_diamond_pattern"]
    if not gold_pattern or not diamond_pattern:
        return RedirectResponse(url="/admin/tier-up?skipped=no_pattern", status_code=303)

    sent = 0
    for customer_id in customer_ids[:TIER_UP_SMS_LIMIT]:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer or customer.tier == "silver":
            continue
        if tier_up_sent_rank(db, customer) >= TIER_RANK[customer.tier]:
            continue

        is_gold = customer.tier == "gold"
        pattern = gold_pattern if is_gold else diamond_pattern
        success = await queue_sms(
            pattern,
            customer.phone,
            {"var1": customer.first_name or "مشتری", "var2": str(customer.total_points)},
            db,
            template_key="tier_up_gold" if is_gold else "tier_up_diamond",
            source="tier_up",
            customer=customer,
        ) is not None
        if success:
            marker = db.query(Settings).filter(Settings.key == tier_up_marker_key(customer.id)).first()
            if marker:
                marker.value = customer.tier
            else:
                db.add(Settings(key=tier_up_marker_key(customer.id), value=customer.tier))
            sent += 1

    db.commit()
    log_action(db, "tier_up_sms", f"{sent} پیامک ارتقا ارسال شد", request=request, target_type="customer")

    skipped = max(0, len(customer_ids) - TIER_UP_SMS_LIMIT)
    return RedirectResponse(url=f"/admin/tier-up?sent={sent}&skipped={skipped}", status_code=303)


@router.get("/sales", response_class=HTMLResponse)
async def admin_sales_redirect(request: Request, db: Session = Depends(get_db)):
    """Redirect admin sales to sales list."""
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    return RedirectResponse(url="/sales/", status_code=303)
