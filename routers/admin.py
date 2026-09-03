from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from datetime import datetime, timezone
from models import (
    Customer, Referral, Settings, Sale, SaleItem, SaleCampaign, GeneratedImage, AdminLog, POSTransaction, StockMovement,
    BusinessEvent,
)
from config import ADMIN_PASSWORD, API_TOKEN
from deployment import OWNER_MODE
from services.sms import (
    get_balance,
    send_birthday_sms,
    send_tier_up_gold_sms,
    send_tier_up_diamond_sms,
)
from services._common import fmt, check_admin, get_setting_int as get_discount_setting, jalali_str
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
    page: int = 1,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

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
    
    per_page = 10
    total = query.count()
    customers = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)

    tier_config = get_tier_config(db)

    return templates.TemplateResponse(request, "admin/customers.html", {
        "customers": customers,
        "search": search,
        "sort": sort,
        "tier_filter": tier,
        "page": page,
        "total_pages": total_pages,
        "tier_config": tier_config,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/customers/{customer_id}/delete", response_class=HTMLResponse)
async def admin_delete_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        db.query(Referral).filter(
            (Referral.referrer_id == customer_id) | (Referral.referred_id == customer_id)
        ).delete()
        db.delete(customer)
        db.commit()
        log_action(db, "customer_delete", f"مشتری {customer.phone}", request=request, target_type="customer", target_id=customer.id, before={"phone": customer.phone})

    return RedirectResponse(url="/admin/customers", status_code=303)


@router.get("/staff", response_class=HTMLResponse)
async def admin_staff(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    from models import StaffUser
    return templates.TemplateResponse(request, "admin/staff.html", {
        "staff_users": db.query(StaffUser).order_by(StaffUser.created_at.desc()).all(),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/staff", response_class=HTMLResponse)
async def admin_staff_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("cashier"),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    from models import StaffUser
    username = username.strip().lower()
    if not username or len(password) < 6 or role not in {"cashier", "manager", "owner"}:
        return RedirectResponse(url="/admin/staff?err=اطلاعات کاربر نامعتبر است.", status_code=303)
    if db.query(StaffUser).filter(StaffUser.username == username).first():
        return RedirectResponse(url="/admin/staff?err=نام کاربری تکراری است.", status_code=303)
    user = StaffUser(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    log_action(db, "staff_create", f"ایجاد کاربر {username}", request=request, target_type="staff_user", target_id=user.id, after={"username": username, "role": role})
    return RedirectResponse(url="/admin/staff?msg=کاربر ایجاد شد.", status_code=303)


@router.post("/staff/{staff_id}/disable", response_class=HTMLResponse)
async def admin_staff_disable(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    from models import StaffUser
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
        "themes": all_theme_previews(db),
        "active_theme_id": get_theme_id(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


def get_theme_id(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
    return row.value if row and row.value in THEMES else DEFAULT_THEME_ID


@router.post("/settings", response_class=HTMLResponse)
async def admin_update_settings(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    theme_id = str(form.get(THEME_SETTING_KEY, DEFAULT_THEME_ID)).strip()
    primary = str(form.get(CUSTOM_PRIMARY_KEY, "#C94B68")).strip().upper()
    secondary = str(form.get(CUSTOM_SECONDARY_KEY, "#197A8C")).strip().upper()
    if theme_id not in THEMES:
        return RedirectResponse(url="/admin/settings?err=تم انتخاب نامعتبر است.", status_code=303)
    if theme_id == "custom-brand":
        if not validate_hex(primary) or not validate_hex(secondary):
            return RedirectResponse(url="/admin/settings?err=رنگ‌ها باید به صورت HEX شش‌رقمی باشند.", status_code=303)
        if contrast_ratio(primary, "#FFFFFF") < 4.5 and contrast_ratio(primary, "#000000") < 4.5:
            return RedirectResponse(url="/admin/settings?err=رنگ اصلی کنتراست کافی ندارد.", status_code=303)
        if contrast_ratio(secondary, "#FFFFFF") < 3 and contrast_ratio(secondary, "#000000") < 3:
            return RedirectResponse(url="/admin/settings?err=رنگ دوم کنتراست کافی ندارد.", status_code=303)
    updates = {THEME_SETTING_KEY: theme_id, CUSTOM_PRIMARY_KEY: primary, CUSTOM_SECONDARY_KEY: secondary}
    for key, value in form.items():
        if key in {"csrf_token", THEME_SETTING_KEY, CUSTOM_PRIMARY_KEY, CUSTOM_SECONDARY_KEY, "theme_logo"}:
            continue
        updates[key] = str(value)
    for key, value in updates.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    logo = form.get("theme_logo")
    if hasattr(logo, "filename") and hasattr(logo, "read") and logo.filename:
        if logo.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            return RedirectResponse(url="/admin/settings?err=فرمت لوگو باید PNG، JPG یا WEBP باشد.", status_code=303)
        content = await logo.read()
        if len(content) > 2_000_000:
            return RedirectResponse(url="/admin/settings?err=حجم لوگو نباید بیشتر از ۲ مگابایت باشد.", status_code=303)
        from PIL import Image
        from io import BytesIO
        try:
            image = Image.open(BytesIO(content))
            image.verify()
        except Exception:
            return RedirectResponse(url="/admin/settings?err=فایل لوگو معتبر نیست.", status_code=303)
        logo_dir = Path("static/uploads/branding")
        logo_dir.mkdir(parents=True, exist_ok=True)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[logo.content_type]
        logo_path = logo_dir / ("store-logo" + suffix)
        logo_path.write_bytes(content)
        logo_setting = db.query(Settings).filter(Settings.key == "store_logo_path").first()
        if logo_setting:
            logo_setting.value = "/static/uploads/branding/store-logo" + suffix
        else:
            db.add(Settings(key="store_logo_path", value="/static/uploads/branding/store-logo" + suffix))
    db.commit()
    invalidate_store_cache()
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


@router.post("/check-birthdays", response_class=HTMLResponse)
async def admin_check_birthdays(request: Request, db: Session = Depends(get_db)):
    """Manually send birthday SMS to customers 7 days (or less) before their child's birthday.

    Each sent customer gets a marker, so pressing the button again (even next day)
    won't re-send to the same customer for the same birthday."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    config = get_tier_config(db)
    days_before = config["birthday_sms_days_before"]
    eligible = get_customers_for_birthday_check(db, days_before)

    sent = 0
    skipped = 0
    for customer, days_until in eligible:
        log_key = f"birthday_sms_{customer.id}_{datetime.now(timezone.utc).year}_{customer.child_birthday}"
        if db.query(Settings).filter(Settings.key == log_key).first():
            skipped += 1
            continue

        from services.sms import queue_sms
        success = await queue_sms(config.get("birthday_pattern", ""), customer.phone, {"var1": customer.first_name or "مشتری", "var2": customer.child_name or "فرزند شما"}, db) is not None
        if success:
            db.add(Settings(key=log_key, value="sent"))
            sent += 1
    db.commit()

    return RedirectResponse(
        url=f"/admin?birthday_msg={sent}&birthday_skip={skipped}&birthday_eligible={len(eligible)}",
        status_code=303,
    )


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

    gold_pattern = db.query(Settings).filter(Settings.key == "sms_pattern_tier_up_gold").first()
    diamond_pattern = db.query(Settings).filter(Settings.key == "sms_pattern_tier_up_diamond").first()
    if not gold_pattern or not gold_pattern.value or not diamond_pattern or not diamond_pattern.value:
        return RedirectResponse(url="/admin/tier-up?skipped=no_pattern", status_code=303)

    sent = 0
    for customer_id in customer_ids[:TIER_UP_SMS_LIMIT]:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer or customer.tier == "silver":
            continue
        if tier_up_sent_rank(db, customer) >= TIER_RANK[customer.tier]:
            continue

        sms_fn = send_tier_up_gold_sms if customer.tier == "gold" else send_tier_up_diamond_sms
        pattern = gold_pattern.value if customer.tier == "gold" else diamond_pattern.value
        success = await queue_sms(pattern, customer.phone, {"var1": customer.first_name or "مشتری", "var2": str(customer.total_points)}, db) is not None
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
