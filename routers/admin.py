import re
import json
from pathlib import Path

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from database import get_db
from datetime import datetime, timedelta, timezone
from models import (
    Campaign, Customer, Referral, Settings, Sale, SaleItem, SaleCampaign, GeneratedImage, AdminLog, POSTransaction, StockMovement,
    BusinessEvent, StaffUser, SalaryPayment, CashSession, AttendanceRecord, JobPosition,
)
from config import ADMIN_PASSWORD, API_TOKEN
from deployment import OWNER_MODE
from services.sms import (
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
    format_bytes_fa,
    get_birthday_target,
    get_setting_int as get_discount_setting,
    get_setting_int,
    jalali_age,
    jalali_str,
    amount_to_words,
    page_arg,
    int_arg,
    parse_jalali_input,
    parse_jalali_input_end,
    rel_time,
    jalali_day_label,
    highlight,
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
    bulk_tag_customers,
    can_delete_customer,
    customer_overview,
    customer_profile,
    delete_customer,
    export_customers,
    invalidate_customer_cache,
    is_archived_customer,
    list_customers,
    marketing_opt_in,
    parse_tags,
    reconcile_customer_counters,
    serialize_tags,
    update_customer_meta,
)
from models import to_english_digits, to_persian_digits
from services.backup import (
    BACKUP_DIR, BACKUP_EVERY_DEFAULT, BACKUP_EVERY_OPTIONS, BACKUP_KEEP_DEFAULT,
    backup_download_path, backup_due, backups_storage, create_backup,
    delete_backup, download_all_bytes, latest_backup, list_backups,
    normalize_every_days, normalize_keep_count, recheck_backup, verify_cached,
)
from services.dashboard import dashboard_overview
from services.pos_reconciliation import unresolved_transactions
from services.pos_terminal import get_terminal_config

from services.navigation import home_for
from services.security import (
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
    verify_password,
    require_html_role,
    role_allows,
    effective_cap,
    require_cap,
    ROLE_DISCOUNT_LIMITS,
    ADMIN_ACTION_LABELS,
    admin_action_tone,
    admin_target_link,
    admin_log_diff,
    admin_log_diff_fields,
)
from services.store import invalidate_store_cache, get_store
from services.sorting import parse_sort
from services.templating import templates
from services.tier import (
    apply_tier_downgrades,
    downgrade_candidates,
    get_tier_config,
    get_customers_for_birthday_check,
    tier_downgrade_rule,
    tier_up_candidates,
    tier_up_marker_key,
    tier_up_sent_rank,
    TIER_LABELS,
    TIER_RANK,
)
from services.events import (
    event_payload, append_event,
    BUSINESS_EVENT_LABELS, business_event_tone,
)
from services.payroll import (create_salary_payment, current_period_key,
                             is_payroll_due, normalize_period_key,
                             run_monthly_payday, void_salary_payment)
from services.themes import THEMES, DEFAULT_THEME_ID, THEME_SETTING_KEY, CUSTOM_PRIMARY_KEY, CUSTOM_SECONDARY_KEY, DEFAULT_CUSTOM_PRIMARY, DEFAULT_CUSTOM_SECONDARY, all_theme_previews, validate_hex, contrast_ratio, get_theme, invalidate_theme_cache, migrate_retired_theme

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
        # First login of the day marks the person present: the row appears
        # only when the day is still unmarked, so repeat logins never
        # duplicate and a hand-marked leave is never overwritten. Presence
        # only — sessions outlive any work stretch, so hours would lie.
        today = datetime.now(timezone.utc).date()
        if not db.query(AttendanceRecord).filter(
                AttendanceRecord.staff_user_id == user.id,
                AttendanceRecord.day == today).first():
            db.add(AttendanceRecord(staff_user_id=user.id, day=today,
                                    status="present", recorded_by_user_id=None))
        db.commit()
        log_action(db, "login", "ورود موفق", request=request, target_type="staff_user", target_id=user.id)
        # The till for a cashier, the dashboard for everyone above them. Sending
        # everybody to /admin meant a cashier's first page was a refusal.
        return RedirectResponse(url=home_for(user.role), status_code=303)
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
    """The shop at a glance, built for the role that is looking at it.

    The route decides nothing about who sees what: it hands the viewer's role to
    :func:`services.dashboard.dashboard_overview`, which returns only the cards
    that role may see and never computes the others. That is what keeps the
    template free of role logic — it prints what it is given.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    view = dashboard_overview(db, role=guard.role)
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        **view,
        # The print heading names the day; a staple carries no URL.
        "today": jalali_str(datetime.now(), with_time=False),
        "jalali_str": jalali_str,
        # Nothing redirects here with a result any more: the only action the
        # dashboard used to carry was the downgrade sweep, and it now reports
        # back on its own page.
    })


@router.get("/customers", response_class=HTMLResponse)
async def admin_customers(
    request: Request,
    search: str = "",
    sort: str = "date",
    tier: str = "",
    status: str = "all",
    tag: str = "",
    page: str = "1",
    per_page: str = "25",
    drifted: str = "0",
    db: Session = Depends(get_db),
):
    """The customer list: who they are, what they bought, and what is due."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    page = page_arg(page)
    per_page_int = int(per_page) if str(per_page).isdigit() and int(per_page) in (10, 25, 50) else 25
    drifted_on = bool(int_arg(drifted, default=0))
    listing = list_customers(
        db, search=search, tier=tier, status=status, tag=tag, sort=sort, page=page,
        per_page=per_page_int, drifted=drifted_on,
    )
    # Query strings are urlencoded once in the route (the purchases page's
    # rule): the template only drops this inside quoted hrefs.
    from urllib.parse import urlencode
    sort_base_qs = "&" + urlencode({
        "tier": listing["tier"],
        "status": listing["status"],
        "tag": listing["tag"],
        "search": listing["search"],
        "per_page": listing["per_page"],
        **({"drifted": "1"} if drifted_on else {}),
    })
    filter_qs = f"{sort_base_qs}&sort={listing['sort']}"

    return templates.TemplateResponse(request, "admin/customers.html", {
        **listing,
        "filter_qs": filter_qs,
        "export_qs": sort_base_qs.lstrip("&"),
        "per_page_options": (10, 25, 50),
        "tier_labels": TIER_LABELS,
        "tags_in_use": db.query(Customer.id).filter(
            Customer.tags.isnot(None), Customer.tags != "").first() is not None,
        "rows": build_customer_rows(db, listing["customers"]),
        "overview": customer_overview(db),
        "active_days": ACTIVE_DAYS,
        "inactive_days": INACTIVE_DAYS,
        "status_labels": STATUS_LABELS,
        "sort_labels": SORT_LABELS,
        "tag_palette": TAG_PALETTE,
        "tag_labels": TAG_LABELS,
        "is_owner": request.session.get("staff_role") == "owner",
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "tier_config": get_tier_config(db),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/customers/export", response_class=HTMLResponse)
async def admin_customers_export(
    request: Request,
    search: str = "",
    sort: str = "date",
    tier: str = "",
    status: str = "all",
    tag: str = "",
    drifted: str = "0",
    db: Session = Depends(get_db),
):
    """The filtered list as a file: the same query the page reads, unsliced."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    import csv
    import io
    customers = export_customers(
        db, search=search, tier=tier, status=status, tag=tag, sort=sort,
        drifted=bool(int_arg(drifted, default=0)),
    )
    rows = build_customer_rows(db, customers)
    out = [["تلفن", "نام", "سطح", "مجموع خرید", "تعداد خرید", "امتیاز",
            "بدهی", "آخرین خرید", "برچسب‌ها"]]
    for row in rows:
        customer = row["customer"]
        out.append([
            customer.phone,
            customer.full_name or "",
            TIER_LABELS.get(customer.tier, customer.tier or ""),
            row["invoice_spent"] if row["invoice_spent"] is not None else "",
            row["invoice_count"] if row["invoice_count"] is not None else "",
            row["invoice_points"] if row["invoice_points"] is not None else "",
            customer.total_debt or 0,
            jalali_str(customer.last_purchase_date, False) if customer.last_purchase_date else "",
            "، ".join(TAG_LABELS.get(key, key) for key in row["tags"]),
        ])
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(out)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="customers_{today}.csv"'},
    )


@router.post("/customers/bulk-tag", response_class=HTMLResponse)
async def admin_customers_bulk_tag(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    ids = [int(raw) for raw in form.getlist("ids") if str(raw).isdigit()]
    tag = str(form.get("tag", "") or "")
    remove = str(form.get("mode", "") or "") == "remove"
    try:
        changed = bulk_tag_customers(db, ids, tag, remove=remove) if ids else 0
    except ValueError:
        return RedirectResponse(
            url="/admin/customers?err=برچسب نامعتبر است.", status_code=303)
    if changed:
        db.commit()
        verb = "برداشته شد" if remove else "افزوده شد"
        message = f"برچسب {TAG_LABELS.get(tag, tag)} برای {changed} مشتری {verb}."
    else:
        message = "مشتری‌ای برای برچسب‌زدن انتخاب نشده بود."
    log_action(db, "customer_bulk_tag", message, request=request, after={"count": changed, "tag": tag})
    return RedirectResponse(
        url="/admin/customers?msg=" + quote_plus(message), status_code=303)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
async def admin_customer_profile(
    customer_id: int, request: Request, page: str = "1", db: Session = Depends(get_db)
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

    profile = customer_profile(db, customer, history_page=page_arg(page))
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
        "is_owner": request.session.get("staff_role") == "owner",
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
        "tier_labels": TIER_LABELS,
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


@router.post("/customers/{customer_id}/identity", response_class=HTMLResponse)
async def admin_customer_identity(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """Correct the name and phone on the file, audited with before/after.

    Phone obeys the signup rule (09 + 11 digits, Farsi digits accepted) and a
    number that already belongs to another file is refused — two files never
    share a phone, so the refusal names the fact instead of merging.
    """
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")
    form = await request.form()
    phone = to_english_digits(str(form.get("phone", "") or "").strip())
    if not phone.startswith("09") or len(phone) != 11:
        return RedirectResponse(
            url=f"/admin/customers/{customer.id}?err=شماره موبایل نامعتبر است. فرمت صحیح: 09xxxxxxxxx",
            status_code=303)
    clash = db.query(Customer).filter(
        Customer.phone == phone, Customer.id != customer.id).first()
    if clash:
        return RedirectResponse(
            url=f"/admin/customers/{customer.id}?err=این شماره برای مشتری دیگری ثبت است.",
            status_code=303)
    before = {"first_name": customer.first_name, "last_name": customer.last_name,
              "phone": customer.phone}
    customer.first_name = str(form.get("first_name", "") or "").strip() or None
    customer.last_name = str(form.get("last_name", "") or "").strip() or None
    customer.phone = phone
    db.commit()
    log_action(
        db, "customer_identity", f"اصلاح مشخصات {phone}", request=request,
        target_type="customer", target_id=customer.id, before=before,
        after={"first_name": customer.first_name, "last_name": customer.last_name,
               "phone": customer.phone},
    )
    return RedirectResponse(
        url=f"/admin/customers/{customer.id}?msg=مشخصات مشتری به‌روز شد.", status_code=303)


@router.post("/customers/{customer_id}/sms", response_class=HTMLResponse)
async def admin_customer_sms(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """Flip the marketing-SMS consent from the facts card, one tap."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")
    form = await request.form()
    customer.sms_opt_in = str(form.get("value", "") or "") == "1"
    db.commit()
    log_action(
        db, "customer_sms_opt", f"پیامک تبلیغاتی {customer.phone}: "
        f"{'فعال' if customer.sms_opt_in else 'انصراف'}",
        request=request, target_type="customer", target_id=customer.id,
        after={"sms_opt_in": customer.sms_opt_in},
    )
    return RedirectResponse(
        url=f"/admin/customers/{customer.id}?msg=وضعیت پیامک ثبت شد.", status_code=303)


@router.post("/customers/{customer_id}/reconcile-request", response_class=HTMLResponse)
async def admin_customer_reconcile_request(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """A non-owner's ask for a reconcile: the run stays owner-only, but the
    ask lands in the audit trail where the owner reads it."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")
    log_action(
        db, "customer_reconcile_request", f"درخواست هم‌سازی حساب‌ها: {customer.phone}",
        request=request, target_type="customer", target_id=customer.id,
    )
    return RedirectResponse(
        url=f"/admin/customers/{customer.id}?msg=درخواست هم‌سازی برای مالک ثبت شد.",
        status_code=303)


@router.post("/customers/{customer_id}/reconcile", response_class=HTMLResponse)
async def admin_customer_reconcile(
    customer_id: int, request: Request, db: Session = Depends(get_db)
):
    """Set the stored counters to what the invoices add up to.

    The drift banner on the page says the counters disagree; this is where the
    owner says «trust the invoices». The before/after of both counters goes to
    the audit trail, so a reconcile that moved a figure can always be traced.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    before_spent, after_spent, before_count, after_count = reconcile_customer_counters(db, customer)
    log_action(
        db, "customer_reconcile", f"هم‌سازی شمارنده‌ها با فاکتورها: {customer.phone}",
        request=request, target_type="customer", target_id=customer.id,
        before={"total_spent": before_spent, "total_purchases": before_count},
        after={"total_spent": after_spent, "total_purchases": after_count},
    )
    if (before_spent, before_count) == (after_spent, after_count):
        message = "شمارنده‌ها هم‌ساز بودند؛ چیزی تغییر نکرد."
    else:
        message = "شمارنده‌ها با فاکتورها هم‌ساز شد."
    return RedirectResponse(url=f"/admin/customers/{customer.id}?msg={message}", status_code=303)


@router.post("/customers/reconcile-all", response_class=HTMLResponse)
async def admin_reconcile_all_counters(
    request: Request, db: Session = Depends(get_db)
):
    """Trust the invoices everywhere, at once.

    The drift card counts the customers whose stored counters disagree with
    their invoices, and the digest reports the same count monthly; this is the
    owner's one-action answer. Owner-only like the per-profile reconcile, and
    the audit row says both numbers — the drift that was found, and the rows
    whose figures actually moved — so a no-op run is traceable too.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.customers import reconcile_all_counters
    result = reconcile_all_counters(db)
    log_action(
        db, "customer_reconcile_all",
        f"هم‌سازی یک‌جای شمارنده‌ها با فاکتورها: {result['found']} ناهم‌خوان، "
        f"{result['reconciled']} مورد اصلاح شد.",
        request=request, target_type="customer",
        before={"drifted": result["found"]},
        after={"reconciled": result["reconciled"]},
    )
    if result["found"] == 0:
        message = "هیچ شمارنده‌ی ناهم‌خوانی نبود؛ چیزی تغییر نکرد."
    elif result["reconciled"] == 0:
        message = "ناهم‌خوانی پیدا شد ولی چیزی برای اصلاح نبود."
    else:
        message = (f"شمارنده‌های {to_persian_digits(result['reconciled'])} مشتری "
                   "با فاکتورها هم‌ساز شد.")
    return RedirectResponse(url=f"/admin/customers?msg={quote_plus(message)}", status_code=303)


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


# Contract terms in months. Empty means open-ended (or a legacy free date the
# owner typed before terms existed) — the end date then stays hand-written.
CONTRACT_TERMS = {"1": "۱ ماهه", "3": "۳ ماهه", "6": "۶ ماهه", "12": "۱۲ ماهه"}


def _add_jalali_months(base, months: int):
    """hire_date + term, in the calendar the shop reads. Day clamps to the
    target month (a 31st landing in a 29-day Esfand becomes the 29th)."""
    import jdatetime
    jd = jdatetime.date.fromgregorian(date=base.date())
    total = (jd.month - 1) + months
    year, month = jd.year + total // 12, total % 12 + 1
    day = jd.day
    while day > 28:
        try:
            jdatetime.date(year, month, day)
            break
        except ValueError:
            day -= 1
    gregorian = jdatetime.date(year, month, day).togregorian()
    return datetime(gregorian.year, gregorian.month, gregorian.day,
                    tzinfo=timezone.utc)


def _contract_days_left(user, today=None):
    """Whole days until the contract ends, negative when past. None when the
    contract is open-ended — nothing to count down to."""
    if not user.contract_end_date:
        return None
    if today is None:
        today = datetime.now(timezone.utc).date()
    return (user.contract_end_date.date() - today).days


def _contract_flag(days):
    if days is None:
        return ""
    if days < 0:
        return "پایان‌یافته"
    if days <= 7:
        return "رو به پایان"
    return ""


# Annual leave allowance in days, per Iranian labour law (one month). Sick
# leave is tracked without an allowance — counted, never capped.
ANNUAL_LEAVE_ALLOWANCE = 26

ATTENDANCE_STATUSES = {
    "present": "حاضر",
    "absent": "غایب",
    "annual_leave": "مرخصی استحقاقی",
    "sick_leave": "مرخصی استعلاجی",
}


def _jalali_month_bounds(period_key: str):
    """Gregorian UTC bounds of a Jalali YYYY-MM month, for range queries."""
    import jdatetime
    year, month = int(period_key[:4]), int(period_key[5:7])
    start = jdatetime.date(year, month, 1).togregorian()
    if month == 12:
        end = jdatetime.date(year + 1, 1, 1).togregorian()
    else:
        end = jdatetime.date(year, month + 1, 1).togregorian()
    return (datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            datetime(end.year, end.month, end.day, tzinfo=timezone.utc))


def _jalali_year_bounds(jyear: int):
    import jdatetime
    start = jdatetime.date(jyear, 1, 1).togregorian()
    end = jdatetime.date(jyear + 1, 1, 1).togregorian()
    return (datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            datetime(end.year, end.month, end.day, tzinfo=timezone.utc))


def _closed_periods(current: str, count: int) -> list:
    """The ``count`` Jalali months before ``current``, newest first — the
    pickable salary periods. The current (open) month is never payable."""
    year, month = int(current[:4]), int(current[5:7])
    periods = []
    for _ in range(count):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        periods.append(f"{year:04d}-{month:02d}")
    return periods


def _staff_amount(value: str, default: int = 0, field: str = "مبلغ") -> int:
    text = to_english_digits((value or "").replace(",", "").strip())
    if not text:
        return default
    try:
        return int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{field} معتبر نیست.")


def _staff_limit(form, field: str, label: str):
    """An optional per-person ceiling: blank means NULL (follow the role
    default), otherwise a non-negative integer."""
    text = to_english_digits(str(form.get(field, "") or "").replace(",", "").strip())
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label} معتبر نیست.")
    if value < 0:
        raise ValueError(f"{label} نمی‌تواند منفی باشد.")
    return value


def _check_national_id(code: str) -> str | None:
    """Validate an Iranian national ID (کد ملی), checksum included. Empty stays
    empty — the field is optional; a filled one must be real. Spacing and
    dashes people copy along (``001 234 5678``) are stripped, not punished."""
    import logging
    import re
    raw = to_english_digits((code or "").strip())
    text = re.sub(r"[\s\u200c\-/]", "", raw)
    if not text:
        return None
    if not (text.isdigit() and len(text) == 10) or len(set(text)) == 1:
        logging.getLogger("raykid.staff").warning(
            "national-id refused by shape: length=%d all_digits=%s",
            len(text), text.isdigit())
        raise ValueError("کد ملی باید ۱۰ رقم باشد.")
    check = int(text[9])
    remainder = sum(int(digit) * (10 - index) for index, digit in enumerate(text[:9])) % 11
    if (remainder if remainder < 2 else 11 - remainder) != check:
        logging.getLogger("raykid.staff").warning(
            "national-id refused by checksum: length=10 expected_check=%d",
            remainder if remainder < 2 else 11 - remainder)
        raise ValueError("کد ملی معتبر نیست.")
    return text


def _check_mobile(phone: str) -> str | None:
    """Mobile numbers route SMS and password handovers; landlines and
    fragments silently break both. Empty stays empty."""
    import re
    text = to_english_digits((phone or "").strip()).replace(" ", "").replace("-", "")
    if not text:
        return None
    if not re.fullmatch(r"09\d{9}", text):
        raise ValueError("تلفن همراه باید با ۰۹ شروع شود و ۱۱ رقم باشد.")
    return text


def _check_iban(iban: str) -> str | None:
    """Validate a Sheba (IR-IBAN): salary lands here, so a typo'd account
    must refuse at typing time, not at payday. Empty stays empty."""
    import re
    text = (iban or "").strip().replace(" ", "").upper()
    if not text:
        return None
    if not re.fullmatch(r"IR\d{24}", text):
        raise ValueError("شبا باید با IR شروع شود و ۲۴ رقم بعد از آن داشته باشد.")
    rearranged = text[4:] + text[:4]
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    if int(numeric) % 97 != 1:
        raise ValueError("شبا معتبر نیست.")
    return text


# The staff forms' numeric fields, with the bound the server enforces and the
# label it refuses by. One table, two readers: the POST validates against it
# (_staff_profile_data for the two salary fields, the payroll service for
# deductions) and the forms render their min/max from it.
STAFF_NUMERIC_RULES = {
    "salary_amount": (0, None, "حقوق ماهانه"),
    "salary_payment_day": (1, 31, "روز پرداخت حقوق"),
    "deductions": (0, None, "کسورات"),
}


def _staff_profile_data(form, db=None):
    employment_type = str(form.get("employment_type", "full_time")).strip()
    if employment_type not in {"full_time", "part_time", "contractor"}:
        raise ValueError("نوع همکاری نامعتبر است.")
    salary_amount = _staff_amount(str(form.get("salary_amount", "0")), field="حقوق ماهانه")
    if salary_amount < STAFF_NUMERIC_RULES["salary_amount"][0]:
        raise ValueError("حقوق ماهانه نمی‌تواند منفی باشد.")
    hire_date = _parse_staff_date(str(form.get("hire_date", "")))
    contract_term = to_english_digits(str(form.get("contract_term_months", "") or "").strip())
    if contract_term and contract_term not in CONTRACT_TERMS:
        raise ValueError("مدت قرارداد نامعتبر است.")
    if contract_term and not hire_date:
        raise ValueError("برای قرارداد مدت‌دار، تاریخ شروع الزامی است.")
    # A set term governs the end date: derived from start + term, never
    # hand-typed beside it. Open terms keep the legacy free date.
    contract_end_date = (_add_jalali_months(hire_date, int(contract_term))
                         if contract_term else
                         _parse_staff_date(str(form.get("contract_end_date", ""))))
    gender = str(form.get("gender", "") or "").strip()
    if gender and gender not in {"male", "female"}:
        raise ValueError("جنسیت نامعتبر است.")
    position_id = None
    position_raw = to_english_digits(str(form.get("position_id", "") or "").strip())
    if position_raw:
        try:
            position_id = int(position_raw)
        except (TypeError, ValueError):
            raise ValueError("سمت سازمانی نامعتبر است.")
        if db is not None and not db.query(JobPosition).filter(
                JobPosition.id == position_id).first():
            raise ValueError("سمت سازمانی نامعتبر است.")
    salary_day_value = to_english_digits(str(form.get("salary_payment_day", "")).strip())
    salary_day = None
    if salary_day_value:
        try:
            salary_day = int(salary_day_value)
        except ValueError:
            raise ValueError("روز پرداخت حقوق نامعتبر است.")
        if not STAFF_NUMERIC_RULES["salary_payment_day"][0] <= salary_day <= STAFF_NUMERIC_RULES["salary_payment_day"][1]:
            raise ValueError("روز پرداخت حقوق باید بین ۱ تا ۳۱ باشد.")
    return {
        "full_name": str(form.get("full_name", "")).strip()[:200] or None,
        "employee_code": str(form.get("employee_code", "")).strip()[:50] or None,
        "national_id": _check_national_id(str(form.get("national_id", ""))[:30]),
        "phone": _check_mobile(str(form.get("phone", ""))[:30]),
        "email": str(form.get("email", "")).strip()[:150] or None,
        "job_title": str(form.get("job_title", "")).strip()[:100] or None,
        "position_id": position_id,
        "gender": gender or None,
        "insured": str(form.get("insured", "") or "") == "1",
        "employment_type": employment_type,
        "hire_date": hire_date,
        "birth_date": _parse_staff_date(str(form.get("birth_date", ""))),
        "contract_term_months": int(contract_term) if contract_term else None,
        "contract_end_date": contract_end_date,
        "education": str(form.get("education", "")).strip()[:200] or None,
        "work_schedule": str(form.get("work_schedule", "")).strip()[:200] or None,
        "salary_payment_day": salary_day,
        "address": str(form.get("address", "")).strip()[:2000] or None,
        "emergency_contact": str(form.get("emergency_contact", "")).strip()[:200] or None,
        "emergency_name": str(form.get("emergency_name", "")).strip()[:100] or None,
        "emergency_relation": str(form.get("emergency_relation", "")).strip()[:50] or None,
        "emergency_phone": _check_mobile(str(form.get("emergency_phone", ""))[:30]),
        "bank_account": str(form.get("bank_account", "")).strip()[:80] or None,
        "iban": _check_iban(str(form.get("iban", ""))[:40]),
        "salary_amount": salary_amount,
        "notes": str(form.get("notes", "")).strip()[:4000] or None,
    }


# The staff list's sort keys, with each key's own default direction. Names
# ascend, money descends — the same rule the ledger lists follow.
STAFF_SORTS = {"newest": "desc", "name": "asc", "salary": "desc"}


@router.get("/staff", response_class=HTMLResponse)
async def admin_staff(request: Request, q: str = "", status: str = "all", page: str = "1",
                      per_page: str = "10", sort: str = "newest", dir: str = "desc",
                      db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    from urllib.parse import urlencode
    search = (q or "").strip()
    status_filter = status if status in {"active", "inactive"} else "all"
    page_int = page_arg(page)
    per_page_int = int(per_page) if str(per_page).isdigit() and int(per_page) in (10, 25, 50) else 10
    sort_key, sort_dir = parse_sort(request.query_params, STAFF_SORTS, "newest")
    query = db.query(StaffUser)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            StaffUser.full_name.ilike(like),
            StaffUser.username.ilike(like),
            StaffUser.employee_code.ilike(like),
        ))
    if status_filter == "active":
        query = query.filter(StaffUser.is_active == True)  # noqa: E712
    elif status_filter == "inactive":
        query = query.filter(StaffUser.is_active == False)  # noqa: E712
    if sort_key == "name":
        query = query.order_by(
            func.coalesce(StaffUser.full_name, StaffUser.username).asc()
            if sort_dir == "asc" else
            func.coalesce(StaffUser.full_name, StaffUser.username).desc())
    elif sort_key == "salary":
        query = query.order_by(
            StaffUser.salary_amount.desc() if sort_dir == "desc" else StaffUser.salary_amount.asc())
    else:
        query = query.order_by(StaffUser.created_at.desc())
    query = query.order_by(StaffUser.id.desc()) if sort_key != "newest" else query
    total_count = query.count()
    total_pages = max(1, -(-total_count // per_page_int))
    page_int = min(page_int, total_pages)
    staff_users = query.offset((page_int - 1) * per_page_int).limit(per_page_int).all()
    today = datetime.now(timezone.utc).date()
    contract_flags = {person.id: _contract_flag(_contract_days_left(person, today))
                      for person in staff_users}
    current = current_period_key()
    unpaid_staff = db.query(StaffUser).filter(
        StaffUser.is_active == True,  # noqa: E712
        StaffUser.salary_amount > 0).order_by(StaffUser.id).all()
    paid_ids = {row[0] for row in db.query(SalaryPayment.staff_user_id).filter(
        SalaryPayment.period_key == current,
        SalaryPayment.is_voided == False).all()}  # noqa: E712
    unpaid_names = [(person.full_name or person.username)
                    for person in unpaid_staff
                    if person.id not in paid_ids
                    and is_payroll_due(person.hire_date, current)]
    base_qs = urlencode({
        **({"q": search} if search else {}),
        **({"status": status_filter} if status_filter != "all" else {}),
        "per_page": per_page_int,
    })
    return templates.TemplateResponse(request, "admin/staff.html", {
        "staff_users": staff_users,
        "owner_settings": {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()},
        "search": search,
        "status_filter": status_filter,
        "has_filters": bool(search or status_filter != "all" or sort_key != "newest"
                            or sort_dir != STAFF_SORTS[sort_key] or per_page_int != 10),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
        "page": page_int,
        "per_page": per_page_int,
        "per_page_options": (10, 25, 50),
        "total_count": total_count,
        "total_pages": total_pages,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "base_qs": base_qs,
        # Payday nudges: active, salaried staff without a live payment for the
        # current month. The banner names a few and counts the rest.
        "current_period": current,
        "unpaid_names": unpaid_names,
        "contract_flags": contract_flags,
        # The staff forms paint their bounds from the same table the POSTs
        # validate against.
        "numeric_rules": STAFF_NUMERIC_RULES,
    })


@router.get("/staff/export")
async def admin_staff_export(request: Request, db: Session = Depends(get_db)):
    """The staff directory as a file: identity, role, pay and state."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    import csv
    import io
    role_labels = {"cashier": "صندوقدار", "manager": "مدیر", "owner": "مالک"}
    employment_labels = {"full_time": "تمام‌وقت", "part_time": "پاره‌وقت", "contractor": "قراردادی"}
    out = [["نام", "نام کاربری", "کد پرسنلی", "کد ملی", "تلفن", "عنوان شغلی",
            "نوع همکاری", "نقش", "حقوق ماهانه", "وضعیت", "تاریخ شروع"]]
    for user in db.query(StaffUser).order_by(StaffUser.id).all():
        out.append([
            user.full_name or "", user.username, user.employee_code or "",
            user.national_id or "", user.phone or "", user.job_title or "",
            employment_labels.get(user.employment_type, user.employment_type or ""),
            role_labels.get(user.role, user.role or ""), user.salary_amount or 0,
            "فعال" if user.is_active else "غیرفعال",
            jalali_str(user.hire_date, False) if user.hire_date else "",
        ])
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(out)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="staff_{today}.csv"'},
    )


@router.get("/staff/new", response_class=HTMLResponse)
async def admin_staff_new(request: Request, db: Session = Depends(get_db)):
    """The full hire form on its own page — the list stays a list."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    return templates.TemplateResponse(request, "admin/staff_new.html", {
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "contract_terms": CONTRACT_TERMS,
        "positions": db.query(JobPosition).filter(JobPosition.is_active == True).order_by(JobPosition.id).all(),  # noqa: E712
        # The staff forms paint their bounds from the same table the POSTs
        # validate against.
        "numeric_rules": STAFF_NUMERIC_RULES,
    })


@router.get("/staff/{staff_id}", response_class=HTMLResponse)
async def admin_staff_profile(staff_id: int, request: Request, tab: str = "overview",
                              db: Session = Depends(get_db)):
    """One person's whole file: header, tabbed edit/permissions/payroll."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    staff_user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="کارمند یافت نشد")
    active_tab = tab if tab in {"overview", "permissions", "payroll", "attendance", "history"} else "overview"
    # Deactivation impact, computed while the switch is still flippable: an
    # open drawer in this person's name and an unpaid current month are the
    # two things closing access would strand.
    current = current_period_key()
    open_shift = db.query(CashSession).filter(
        CashSession.cashier_user_id == staff_user.id,
        CashSession.status == "open").order_by(CashSession.opened_at.desc()).first()
    unpaid_month = staff_user.is_active and staff_user.salary_amount > 0 \
        and is_payroll_due(staff_user.hire_date, current) and not db.query(
        SalaryPayment).filter(SalaryPayment.staff_user_id == staff_user.id,
                              SalaryPayment.period_key == current).first()
    # Yearly rollups from live payments: the year is the period prefix.
    year_totals: dict[str, dict] = {}
    for payment in staff_user.salary_payments:
        if payment.is_voided:
            continue
        year = (payment.period_key or "")[:4]
        bucket = year_totals.setdefault(
            year, {"gross": 0, "deductions": 0, "net": 0, "count": 0})
        bucket["gross"] += payment.gross_amount or 0
        bucket["deductions"] += payment.deductions or 0
        bucket["net"] += payment.net_amount or 0
        bucket["count"] += 1
    # Attendance month on display (query ?month=YYYY-MM, default current) with
    # leave balances derived from marked days of its Jalali year.
    from services.payroll import normalize_period_key as _normalize_month
    try:
        attendance_month = _normalize_month(
            str(request.query_params.get("month", "") or current))
    except ValueError:
        attendance_month = current
    month_start, month_end = _jalali_month_bounds(attendance_month)
    marks = db.query(AttendanceRecord).filter(
        AttendanceRecord.staff_user_id == staff_user.id,
        AttendanceRecord.day >= month_start.date(),
        AttendanceRecord.day < month_end.date()).order_by(
        AttendanceRecord.day).all()
    attendance_rows = [{
        "day": jalali_str(datetime(mark.day.year, mark.day.month, mark.day.day,
                                   tzinfo=timezone.utc), False),
        "status": ATTENDANCE_STATUSES.get(mark.status, mark.status),
        "note": mark.note or "—",
        "source": "خودکار" if mark.recorded_by_user_id is None else "دستی",
    } for mark in marks]
    year_start, year_end = _jalali_year_bounds(int(attendance_month[:4]))
    leave_used = {"annual_leave": 0, "sick_leave": 0}
    for (status,) in db.query(AttendanceRecord.status).filter(
            AttendanceRecord.staff_user_id == staff_user.id,
            AttendanceRecord.day >= year_start.date(),
            AttendanceRecord.day < year_end.date(),
            AttendanceRecord.status.in_(("annual_leave", "sick_leave"))).all():
        leave_used[status] = leave_used.get(status, 0) + 1
    # This month's sales performance, attributed by shift like analytics.
    from services.analytics import get_staff_performance
    performance = {"invoices": 0, "revenue": 0}
    try:
        staff_name = staff_user.full_name or staff_user.username
        for row in get_staff_performance(db, month_start, month_end).get("rows", []):
            if row.get("name") == staff_name:
                performance = {"invoices": row.get("invoices", 0),
                               "revenue": row.get("revenue", 0)}
                break
    except Exception:  # noqa: BLE001 — performance is garnish, never a blocker
        pass
    # The person's paper trail: every admin-log entry aimed at them, with an
    # optional contract-and-pay lens. Unknown kinds degrade to the full feed.
    history_kind = str(request.query_params.get("kind", "") or "").strip()
    timeline_query = db.query(AdminLog).filter(
        AdminLog.target_type == "staff_user",
        AdminLog.target_id == staff_user.id)
    if history_kind == "contract":
        timeline_query = timeline_query.filter(AdminLog.action.in_(
            ("staff_update", "staff_caps", "salary_void", "attendance_mark")))
        # Salary payments aim at the payment row, not the person — pull this
        # person's own pay events in and merge chronologically.
        staff_payment_ids = [row[0] for row in db.query(SalaryPayment.id).filter(
            SalaryPayment.staff_user_id == staff_user.id).all()]
        pay_events = db.query(AdminLog).filter(
            AdminLog.target_type == "salary_payment",
            AdminLog.target_id.in_(staff_payment_ids)).order_by(
            AdminLog.id.desc()).limit(30).all() if staff_payment_ids else []
        timeline = sorted(list(timeline_query.order_by(
            AdminLog.id.desc()).limit(30).all()) + pay_events,
            key=lambda entry: entry.id, reverse=True)[:30]
    else:
        history_kind = "all"
        timeline = timeline_query.order_by(AdminLog.id.desc()).limit(30).all()
    return templates.TemplateResponse(request, "admin/staff_profile.html", {
        "staff_user": staff_user,
        "active_tab": active_tab,
        "is_self": staff_user.id == guard.id,
        # What each switch currently answers, after role defaults and the
        # owner bypass — the toggles paint from the same answer enforcement reads.
        "staff_caps": {field: effective_cap(staff_user, field) for field in CAP_FIELDS},
        # The ceilings the limit inputs narrow: one source, painted and enforced.
        "role_discount_limits": ROLE_DISCOUNT_LIMITS.get(staff_user.role or "", {}),
        "positions": db.query(JobPosition).filter(JobPosition.is_active == True).order_by(JobPosition.id).all(),  # noqa: E712
        "open_shift": open_shift,
        "unpaid_month": current if unpaid_month else "",
        "year_totals": dict(sorted(year_totals.items(), reverse=True)),
        "contract_days_left": _contract_days_left(staff_user),
        "contract_flag": _contract_flag(_contract_days_left(staff_user)),
        "contract_terms": CONTRACT_TERMS,
        "attendance_statuses": ATTENDANCE_STATUSES,
        "attendance_month": attendance_month,
        "attendance_marks": attendance_rows,
        "annual_allowance": ANNUAL_LEAVE_ALLOWANCE,
        "annual_used": leave_used["annual_leave"],
        "sick_used": leave_used["sick_leave"],
        "performance": performance,
        "timeline": timeline,
        "history_kind": history_kind,
        "action_labels": ADMIN_ACTION_LABELS,
        "today_jalali": jalali_str(datetime.now(timezone.utc), False),
        # The last closed months, newest first: pickable salary periods so
        # the month format never has to be recalled from memory.
        "closed_months": _closed_periods(current, 6),
        "owner_settings": {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()},
        "current_period": current_period_key(),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
        # The staff forms paint their bounds from the same table the POSTs
        # validate against.
        "numeric_rules": STAFF_NUMERIC_RULES,
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
        return RedirectResponse(url="/admin/staff/new?err=اطلاعات ورود کاربر نامعتبر است.", status_code=303)
    if db.query(StaffUser).filter(StaffUser.username == username).first():
        return RedirectResponse(url="/admin/staff/new?err=نام کاربری تکراری است.", status_code=303)
    try:
        profile = _staff_profile_data(form, db)
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff/new?err={error}", status_code=303)
    user = StaffUser(username=username, password_hash=hash_password(password), role=role, **profile)
    db.add(user)
    db.commit()
    log_action(db, "staff_create", f"ایجاد کاربر {username}", request=request, target_type="staff_user", target_id=user.id, after={"username": username, "role": role, "full_name": user.full_name, "salary_amount": user.salary_amount})
    return RedirectResponse(url=f"/admin/staff/{user.id}?msg=کاربر و اطلاعات پرسنلی ثبت شد.", status_code=303)


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
        return RedirectResponse(url=f"/admin/staff/{user.id}?tab=permissions&err=نقش کاربر نامعتبر است.", status_code=303)
    if user.role == "owner" and role != "owner":
        if user.id == guard.id:
            return RedirectResponse(url=f"/admin/staff/{user.id}?tab=permissions&err=نمی‌توانید نقش خودتان را از مالک تغییر دهید.", status_code=303)
        remaining_owners = db.query(StaffUser).filter(
            StaffUser.role == "owner", StaffUser.is_active == True,  # noqa: E712
            StaffUser.id != user.id).count()
        if remaining_owners == 0:
            return RedirectResponse(url=f"/admin/staff/{user.id}?tab=permissions&err=نمی‌توانید نقش آخرین مالک فعال را تغییر دهید.", status_code=303)
    try:
        profile = _staff_profile_data(form, db)
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff/{user.id}?tab=overview&err={error}", status_code=303)
    before = {"role": user.role, "full_name": user.full_name,
              "salary_amount": user.salary_amount, "is_active": user.is_active,
              "job_title": user.job_title, "employment_type": user.employment_type,
              "hire_date": user.hire_date.isoformat() if user.hire_date else None,
              "contract_term_months": user.contract_term_months,
              "contract_end_date": user.contract_end_date.isoformat() if user.contract_end_date else None,
              "gender": user.gender, "insured": user.insured,
              "position_id": user.position_id}
    user.role = role
    # A partial form (the permissions tab posts only the role) must not wipe
    # the fields it omits: merge what arrived, leave the rest standing.
    posted = set(form.keys())
    for key, value in profile.items():
        if key in posted:
            setattr(user, key, value)
    # Checkboxes post nothing when cleared: the marker says the form owned
    # the switch, so absence means off rather than untouched.
    if "insured_present" in posted:
        user.insured = "insured" in posted
    password = str(form.get("password", ""))
    if password:
        if len(password) < 6:
            return RedirectResponse(url=f"/admin/staff/{user.id}?tab=overview&err=رمز عبور باید حداقل ۶ کاراکتر باشد.", status_code=303)
        user.password_hash = hash_password(password)
    db.commit()
    log_action(db, "staff_update", f"ویرایش کاربر {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after={"role": user.role, "full_name": user.full_name, "salary_amount": user.salary_amount, "is_active": user.is_active, "job_title": user.job_title, "employment_type": user.employment_type, "hire_date": user.hire_date.isoformat() if user.hire_date else None, "contract_term_months": user.contract_term_months, "contract_end_date": user.contract_end_date.isoformat() if user.contract_end_date else None, "gender": user.gender, "insured": user.insured, "position_id": user.position_id})
    return RedirectResponse(url=f"/admin/staff/{user.id}?msg=اطلاعات کارمند ذخیره شد.", status_code=303)


CAP_FIELDS = ("can_refund", "can_discount", "can_view_payroll", "can_reconcile_pos")


@router.post("/staff/{staff_id}/caps", response_class=HTMLResponse)
async def admin_staff_caps(staff_id: int, request: Request, db: Session = Depends(get_db)):
    """Flip one person's capability toggles. Narrow-only: a switch can take
    away what the role grants, never grant what the role denies, and owners
    bypass toggles — so an owner row is pinned all-on."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    form = await request.form()
    before = {field: getattr(user, field) for field in CAP_FIELDS}
    before.update({"max_discount_amount": user.max_discount_amount,
                   "max_discount_percent": user.max_discount_percent})
    if user.role == "owner":
        for field in CAP_FIELDS:
            setattr(user, field, True)
    else:
        for field in CAP_FIELDS:
            setattr(user, field, str(form.get(field, "")) == "1")
        try:
            user.max_discount_amount = _staff_limit(form, "max_discount_amount", "سقف مبلغ تخفیف")
            user.max_discount_percent = _staff_limit(form, "max_discount_percent", "سقف درصد تخفیف")
        except ValueError as error:
            db.rollback()
            return RedirectResponse(
                url=f"/admin/staff/{user.id}?tab=permissions&err={error}", status_code=303)
    db.commit()
    after = {field: getattr(user, field) for field in CAP_FIELDS}
    after.update({"max_discount_amount": user.max_discount_amount,
                  "max_discount_percent": user.max_discount_percent})
    log_action(db, "staff_caps", f"کلیدهای دسترسی {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after=after)
    return RedirectResponse(url=f"/admin/staff/{user.id}?tab=permissions&msg=کلیدهای دسترسی ذخیره شد.", status_code=303)


@router.get("/positions", response_class=HTMLResponse)
async def admin_positions(request: Request, db: Session = Depends(get_db)):
    """The company positions directory: titles the contract prints."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    positions = db.query(JobPosition).order_by(JobPosition.is_active.desc(), JobPosition.id).all()
    counts = {row[0]: row[1] for row in db.query(
        StaffUser.position_id, func.count(StaffUser.id)).filter(
        StaffUser.position_id.isnot(None)).group_by(StaffUser.position_id).all()}
    return templates.TemplateResponse(request, "admin/positions.html", {
        "positions": positions,
        "holder_counts": counts,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/positions", response_class=HTMLResponse)
async def admin_position_add(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    title = str(form.get("title", "") or "").strip()[:100]
    if not title:
        return RedirectResponse(url="/admin/positions?err=عنوان سمت الزامی است.", status_code=303)
    if db.query(JobPosition).filter(JobPosition.title == title).first():
        return RedirectResponse(url="/admin/positions?err=این سمت قبلاً ثبت شده است.", status_code=303)
    position = JobPosition(title=title)
    db.add(position)
    db.commit()
    log_action(db, "position_add", f"سمت سازمانی {title}", request=request,
               target_type="job_position", target_id=position.id, after={"title": title})
    return RedirectResponse(url="/admin/positions?msg=سمت ثبت شد.", status_code=303)


@router.post("/positions/{position_id}", response_class=HTMLResponse)
async def admin_position_update(position_id: int, request: Request, db: Session = Depends(get_db)):
    """Rename or retire a position. Retiring keeps holders' history — the
    contract of record already printed their title."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    position = db.query(JobPosition).filter(JobPosition.id == position_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="سمت یافت نشد")
    form = await request.form()
    before = {"title": position.title, "is_active": position.is_active}
    title = str(form.get("title", "") or "").strip()[:100]
    if title and title != position.title:
        if db.query(JobPosition).filter(JobPosition.title == title,
                                        JobPosition.id != position.id).first():
            return RedirectResponse(url="/admin/positions?err=این عنوان تکراری است.", status_code=303)
        position.title = title
    position.is_active = str(form.get("is_active", "") or "") == "1"
    db.commit()
    log_action(db, "position_update", f"سمت {position.title}", request=request,
               target_type="job_position", target_id=position.id,
               before=before, after={"title": position.title, "is_active": position.is_active})
    return RedirectResponse(url="/admin/positions?msg=سمت ذخیره شد.", status_code=303)


@router.post("/staff/{staff_id}/attendance", response_class=HTMLResponse)
async def admin_staff_attendance(staff_id: int, request: Request, db: Session = Depends(get_db)):
    """Mark one day present/absent/on-leave. Upsert by person-day: marking
    twice rewrites, never duplicates. A missing day stays unmarked."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    form = await request.form()
    status = str(form.get("status", "") or "").strip()
    if status not in ATTENDANCE_STATUSES:
        return RedirectResponse(
            url=f"/admin/staff/{user.id}?tab=attendance&err=وضعیت حضور نامعتبر است.",
            status_code=303)
    day_value = _parse_staff_date(str(form.get("day", "")))
    if not day_value:
        return RedirectResponse(
            url=f"/admin/staff/{user.id}?tab=attendance&err=تاریخ معتبر نیست.",
            status_code=303)
    day = day_value.date()
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.staff_user_id == user.id,
        AttendanceRecord.day == day).first()
    before = {"status": record.status if record else None}
    if record is None:
        record = AttendanceRecord(staff_user_id=user.id, day=day, status=status,
                                  recorded_by_user_id=guard.id)
        db.add(record)
    else:
        record.status = status
        record.recorded_by_user_id = guard.id
    record.note = str(form.get("note", "") or "").strip()[:200] or None
    db.commit()
    log_action(db, "attendance_mark", f"حضور {user.username} در {day.isoformat()}",
               request=request, target_type="staff_user", target_id=user.id,
               before=before, after={"status": status})
    import jdatetime
    jd = jdatetime.date.fromgregorian(date=day)
    month = f"{jd.year:04d}-{jd.month:02d}"
    return RedirectResponse(
        url=f"/admin/staff/{user.id}?tab=attendance&month={month}&msg=حضور ثبت شد.",
        status_code=303)


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
        period = normalize_period_key(str(form.get("period_key", "")))
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff/{user.id}?tab=payroll&err={error}", status_code=303)
    existing = db.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == user.id,
        SalaryPayment.period_key == period,
        SalaryPayment.is_voided == False).first()  # noqa: E712
    if existing is not None:
        return RedirectResponse(url=f"/admin/payroll/{existing.id}/receipt?msg=پرداخت حقوق قبلاً ثبت شده بود.", status_code=303)
    # Itemized lines ride as indexed fields; a voided month re-pays clean.
    items = []
    for index in range(12):
        kind = str(form.get(f"item_kind_{index}", "") or "").strip()
        if not kind:
            continue
        items.append({
            "kind": kind,
            "label": str(form.get(f"item_label_{index}", "") or ""),
            "amount": str(form.get(f"item_amount_{index}", "") or "0"),
        })
    try:
        payment = create_salary_payment(
            db,
            user,
            guard,
            period,
            deductions=_staff_amount(str(form.get("deductions", "0")), field="کسورات"),
            payment_method=str(form.get("payment_method", "cash")),
            note=str(form.get("note", "")),
            request_id=request.headers.get("X-Request-ID"),
            items=items,
        )
        db.commit()
    except ValueError as error:
        db.rollback()
        return RedirectResponse(url=f"/admin/staff/{user.id}?tab=payroll&err={error}", status_code=303)
    except IntegrityError:
        # A race for the same staff and month: the record already exists, so
        # land on its receipt instead of erroring.
        db.rollback()
        existing = db.query(SalaryPayment).filter(
            SalaryPayment.staff_user_id == user.id,
            SalaryPayment.period_key == period,
            SalaryPayment.is_voided == False).first()  # noqa: E712
        if existing is None:
            return RedirectResponse(url=f"/admin/staff/{user.id}?tab=payroll&err=حقوق این کارمند برای این ماه قبلاً ثبت شده است.", status_code=303)
        return RedirectResponse(url=f"/admin/payroll/{existing.id}/receipt?msg=پرداخت حقوق قبلاً ثبت شده بود.", status_code=303)
    log_action(db, "salary_payment", f"پرداخت حقوق {user.username} برای {payment.period_key}", request=request, target_type="salary_payment", target_id=payment.id, after={"net_amount": payment.net_amount, "expense_id": payment.expense_id})
    return RedirectResponse(url=f"/admin/payroll/{payment.id}/receipt?msg=پرداخت حقوق ثبت شد.", status_code=303)


@router.post("/payroll/{payment_id}/void", response_class=HTMLResponse)
async def admin_salary_void(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Void a salary payment with a reason: the linked salary expense reverses
    through the ledger, the row stays as voided history, and the freed month
    may be re-paid fresh from the profile."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    payment = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="پرداخت حقوق یافت نشد")
    form = await request.form()
    try:
        void_salary_payment(db, payment, guard, str(form.get("reason", "")),
                            request_id=request.headers.get("X-Request-ID"))
        db.commit()
    except ValueError as error:
        db.rollback()
        return RedirectResponse(
            url=f"/admin/staff/{payment.staff_user_id}?tab=payroll&err={error}", status_code=303)
    log_action(db, "salary_void", f"ابطال حقوق {payment.staff_user.username} برای {payment.period_key}", request=request, target_type="salary_payment", target_id=payment.id, after={"reason": payment.void_reason, "net_amount": payment.net_amount})
    return RedirectResponse(
        url=f"/admin/staff/{payment.staff_user_id}?tab=payroll&msg=پرداخت حقوق باطل شد.", status_code=303)


@router.post("/payroll/bulk", response_class=HTMLResponse)
async def admin_payroll_bulk(request: Request, db: Session = Depends(get_db)):
    """Payday for the whole shop in one press: every active, salaried staffer
    without a live payment for the month gets base pay; the already-paid are
    named as skipped, failures name their reason. Idempotent — pressing again
    only skips."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    try:
        report = run_monthly_payday(
            db, guard, str(form.get("period_key", "")),
            payment_method=str(form.get("payment_method", "cash")))
    except ValueError as error:
        return RedirectResponse(url=f"/admin/staff?err={error}", status_code=303)
    parts = [f"حقوق {len(report['created'])} نفر ثبت شد"]
    if report["skipped"]:
        parts.append(f"{len(report['skipped'])} نفر قبلاً پرداخت شده بودند")
    if report["waiting"]:
        parts.append(f"{len(report['waiting'])} نفر هنوز ماه اولشان تمام نشده")
    if report["failed"]:
        names = "، ".join(item["name"] for item in report["failed"][:5])
        parts.append(f"خطا برای {len(report['failed'])} نفر ({names})")
    log_action(db, "salary_bulk", f"پرداخت گروهی {report['period']}", request=request, target_type="salary_bulk", after={"created": len(report["created"]), "skipped": len(report["skipped"]), "waiting": len(report["waiting"]), "failed": len(report["failed"])})
    return RedirectResponse(url=f"/admin/staff?msg={'؛ '.join(parts)}.", status_code=303)


@router.get("/payroll/export")
async def admin_payroll_export(request: Request, year: str = "", db: Session = Depends(get_db)):
    """Every salary payment as a file: who, which month, the figures, voided
    or live. Optional Jalali-year filter (e.g. ?year=1405)."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    import csv
    import io
    query = db.query(SalaryPayment).join(
        StaffUser, SalaryPayment.staff_user_id == StaffUser.id)
    year_filter = to_english_digits((year or "").strip())
    if year_filter:
        query = query.filter(SalaryPayment.period_key.like(f"{year_filter}-%"))
    payments = query.order_by(SalaryPayment.period_key.desc(), SalaryPayment.id.desc()).all()
    out = [["کارمند", "ماه", "ناخالص", "کسورات", "خالص", "روش", "تاریخ پرداخت",
            "وضعیت", "دلیل ابطال"]]
    for payment in payments:
        person = payment.staff_user
        out.append([
            person.full_name or person.username, payment.period_key,
            payment.gross_amount, payment.deductions, payment.net_amount,
            "نقدی" if payment.payment_method == "cash" else "کارت",
            jalali_str(payment.paid_at),
            "باطل‌شده" if payment.is_voided else "پرداخت‌شده",
            payment.void_reason or "",
        ])
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(out)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="payroll_{today}.csv"'},
    )


@router.get("/payroll/{payment_id}/receipt", response_class=HTMLResponse)
async def admin_salary_receipt(payment_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    cap = require_cap(request, db, "can_view_payroll")
    if not hasattr(cap, "role"):
        return cap
    payment = db.query(SalaryPayment).filter(SalaryPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="رسید حقوق یافت نشد")
    voided_by = ""
    if payment.is_voided and payment.voided_by_user_id:
        actor = db.query(StaffUser).filter(
            StaffUser.id == payment.voided_by_user_id).first()
        voided_by = actor.full_name or actor.username if actor else ""
    # Year-to-date from live payments of the slip's own year.
    year = (payment.period_key or "")[:4]
    ytd = {"gross": 0, "deductions": 0, "net": 0}
    for sibling in db.query(SalaryPayment).filter(
            SalaryPayment.staff_user_id == payment.staff_user_id,
            SalaryPayment.period_key.like(f"{year}-%"),
            SalaryPayment.is_voided == False).all():  # noqa: E712
        ytd["gross"] += sibling.gross_amount or 0
        ytd["deductions"] += sibling.deductions or 0
        ytd["net"] += sibling.net_amount or 0
    kind_labels = {"base": "حقوق پایه", "advance": "پیش‌پرداخت", "overtime": "اضافه‌کاری",
                   "bonus": "پاداش", "deduction": "کسورات"}
    receipt_owner = {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()}
    return templates.TemplateResponse(request, "admin/salary_receipt.html", {
        "payment": payment,
        "staff_user": payment.staff_user,
        "owner_settings": receipt_owner,
        "store": get_store(db),
        "msg": request.query_params.get("msg", ""),
        "jalali_str": jalali_str,
        "fmt": fmt,
        "ytd": ytd,
        "ytd_year": year,
        "kind_labels": kind_labels,
        "voided_by": voided_by,
        "signatory_name": contract_signatory_name(db, receipt_owner),
    })


def contract_missing(db, owner_settings: dict, staff_user=None) -> list:
    """The single answer to «what blocks a printable contract», read by both
    the contract gate and the business page preview — one definition, or the
    two views will disagree about what «complete» means. With no staffer, only
    the business side is judged."""
    missing = []
    if not owner_settings.get("owner_full_name"):
        missing.append(("نام مالک", "/admin/owner-profile"))
    if not owner_settings.get("owner_business_name"):
        missing.append(("نام کسب‌وکار", "/admin/owner-profile"))
    if not owner_settings.get("owner_address"):
        missing.append(("نشانی کسب‌وکار", "/admin/owner-profile"))
    if staff_user is None:
        return missing
    if not staff_user.full_name:
        missing.append(("نام و نام خانوادگی کارمند", f"/admin/staff/{staff_user.id}"))
    if not staff_user.national_id:
        missing.append(("کد ملی کارمند", f"/admin/staff/{staff_user.id}"))
    if not staff_user.address:
        missing.append(("نشانی کارمند", f"/admin/staff/{staff_user.id}"))
    position_title = None
    if staff_user.position_id:
        position = db.query(JobPosition).filter(JobPosition.id == staff_user.position_id).first()
        position_title = position.title if position else None
    if not (position_title or staff_user.job_title):
        missing.append(("سمت سازمانی یا عنوان شغلی", f"/admin/staff/{staff_user.id}"))
    if not staff_user.salary_amount or staff_user.salary_amount <= 0:
        missing.append(("حقوق ماهانه", f"/admin/staff/{staff_user.id}"))
    if not staff_user.hire_date:
        missing.append(("تاریخ شروع همکاری", f"/admin/staff/{staff_user.id}"))
    return missing


def contract_position_title(db, staff_user) -> str | None:
    if staff_user.position_id:
        position = db.query(JobPosition).filter(JobPosition.id == staff_user.position_id).first()
        if position:
            return position.title
    return None


def contract_signatory_name(db, owner_settings: dict) -> str:
    """Who signs employer-side: the picked staffer, else the named owner."""
    signatory_id = (owner_settings.get("owner_signatory_user_id") or "").strip()
    if signatory_id.isdigit():
        signer = db.query(StaffUser).filter(StaffUser.id == int(signatory_id)).first()
        if signer:
            return signer.full_name or signer.username
    return owner_settings.get("owner_full_name", "")


@router.get("/staff/{staff_id}/contract", response_class=HTMLResponse)
async def admin_staff_contract(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    staff_user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="کارمند یافت نشد")
    owner_settings = {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()}
    missing = contract_missing(db, owner_settings, staff_user)
    position_title = contract_position_title(db, staff_user)
    signatory_name = contract_signatory_name(db, owner_settings)
    return templates.TemplateResponse(request, "admin/employment_contract.html", {
        "staff_user": staff_user,
        "owner_settings": owner_settings,
        "store": get_store(db),
        "jalali_str": jalali_str,
        "fmt": fmt,
        "amount_to_words": amount_to_words,
        "missing": missing,
        "position_title": position_title,
        "signatory_name": signatory_name,
        "employee_address": ("جناب آقای " if staff_user.gender == "male" else
                             "سرکار خانم " if staff_user.gender == "female" else "") + (
                             staff_user.full_name or staff_user.username),
    })


@router.get("/owner-profile", response_class=HTMLResponse)
async def admin_owner_profile(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    settings = {row.key: row.value for row in db.query(Settings).filter(Settings.key.like("owner_%")).all()}
    staffers = db.query(StaffUser).filter(StaffUser.is_active == True).order_by(  # noqa: E712
        StaffUser.full_name, StaffUser.username).all()
    feed = db.query(AdminLog).filter(AdminLog.action == "owner_profile_update").order_by(
        AdminLog.id.desc()).limit(5).all()
    actors = {user.id: (user.full_name or user.username) for user in db.query(StaffUser).filter(
        StaffUser.id.in_([entry.staff_user_id for entry in feed if entry.staff_user_id])).all()} if feed else {}
    return templates.TemplateResponse(request, "admin/owner_profile.html", {
        "owner_settings": settings,
        "store": get_store(db),
        "staffers": staffers,
        "business_missing": contract_missing(db, settings),
        "feed": feed,
        "actor_names": actors,
        "jalali_str": jalali_str,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


@router.post("/owner-profile", response_class=HTMLResponse)
async def admin_owner_profile_update(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    national_id = str(form.get("owner_national_id", "") or "").strip()[:30]
    if national_id:
        try:
            national_id = _check_national_id(national_id)
        except ValueError as error:
            return RedirectResponse(url=f"/admin/owner-profile?err={error}", status_code=303)
    phone = str(form.get("owner_phone", "") or "").strip()[:30]
    if phone:
        try:
            phone = _check_mobile(phone)
        except ValueError as error:
            return RedirectResponse(url=f"/admin/owner-profile?err={error}", status_code=303)
    signatory_id = to_english_digits(str(form.get("owner_signatory_user_id", "") or "").strip())
    if signatory_id:
        if not signatory_id.isdigit() or not db.query(StaffUser).filter(
                StaffUser.id == int(signatory_id)).first():
            return RedirectResponse(url="/admin/owner-profile?err=امضاکننده نامعتبر است.", status_code=303)
    bank_iban = str(form.get("owner_bank_iban", "") or "").strip()[:40]
    if bank_iban:
        try:
            bank_iban = _check_iban(bank_iban)
        except ValueError as error:
            return RedirectResponse(url=f"/admin/owner-profile?err={error}", status_code=303)
    website = str(form.get("owner_website", "") or "").strip()[:200]
    if website and (" " in website or "." not in website):
        return RedirectResponse(url="/admin/owner-profile?err=نشانی وب‌سایت معتبر نیست.", status_code=303)
    instagram = str(form.get("owner_instagram", "") or "").strip().lstrip("@")[:100]
    # Logo: validated raster bytes under static/uploads/business, old file
    # removed on replace. Optional forever — never blocks the gate.
    from services.tags import validate_tag_image
    logo_upload = form.get("logo")
    logo_path = None
    if logo_upload is not None and getattr(logo_upload, "filename", ""):
        try:
            raw = await logo_upload.read()
            extension = validate_tag_image(raw, logo_upload.filename or "",
                                           logo_upload.content_type)
        except ValueError as error:
            return RedirectResponse(url=f"/admin/owner-profile?err={error}", status_code=303)
        import uuid as _uuid
        from pathlib import Path as _Path
        directory = _Path("static/uploads/business")
        directory.mkdir(parents=True, exist_ok=True)
        old = db.query(Settings).filter(Settings.key == "owner_logo_path").first()
        if old and old.value:
            try:
                _Path(old.value.lstrip("/")).unlink(missing_ok=True)
            except OSError:
                pass
        target = directory / f"logo-{_uuid.uuid4().hex}.{extension}"
        target.write_bytes(raw)
        logo_path = f"/static/uploads/business/{target.name}"
    allowed = {"owner_full_name", "owner_phone", "owner_email", "owner_address", "owner_business_name", "owner_business_registration", "owner_signatory_title"}
    updates = {key: str(form.get(key, "")).strip()[:1000] for key in allowed}
    updates["owner_national_id"] = national_id or ""
    updates["owner_phone"] = phone or ""
    updates["owner_signatory_user_id"] = signatory_id
    updates["owner_bank_account"] = str(form.get("owner_bank_account", "") or "").strip()[:80]
    updates["owner_bank_iban"] = bank_iban or ""
    updates["owner_website"] = website
    updates["owner_instagram"] = instagram
    if logo_path is not None:
        updates["owner_logo_path"] = logo_path
    elif str(form.get("remove_logo", "") or "") == "1":
        updates["owner_logo_path"] = ""
    for key, value in updates.items():
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    log_action(db, "owner_profile_update", "به‌روزرسانی هویت کسب‌وکار", request=request, target_type="owner_profile")
    return RedirectResponse(url="/admin/owner-profile?msg=هویت کسب‌وکار ذخیره شد.", status_code=303)


@router.post("/staff/{staff_id}/disable", response_class=HTMLResponse)
async def admin_staff_disable(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.id == guard.id:
        return RedirectResponse(url=f"/admin/staff/{user.id}?err=نمی‌توانید دسترسی خودتان را ببندید.", status_code=303)
    if user.username == "owner":
        return RedirectResponse(url=f"/admin/staff/{user.id}?err=کاربر مالک اصلی را نمی‌توان غیرفعال کرد.", status_code=303)
    before = {"is_active": user.is_active}
    user.is_active = False
    db.commit()
    log_action(db, "staff_disable", f"غیرفعال‌سازی کاربر {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after={"is_active": False})
    return RedirectResponse(url=f"/admin/staff/{user.id}?msg=کاربر غیرفعال شد.", status_code=303)


@router.post("/staff/{staff_id}/enable", response_class=HTMLResponse)
async def admin_staff_enable(staff_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    before = {"is_active": user.is_active}
    user.is_active = True
    db.commit()
    log_action(db, "staff_enable", f"فعال‌سازی کاربر {user.username}", request=request, target_type="staff_user", target_id=user.id, before=before, after={"is_active": True})
    return RedirectResponse(url=f"/admin/staff/{user.id}?msg=کاربر فعال شد.", status_code=303)


# Every numeric field the settings form posts, with the bounds the server
# enforces and the label it refuses by. One table, two readers: the POST
# validates against it and the form renders its min/max from it, so the two
# cannot drift — a field added here is bounded on the page and on the server
# in the same edit, and the browser's attributes stay a kindness, not the rule.
SETTINGS_NUMERIC_RULES = {
    "barcode_code_length": (4, 12, "تعداد رقم کد بارکد"),
    "default_referrer_discount": (0, None, "مبلغ تخفیف معرف"),
    "default_referred_discount": (0, None, "مبلغ تخفیف معرفی‌شده"),
    "min_purchase_for_discount": (0, None, "حداقل مبلغ خرید برای تخفیف"),
    "monthly_referral_limit": (1, None, "سقف معرفی ماهانه"),
    "birthday_sms_days_before": (0, 60, "روزهای قبل از تولد"),
    "tryon_daily_limit": (0, 1000, "سقف تولید روزانه"),
    "credit_terms_days": (0, 365, "مهلت پرداخت نسیه"),
    "credit_reminder_min_hours": (0, 24 * 30, "فاصله بین دو یادآوری"),
    "default_credit_limit": (0, None, "سقف اعتبار پیش‌فرض"),
    "credit_surcharge_percent": (0, 100, "درصد افزایش نسیه"),
    "tier_points_per_amount": (0, None, "امتیاز به ازای هر خرید"),
    "tier_points_per_toman": (0, None, "مبلغ مبنای امتیاز"),
    "tier_gold_threshold": (0, None, "آستانه سطح طلایی"),
    "tier_gold_discount_percent": (0, 100, "درصد تخفیف سطح طلایی"),
    "tier_gold_birthday_discount": (0, None, "تخفیف تولد سطح طلایی"),
    "tier_diamond_threshold": (0, None, "آستانه سطح الماس"),
    "tier_diamond_discount_percent": (0, 100, "درصد تخفیف سطح الماس"),
    "tier_diamond_birthday_discount": (0, None, "تخفیف تولد سطح الماس"),
    "tier_downgrade_months": (0, 120, "ماه‌های عدم خرید"),
    "pos_terminal_port": (1, 65535, "پورت کارت‌خوان"),
    "check_upcoming_days": (1, 90, "بازه چک‌های نزدیک"),
}


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    # The form and the table are checked against each other on every render, so
    # a template that hard-codes a bound the table lacks — or a numeric field
    # the table was never told about — fails visibly here, on the page the
    # owner is looking at, instead of surfacing later as a rule the browser
    # showed and the server ignored.
    template_source = (Path("templates") / "admin" / "settings.html").read_text(encoding="utf-8")
    macro_fields = set(re.findall(r"num\('([a-z_]+)'", template_source))
    missing = sorted(macro_fields - set(SETTINGS_NUMERIC_RULES))
    if missing:
        return templates.TemplateResponse(request, "admin/settings.html", {
            "settings": {}, "tier_config": {}, "downgrade_rule": tier_downgrade_rule(db),
            "numeric_rules": {}, "store": get_store(db),
            "msg": "",
            "err": "خطای قالب: «" + "، ".join(missing) + "» در جدول قواعد نیست — صفحه از ذخیره‌سازی محافظت می‌کند.",
        })
    orphans = sorted(set(SETTINGS_NUMERIC_RULES) - macro_fields)
    if orphans:
        return templates.TemplateResponse(request, "admin/settings.html", {
            "settings": {}, "tier_config": {}, "downgrade_rule": tier_downgrade_rule(db),
            "numeric_rules": {}, "store": get_store(db),
            "msg": "",
            "err": "خطای قالب: «" + "، ".join(orphans) + "» در فرم نیست — یک قاعده بی‌میدان.",
        })
    # Source checks alone cannot see a bypassed helper: the bounds must be
    # verified in what the page actually paints, compared against the table,
    # so a macro overridden per field or a hand-written min/max names itself
    # here as a figure the table never said.
    check_context = {
        "request": request,
        "settings": {s.key: s.value for s in db.query(Settings).all()},
        "tier_config": get_tier_config(db),
        "downgrade_rule": tier_downgrade_rule(db),
        "numeric_rules": SETTINGS_NUMERIC_RULES,
        "store": get_store(db), "msg": "", "err": "",
    }
    for processor in templates.context_processors:
        check_context.update(processor(request))
    rendered = templates.get_template("admin/settings.html").render(check_context)
    hard_bounds = []
    for m in re.finditer(r'<input\b[^>]*>', rendered):
        tag = m.group(0)
        if 'type="number"' not in tag:
            continue
        name_m = re.search(r'name="([a-z_]+)"', tag)
        rule = SETTINGS_NUMERIC_RULES.get(name_m.group(1)) if name_m else None
        min_m = re.search(r'\bmin="(-?\d+)"', tag)
        max_m = re.search(r'\bmax="(-?\d+)"', tag)
        painted = (int(min_m.group(1)) if min_m else None,
                   int(max_m.group(1)) if max_m else None)
        expected = (rule[0], rule[1]) if rule else (None, None)
        if painted != expected:
            hard_bounds.append(name_m.group(1) if name_m else tag[:60])
    hard_bounds = sorted(set(hard_bounds))

    if hard_bounds:
        return templates.TemplateResponse(request, "admin/settings.html", {
            "settings": {}, "tier_config": {}, "downgrade_rule": tier_downgrade_rule(db),
            "numeric_rules": {}, "store": get_store(db),
            "msg": "",
            "err": "خطای قالب: «" + "، ".join(hard_bounds) + "» مرز خودش را نوشته — از جدول قواعد استفاده کنید.",
        })

    settings = {s.key: s.value for s in db.query(Settings).all()}
    tier_config = get_tier_config(db)
    return templates.TemplateResponse(request, "admin/settings.html", {
        "settings": settings,
        "tier_config": tier_config,
        # The one answer to «is the downgrade rule in use», shared with the
        # review page and the dashboard card rather than re-derived here.
        "downgrade_rule": tier_downgrade_rule(db),
        "numeric_rules": SETTINGS_NUMERIC_RULES,
        "store": get_store(db),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    })


def get_theme_id(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
    effective, _retired = migrate_retired_theme(row.value if row else None)
    return effective if effective in THEMES else DEFAULT_THEME_ID


@router.get("/settings/appearance", response_class=HTMLResponse)
async def admin_settings_appearance(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    settings = {s.key: s.value for s in db.query(Settings).all()}
    # A shop that chose a retired palette is told where it moved — the shell
    # already wears the heir (see migrate_retired_theme), so the notice names
    # the move instead of asking.
    _effective, retired = migrate_retired_theme(settings.get(THEME_SETTING_KEY))
    # The theme cards each carry a live sample chart painted by the same
    # renderer the analytics pages use, so the owner judges real shading.
    return templates.TemplateResponse(request, "admin/settings_appearance.html", {
        "show_charts": True,
        "settings": settings,
        "store": get_store(db),
        "themes": all_theme_previews(db),
        "retired_theme_id": retired,
        "retired_theme_home": THEMES[_effective]["name"] if retired else "",
        "default_custom_primary": DEFAULT_CUSTOM_PRIMARY,
        "default_custom_secondary": DEFAULT_CUSTOM_SECONDARY,
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
    primary = str(form.get(CUSTOM_PRIMARY_KEY, DEFAULT_CUSTOM_PRIMARY)).strip().upper()
    secondary = str(form.get(CUSTOM_SECONDARY_KEY, DEFAULT_CUSTOM_SECONDARY)).strip().upper()
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
    invalidate_theme_cache()
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
    # Every numeric setting the form posts is checked before anything is saved,
    # against the same table (SETTINGS_NUMERIC_RULES) the form is rendered from,
    # so the two cannot disagree — and the fields the page bounded but the
    # server ignored (the referral discounts, the card-reader port…) are
    # bounded here too. The `min`/`max` attributes are the browser's kindness
    # to a careful owner, not the rule: a typed minus sign, a stray Persian
    # digit, or a cleared field posted straight through to Settings, and the
    # readers answered in their own ways — a negative نسیه surcharge
    # *discounted* the invoice, a negative points rate drove a customer's
    # points below zero, and a negative threshold promoted every customer to
    # gold. A value the page cannot explain is refused with the field's name,
    # never saved.
    numeric_rules = SETTINGS_NUMERIC_RULES
    for key, (low, high, label) in numeric_rules.items():
        raw = str(form.get(key, "")).strip()
        if not raw:
            continue
        try:
            number = int(to_english_digits(raw))
        except (TypeError, ValueError):
            return RedirectResponse(
                url=f"/admin/settings?err=«{label}» باید یک عدد باشد — «{raw}» ذخیره نشد.",
                status_code=303)
        if number < low or (high is not None and number > high):
            if low > 0:
                band = f"بین {to_persian_digits(low)} و {to_persian_digits(high)}" if high is not None else f"حداقل {to_persian_digits(low)}"
            else:
                band = f"حداکثر {to_persian_digits(high)}" if high is not None else ""
            tail = f" {band}" if band else ""
            return RedirectResponse(
                url=f"/admin/settings?err=«{label}»{tail} — مقدار ذخیره نشد.",
                status_code=303)
    # A validated numeric setting is stored normalised: the owner typed
    # Persian digits, but every reader (`get_barcode_code_length`, the tier
    # thresholds…) parses with `int()` and must never meet «۸» in the store.
    for key, (low, high, label) in numeric_rules.items():
        raw = str(form.get(key, "")).strip()
        if raw:
            updates[key] = str(int(to_english_digits(raw)))
    # The cheque reminder days are a list, not a number, so the table above
    # cannot bound them — but an unreadable list must still be refused rather
    # than saved and silently falling back to defaults on every read.
    if "check_default_reminders" in updates:
        from services.checks import normalize_reminder_days
        try:
            days = normalize_reminder_days(updates["check_default_reminders"])
        except ValueError as error:
            return RedirectResponse(
                url=f"/admin/settings?err=روزهای هشدار چک: {error} — مقدار ذخیره نشد.",
                status_code=303)
        updates["check_default_reminders"] = ", ".join(str(day) for day in days)
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

    if not verify_password(current_password, guard.password_hash):
        return RedirectResponse(url="/admin/settings?err=رمز عبور فعلی اشتباه است.", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse(url="/admin/settings?err=رمز جدید باید حداقل ۶ کاراکتر باشد.", status_code=303)
    if new_password != new_password_confirm:
        return RedirectResponse(url="/admin/settings?err=رمز جدید و تکرار آن یکسان نیستند.", status_code=303)

    guard.password_hash = hash_password(new_password)
    db.commit()
    log_action(db, "change_password", f"تغییر رمز ورود {guard.username}", request=request, target_type="staff_user", target_id=guard.id)
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

    every = normalize_every_days(
        get_setting_int(db, "backup_every_days", BACKUP_EVERY_DEFAULT))
    keep = normalize_keep_count(
        get_setting_int(db, "backup_keep_count", BACKUP_KEEP_DEFAULT))
    backups = []
    for backup in list_backups():
        row = {**backup, **verify_cached(Path("backups") / backup["name"])}
        row["size_fa"] = format_bytes_fa(row.get("size", 0))
        backups.append(row)
    count, total = backups_storage()
    now = datetime.now(timezone.utc)
    last = latest_backup()
    if last is None:
        last_age, next_run = "", ""
    else:
        age_days = int((now.timestamp() - last.timestamp()) // 86400)
        last_age = ("امروز" if age_days <= 0
                    else f"{to_persian_digits(str(age_days))} روز پیش")
        due_on = last + timedelta(days=every)
        next_run = ("امشب" if due_on.date() <= datetime.now().date()
                    else jalali_str(due_on, False))
    return templates.TemplateResponse(request, "admin/backups.html", {
        "backups": backups,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "jalali_str": jalali_str,
        "last_backup": jalali_str(last, False) if last else "",
        "last_age": last_age,
        "next_run": next_run,
        "every_days": every,
        "every_days_fa": to_persian_digits(str(every)),
        "every_options": [(option, to_persian_digits(str(option)))
                          for option in BACKUP_EVERY_OPTIONS],
        "keep_options": [(option, to_persian_digits(str(option)))
                         for option in (5, 10, 15, 20, 30)],
        "keep_count": keep,
        "shelf_count": to_persian_digits(str(count)),
        "shelf_size": format_bytes_fa(total),
    })


@router.post("/backups/schedule", response_class=HTMLResponse)
async def admin_backup_schedule(request: Request, db: Session = Depends(get_db)):
    """The owner's backup cadence: every 10/20/30 days, keeping K files."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    every = normalize_every_days(form.get("backup_every_days"))
    keep = normalize_keep_count(form.get("backup_keep_count"))
    for key, value in (("backup_every_days", str(every)),
                       ("backup_keep_count", str(keep))):
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(Settings(key=key, value=value))
    db.commit()
    log_action(db, "backup_schedule",
               f"زمان‌بندی پشتیبان: هر {every} روز، نگهداری {keep} نسخه",
               request=request, target_type="settings")
    return RedirectResponse(url="/admin/backups?msg=زمان‌بندی پشتیبان ذخیره شد.",
                            status_code=303)


@router.post("/backups/delete", response_class=HTMLResponse)
async def admin_backup_delete(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    name = str(form.get("name") or "")
    if delete_backup(name):
        log_action(db, "backup_delete", f"حذف پشتیبان {name}",
                   request=request, target_type="backup")
        return RedirectResponse(url="/admin/backups?msg=پشتیبان حذف شد.",
                                status_code=303)
    return RedirectResponse(url="/admin/backups?err=فایل یافت نشد.", status_code=303)


@router.post("/backups/recheck", response_class=HTMLResponse)
async def admin_backup_recheck(request: Request, db: Session = Depends(get_db)):
    """Force a fresh integrity check of one file, refreshing its badge."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    form = await request.form()
    result = recheck_backup(str(form.get("name") or ""))
    if result is None:
        return RedirectResponse(url="/admin/backups?err=فایل یافت نشد.", status_code=303)
    if result.get("verified"):
        return RedirectResponse(url="/admin/backups?msg=پشتیبان سالم است.", status_code=303)
    return RedirectResponse(
        url="/admin/backups?err=پشتیبان خراب است — همین حالا نسخه تازه بگیرید.",
        status_code=303)


@router.get("/backups/download-all", response_class=HTMLResponse)
async def admin_backups_download_all(request: Request, db: Session = Depends(get_db)):
    """The whole shelf as one zip — the off-machine second copy."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    data, filename = download_all_bytes()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/backups/download", response_class=HTMLResponse)
async def admin_backup_download(request: Request, name: str = "", db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    path = backup_download_path(name)
    if not path:
        return RedirectResponse(url="/admin/backups?err=فایل یافت نشد.", status_code=303)
    return FileResponse(path, filename=Path(name).name)


POS_STATUSES = {
    "all", "unresolved", "resolved", "created", "sent", "uncertain",
    "approved", "linked_to_sale", "cancelled", "declined",
}


def _pos_list_conds(search: str, status_filter: str) -> list:
    """The filter chain the terminal list reads — one definition for the page
    and the export, so a file can never answer a different view than the
    screen that ordered it."""
    conds = []
    if search:
        digits = search.lstrip("#").strip()
        if digits.isdigit():
            value = int(digits)
            conds.append(or_(POSTransaction.id == value, POSTransaction.amount == value))
        else:
            like = f"%{search}%"
            conds.append(or_(
                POSTransaction.provider_reference.ilike(like),
                POSTransaction.terminal_transaction_number.ilike(like),
                POSTransaction.retrieval_reference_number.ilike(like),
                POSTransaction.masked_card.ilike(like),
                POSTransaction.reconciliation_note.ilike(like),
            ))
    if status_filter == "unresolved":
        conds.append(POSTransaction.status.in_(("created", "sent", "uncertain", "approved")))
        conds.append(POSTransaction.sale_id.is_(None))
        conds.append(POSTransaction.reconciled == False)  # noqa: E712
    elif status_filter == "resolved":
        conds.append(POSTransaction.reconciled == True)  # noqa: E712
    elif status_filter != "all":
        conds.append(POSTransaction.status == status_filter)
    return conds


@router.get("/pos-reconciliation", response_class=HTMLResponse)
async def admin_pos_reconciliation(
    request: Request,
    q: str = "",
    status: str = "all",
    page: str = "1",
    per_page: str = "25",
    sort: str = "date",
    dir: str = "desc",
    db: Session = Depends(get_db),
):
    """Show terminal attempts that need local reconciliation."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    from urllib.parse import urlencode
    search = (q or "").strip()
    status_filter = status if status in POS_STATUSES else "all"
    page_int = page_arg(page)
    per_page_int = int(per_page) if str(per_page).isdigit() and int(per_page) in (10, 25, 50) else 25
    sort_key, sort_dir = parse_sort(request.query_params, {"date": "desc", "amount": "desc"}, "date")

    conds = _pos_list_conds(search, status_filter)

    base = db.query(POSTransaction).filter(*conds)
    order_column = POSTransaction.amount if sort_key == "amount" else POSTransaction.created_at
    order = order_column.desc() if sort_dir == "desc" else order_column.asc()
    total_count = base.count()
    total_pages = max(1, -(-total_count // per_page_int))
    page_int = min(page_int, total_pages)
    transactions = base.order_by(order, POSTransaction.id.desc()) \
        .offset((page_int - 1) * per_page_int).limit(per_page_int).all()
    # The KPI stays the shop-wide outstanding the dashboard shows, whatever
    # the list is filtered to — the heading above the table names the view.
    unresolved_count = unresolved_transactions(db)
    # The exposure, not just the queue: what the outstanding rows add up to.
    unresolved_amount = db.query(func.coalesce(func.sum(POSTransaction.amount), 0)).filter(
        POSTransaction.status.in_(("created", "sent", "uncertain", "approved")),
        POSTransaction.sale_id.is_(None),
        POSTransaction.reconciled == False,  # noqa: E712
    ).scalar() or 0
    operator_ids = {t.operator_user_id for t in transactions if t.operator_user_id}
    operator_names = {u.id: (u.full_name or u.username) for u in db.query(StaffUser).filter(
        StaffUser.id.in_(list(operator_ids))).all()} if operator_ids else {}
    terminal = get_terminal_config(db)
    resolutions = {
        "confirmed_cancelled": "تأیید لغو",
        "reversed_externally": "برگشت خارجی",
        "duplicate": "تکراری",
        "terminal_error": "خطای کارت‌خوان",
        "provider_investigation": "بررسی ارائه‌دهنده",
    }
    has_filters = bool(search or status_filter != "all")
    from urllib.parse import urlencode
    base_qs = urlencode({
        **({"q": search} if search else {}),
        **({"status": status_filter} if status_filter != "all" else {}),
        "per_page": per_page_int,
    })
    return templates.TemplateResponse(request, "admin/pos_reconciliation.html", {
        "transactions": transactions,
        "unresolved_count": unresolved_count,
        "unresolved_amount": unresolved_amount,
        "operator_names": operator_names,
        "terminal": terminal,
        "resolutions": resolutions,
        "is_owner": role_allows(guard.role, "owner"),
        # The review forms paint only for whoever may actually resolve; the
        # POST enforces the same capability.
        "can_reconcile": effective_cap(guard, "can_reconcile_pos"),
        "search": search,
        "status_filter": status_filter,
        "statuses": {
            "all": "همه",
            "unresolved": "حل‌نشده",
            "resolved": "تعیین‌تکلیف‌شده",
            "created": "ایجادشده",
            "sent": "ارسال‌شده",
            "uncertain": "نامشخص",
            "approved": "تأییدشده",
            "linked_to_sale": "متصل به فاکتور",
            "cancelled": "لغوشده",
            "declined": "ردشده",
        },
        "page": page_int,
        "total_pages": total_pages,
        "total_count": total_count,
        "per_page": per_page_int,
        "per_page_options": (10, 25, 50),
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "base_qs": base_qs,
        "has_filters": has_filters,
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
    cap = require_cap(request, db, "can_reconcile_pos")
    if not hasattr(cap, "role"):
        return cap

    transaction = db.query(POSTransaction).filter(POSTransaction.id == transaction_id).first()
    if not transaction:
        return RedirectResponse(url="/admin/pos-reconciliation?err=تراکنش کارت‌خوان یافت نشد.", status_code=303)
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
        # Paid is conferred by an invoice, never by a review: recording it
        # here would invent money the till never counted.
        return RedirectResponse(url="/admin/pos-reconciliation?err=تأیید پرداخت فقط از مسیر ایجاد فاکتور انجام می‌شود؛ اینجا فقط ثبت نتیجه بررسی است.", status_code=303)
    if masked_card and not masked_card.startswith("****"):
        return RedirectResponse(url="/admin/pos-reconciliation?err=فقط اطلاعات کارت ماسک‌شده (****) مجاز است.", status_code=303)

    if transaction.reconciled:
        return RedirectResponse(url="/admin/pos-reconciliation?err=این تراکنش قبلاً تطبیق داده شده است.", status_code=303)

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


@router.get("/pos-reconciliation/export")
async def admin_pos_export(
    request: Request,
    q: str = "",
    status: str = "all",
    sort: str = "date",
    dir: str = "desc",
    db: Session = Depends(get_db),
):
    """The filtered terminal list as a file: the same rows the screen showed,
    not the whole ledger. Raw integers for money, Jalali dates the shop reads."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    import csv
    import io
    search = (q or "").strip()
    status_filter = status if status in POS_STATUSES else "all"
    sort_key, sort_dir = parse_sort(request.query_params, {"date": "desc", "amount": "desc"}, "date")
    order_column = POSTransaction.amount if sort_key == "amount" else POSTransaction.created_at
    order = order_column.desc() if sort_dir == "desc" else order_column.asc()
    rows = db.query(POSTransaction).filter(
        *_pos_list_conds(search, status_filter)).order_by(order, POSTransaction.id.desc()).all()
    operator_ids = {t.operator_user_id for t in rows if t.operator_user_id}
    names = {u.id: (u.full_name or u.username) for u in db.query(StaffUser).filter(
        StaffUser.id.in_(list(operator_ids))).all()} if operator_ids else {}
    labels = {
        "confirmed_cancelled": "تأیید لغو", "reversed_externally": "برگشت خارجی",
        "duplicate": "تکراری", "terminal_error": "خطای کارت‌خوان",
        "provider_investigation": "بررسی ارائه‌دهنده",
    }
    out = [["شناسه", "زمان", "مبلغ", "وضعیت", "کد پاسخ", "مرجع ارائه‌دهنده",
            "شماره تراکنش", "شماره پیگیری", "فاکتور", "تعیین‌تکلیف‌شده",
            "نتیجه", "دلیل", "ثبت‌کننده"]]
    for t in rows:
        out.append([
            t.id, jalali_str(t.created_at), t.amount or 0, t.status,
            t.response_code or "", t.provider_reference or "",
            t.terminal_transaction_number or "", t.retrieval_reference_number or "",
            t.sale_id or "", "بله" if t.reconciled else "خیر",
            labels.get(t.resolution_type, t.resolution_type or ""),
            t.resolution_evidence or "", names.get(t.operator_user_id, ""),
        ])
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(out)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pos_reconciliation_{today}.csv"'},
    )


@router.get("/events", response_class=HTMLResponse)
async def admin_events(
    request: Request,
    aggregate_type: str = "",
    event_type: str = "",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Retired: the domain-event stream lives in the merged audit timeline.

    Old bookmarks and the `limit` box land on the events tab with their
    filters intact — `limit` has no counterpart there (fixed 100 + show
    more), so only it is dropped.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    target = "/admin/logs?source=events"
    if aggregate_type.strip():
        target += f"&aggregate_type={quote_plus(aggregate_type.strip())}"
    if event_type.strip():
        target += f"&event_type={quote_plus(event_type.strip())}"
    return RedirectResponse(url=target, status_code=303)


# One screen of audit rows; "show more" appends the next screen via ?offset=.
LOGS_PAGE_SIZE = 100

# CSV column order, screen order: time, action, actor, record, network, words.
LOGS_CSV_HEADERS = ["زمان", "عملیات", "کننده", "پیوند", "آی‌پی", "جزئیات"]


def _logs_filter_values(request: Request, db: Session) -> dict:
    """Cleaned audit-trail filters, shared by the page and the CSV export.

    Unknown values degrade to "no filter" — the trail never shows an empty
    page for a stale bookmark, and the form repaints clean.
    """
    qp = request.query_params
    source = (qp.get("source") or "actions").strip()
    if source not in ("actions", "events", "all"):
        source = "actions"
    action = (qp.get("action") or "").strip()
    date_from_raw = (qp.get("from") or "").strip()
    date_to_raw = (qp.get("to") or "").strip()
    aggregate_type = (qp.get("aggregate_type") or "").strip()[:50]
    event_type = (qp.get("event_type") or "").strip()[:60]
    search = (qp.get("q") or "").strip()[:100]
    try:
        offset = max(int(qp.get("offset") or 0), 0)
    except ValueError:
        offset = 0

    known_actions = sorted(row[0] for row in db.query(AdminLog.action).distinct().all())
    if action not in known_actions:
        action = ""
    actor = None
    actor_raw = (qp.get("actor") or "").strip()
    if actor_raw.isdigit():
        actor = db.query(StaffUser).filter(StaffUser.id == int(actor_raw)).first()
    date_from = parse_jalali_input(date_from_raw)
    if date_from is None:
        date_from_raw = ""
    date_to = parse_jalali_input_end(date_to_raw)
    if date_to is None:
        date_to_raw = ""
    return {
        "source": source, "action": action, "actor": actor,
        "known_actions": known_actions, "search": search,
        "aggregate_type": aggregate_type, "event_type": event_type,
        "date_from": date_from, "date_from_raw": date_from_raw,
        "date_to": date_to, "date_to_raw": date_to_raw, "offset": offset,
    }


def _logs_base_query(db: Session, filters: dict):
    """The filtered audit query, newest first. Offset paging stays stable on
    id order — equal timestamps can neither duplicate nor skip a row."""
    query = (db.query(AdminLog)
             .options(joinedload(AdminLog.staff_user))
             .order_by(AdminLog.id.desc()))
    if filters["action"]:
        query = query.filter(AdminLog.action == filters["action"])
    if filters["actor"] is not None:
        query = query.filter(AdminLog.staff_user_id == filters["actor"].id)
    if filters["search"]:
        like = "%" + filters["search"].replace("\\", "\\\\").replace(
            "%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(AdminLog.detail.like(like, escape="\\"))
    if filters["date_from"] is not None:
        query = query.filter(AdminLog.created_at >= filters["date_from"])
    if filters["date_to"] is not None:
        query = query.filter(AdminLog.created_at <= filters["date_to"])
    return query


def _events_base_query(db: Session, filters: dict):
    """The filtered domain-event query, newest first — the audit twin of the
    staff-action query above, so the merged view reads both the same way."""
    query = (db.query(BusinessEvent)
             .options(joinedload(BusinessEvent.actor_user))
             .order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.id.desc()))
    if filters["aggregate_type"]:
        query = query.filter(BusinessEvent.aggregate_type == filters["aggregate_type"])
    if filters["event_type"]:
        query = query.filter(BusinessEvent.event_type == filters["event_type"])
    if filters["search"]:
        like = "%" + filters["search"].replace("\\", "\\\\").replace(
            "%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(BusinessEvent.payload.like(like, escape="\\"))
    if filters["date_from"] is not None:
        query = query.filter(BusinessEvent.occurred_at >= filters["date_from"])
    if filters["date_to"] is not None:
        query = query.filter(BusinessEvent.occurred_at <= filters["date_to"])
    return query


def _action_item(row: AdminLog) -> dict:
    """One staff-action row in the timeline's shared shape."""
    user = row.staff_user
    return {
        "source": "action",
        "row_id": row.id,
        "created_at": row.created_at,
        "title": ADMIN_ACTION_LABELS.get(row.action, row.action),
        "code": row.action,
        "tone": admin_action_tone(row.action),
        "actor_name": (user.full_name or user.username) if user else "سیستم",
        "actor_url": f"/admin/staff/{user.id}" if user else "",
        "link": admin_target_link(row.target_type, row.target_id),
        "ip": row.ip_address or "",
        "detail": row.detail or "",
        "diff": admin_log_diff(row.before_json, row.after_json),
        "fields": admin_log_diff_fields(row.before_json, row.after_json),
    }


def _event_item(event: BusinessEvent) -> dict:
    """One domain event in the same shape — payload reads as the detail."""
    user = event.actor_user
    payload = event_payload(event)
    detail = json.dumps(payload, ensure_ascii=False) if payload else ""
    return {
        "source": "event",
        "row_id": event.id,
        "created_at": event.occurred_at,
        "title": BUSINESS_EVENT_LABELS.get(event.event_type, event.event_type),
        "code": event.event_type,
        "tone": business_event_tone(event.event_type),
        "actor_name": (user.full_name or user.username) if user else "سیستم",
        "actor_url": f"/admin/staff/{user.id}" if user else "",
        "link": admin_target_link(event.aggregate_type, event.aggregate_id),
        "ip": "",
        "detail": detail,
        "diff": None,
    }


# Audit rows older than this are offered for archive, never auto-deleted.
LOGS_ARCHIVE_DAYS = 365
LOGS_ARCHIVE_KEEP = 12


def _logs_archivable_count(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOGS_ARCHIVE_DAYS)
    return db.query(AdminLog).filter(AdminLog.created_at < cutoff).count()


@router.get("/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    filters = _logs_filter_values(request, db)
    source = filters["source"]
    if (filters["date_from"] is not None and filters["date_to"] is not None
            and filters["date_from"] > filters["date_to"]):
        return RedirectResponse(
            url=f"/admin/logs?source={source}&err=«از تاریخ» باید پیش از «تا تاریخ» باشد.",
            status_code=303)
    offset = filters["offset"]
    items: list[dict] = []
    has_more = False
    total: int | None = None
    # Replay carries the active filters into the paging, export and way-back
    # links — one definition, so a file can never answer a different view
    # than the screen that ordered it.
    replay = {"source": source} if source != "actions" else {}
    if filters["action"]:
        replay["action"] = filters["action"]
    if filters["actor"] is not None:
        replay["actor"] = filters["actor"].id
    if filters["aggregate_type"]:
        replay["aggregate_type"] = filters["aggregate_type"]
    if filters["event_type"]:
        replay["event_type"] = filters["event_type"]
    if filters["search"]:
        replay["q"] = filters["search"]
    if filters["date_from_raw"]:
        replay["from"] = filters["date_from_raw"]
    if filters["date_to_raw"]:
        replay["to"] = filters["date_to_raw"]

    if source == "events":
        base = _events_base_query(db, filters)
        total = base.count()
        rows = base.offset(offset).limit(
            LOGS_PAGE_SIZE + 1).all()
        has_more = len(rows) > LOGS_PAGE_SIZE
        items = [_event_item(row) for row in rows[:LOGS_PAGE_SIZE]]
    elif source == "all":
        # The merged view reads the newest screen of each stream and weaves
        # them — deep paging lives in the single-source views, where an
        # offset means one thing.
        action_rows = _logs_base_query(db, filters).limit(LOGS_PAGE_SIZE).all()
        event_rows = _events_base_query(db, filters).limit(LOGS_PAGE_SIZE).all()
        items = sorted(
            [_action_item(row) for row in action_rows]
            + [_event_item(row) for row in event_rows],
            key=lambda item: (item["created_at"], item["row_id"]),
            reverse=True)[:LOGS_PAGE_SIZE]
    else:
        base = _logs_base_query(db, filters)
        total = base.count()
        rows = base.offset(offset).limit(
            LOGS_PAGE_SIZE + 1).all()
        has_more = len(rows) > LOGS_PAGE_SIZE
        items = [_action_item(row) for row in rows[:LOGS_PAGE_SIZE]]

    # Day groups, newest day first: the items already arrive newest first,
    # so first-seen order is chronological with no re-sort.
    groups = []
    for item in items:
        key, label = jalali_day_label(item["created_at"])
        if groups and groups[-1]["key"] == key:
            groups[-1]["items"].append(item)
        else:
            groups.append({"key": key, "label": label, "items": [item]})
    more_params = dict(replay, offset=offset + LOGS_PAGE_SIZE)
    more_url = "/admin/logs?" + "&".join(
        f"{key}={quote_plus(str(value))}" for key, value in more_params.items())
    export_url = "/admin/logs/export"
    if replay:
        export_url += "?" + "&".join(
            f"{key}={quote_plus(str(value))}" for key, value in replay.items())
    back_url = "/admin/logs"
    if replay:
        back_url += "?" + "&".join(
            f"{key}={quote_plus(str(value))}" for key, value in replay.items())
    poll_url = "/admin/logs/latest"
    if replay:
        poll_url += "?" + "&".join(
            f"{key}={quote_plus(str(value))}" for key, value in replay.items())
    position_line = ""
    if total is not None and items:
        start = offset + 1
        end = offset + len(items)
        fa = to_persian_digits
        position_line = (f"نمایش {fa(str(start))} تا {fa(str(end))}"
                         f" از {fa(str(total))}")

    return templates.TemplateResponse(request, "admin/logs.html", {
        "groups": groups,
        "has_rows": bool(items),
        "show_source": source == "all",
        "source": source,
        "search": filters["search"],
        "hl": highlight,
        "action_labels": ADMIN_ACTION_LABELS,
        "jalali_str": jalali_str,
        "rel_time": rel_time,
        "known_actions": filters["known_actions"],
        "actors": db.query(StaffUser).order_by(StaffUser.id).all(),
        "action": filters["action"],
        "actor_id": filters["actor"].id if filters["actor"] is not None else "",
        "aggregate_type": filters["aggregate_type"],
        "event_type": filters["event_type"],
        "date_from": filters["date_from_raw"],
        "date_to": filters["date_to_raw"],
        "filters_active": bool(
            filters["action"] or filters["actor"] is not None
            or filters["aggregate_type"] or filters["event_type"]
            or filters["search"]
            or filters["date_from_raw"] or filters["date_to_raw"]),
        "offset": offset,
        "has_more": has_more,
        "more_url": more_url,
        "export_url": export_url,
        "back_url": back_url,
        "poll_url": poll_url,
        "position_line": position_line,
        "first_action_id": next(
            (item["row_id"] for item in items if item["source"] == "action"), 0),
        "first_event_id": next(
            (item["row_id"] for item in items if item["source"] == "event"), 0),
        "archivable": _logs_archivable_count(db),
    })


@router.get("/logs/export")
async def admin_logs_export(request: Request, db: Session = Depends(get_db)):
    """The filtered audit trail as a file: whatever the screen shows, all of it."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    import csv
    import io
    filters = _logs_filter_values(request, db)
    source = filters["source"]
    if source == "events":
        items = [_event_item(row) for row in _events_base_query(db, filters).all()]
    elif source == "all":
        items = sorted(
            [_action_item(row) for row in _logs_base_query(db, filters).limit(
                LOGS_PAGE_SIZE).all()]
            + [_event_item(row) for row in _events_base_query(db, filters).limit(
                LOGS_PAGE_SIZE).all()],
            key=lambda item: (item["created_at"], item["row_id"]),
            reverse=True)
    else:
        items = [_action_item(row) for row in _logs_base_query(db, filters).all()]
    out = [LOGS_CSV_HEADERS]
    for item in items:
        out.append([
            jalali_str(item["created_at"]),
            item["title"],
            item["actor_name"],
            item["link"][0] if item["link"] else "",
            item["ip"],
            item["detail"],
        ])
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(out)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="logs_{today}.csv"'},
    )


@router.get("/logs/latest")
async def admin_logs_latest(request: Request, db: Session = Depends(get_db)):
    """The newest row id per stream under the active filters — the poller asks
    this, not the page, so a quiet check never renders a thing."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    filters = _logs_filter_values(request, db)
    latest_action = _logs_base_query(db, filters).order_by(
        AdminLog.id.desc()).limit(1).first()
    latest_event = _events_base_query(db, filters).order_by(
        BusinessEvent.id.desc()).limit(1).first()
    return JSONResponse({
        "actions_max": latest_action.id if latest_action else 0,
        "events_max": latest_event.id if latest_event else 0,
    })


@router.post("/logs/archive")
async def admin_logs_archive(request: Request, db: Session = Depends(get_db)):
    """File rows older than a year as CSV into backups/, then delete them.

    Owner-triggered, never automatic: the audit trail shrinks only when the
    owner says so, and the file lands before a single row is deleted.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    import csv
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOGS_ARCHIVE_DAYS)
    old_rows = db.query(AdminLog).options(joinedload(AdminLog.staff_user)).filter(
        AdminLog.created_at < cutoff).order_by(AdminLog.id).all()
    if not old_rows:
        return RedirectResponse(url="/admin/logs?msg=ردیف قدیمی برای بایگانی نیست.",
                                status_code=303)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"logs_archive_{stamp}.csv"
    try:
        with dest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(LOGS_CSV_HEADERS)
            for row in old_rows:
                link = admin_target_link(row.target_type, row.target_id)
                writer.writerow([
                    jalali_str(row.created_at),
                    ADMIN_ACTION_LABELS.get(row.action, row.action),
                    (row.staff_user.full_name or row.staff_user.username)
                    if row.staff_user else "سیستم",
                    link[0] if link else "",
                    row.ip_address or "",
                    row.detail or "",
                ])
    except OSError:
        return RedirectResponse(url="/admin/logs?err=نوشتن فایل بایگانی ناموفق بود.",
                                status_code=303)
    # The file is on disk before any row goes — a failed delete keeps both.
    for row in old_rows:
        db.delete(row)
    db.commit()
    for stale in sorted(BACKUP_DIR.glob("logs_archive_*.csv"))[:-LOGS_ARCHIVE_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass
    log_action(db, "logs_archive", f"بایگانی {len(old_rows)} ردیف قدیمی در {dest.name}",
               request=request, target_type="backup")
    return RedirectResponse(
        url=f"/admin/logs?msg={len(old_rows)} ردیف قدیمی بایگانی شد.",
        status_code=303)


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

    from services.sms import birthday_sms_vars, get_sms_config
    from services.sms_templates import render_text, sms_metrics
    from services.tier import get_customers_for_birthday_check, get_tier_config

    days_before = get_tier_config(db)["birthday_sms_days_before"]
    result = get_customers_for_birthday_check(db, days_before)
    pattern = get_sms_config(db)["birthday_pattern"]
    pattern_ready = bool(pattern)

    rows = []
    for customer, days_until, occasion in result["eligible"]:
        month_day = customer.birth_month_day if occasion == "customer" else customer.child_birthday
        year = customer.birth_year if occasion == "customer" else customer.child_birth_year
        turning = jalali_age(year, month_day)
        body = render_text(pattern, birthday_sms_vars(
            customer.first_name, customer.child_name, occasion)) if pattern else ""
        rows.append({
            "customer": customer,
            "occasion": occasion,
            "occasion_label": BIRTHDAY_SUBJECT_LABELS.get(occasion, occasion),
            "celebrated": (customer.first_name or "مشتری") if occasion == "customer"
                          else (customer.child_name or "فرزند"),
            "date": birthday_display(month_day, year),
            "age": (turning + 1) if turning is not None else None,
            "days_until": days_until,
            "days_label": ("امروز" if days_until == 0
                           else f"{to_persian_digits(str(days_until))} روز دیگر"),
            "already_sent": _birthday_marker(db, customer, occasion) is not None,
            "body": body,
            "segments": sms_metrics(body)["segments"] if body else 0,
        })
    rows.sort(key=lambda row: row["days_until"])

    page_arg_value = (request.query_params.get("page") or "1").strip()
    page = int(page_arg_value) if page_arg_value.isdigit() else 1
    per_page_raw = (request.query_params.get("per_page") or "25").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() and int(per_page_raw) in (10, 25, 50) else 25
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)

    return templates.TemplateResponse(request, "admin/birthdays.html", {
        "rows": rows[(page - 1) * per_page: page * per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "per_page_options": (10, 25, 50),
        "blocked": result["blocked"],
        "blocked_reasons": result["blocked_reasons"],
        "silver": result["silver"],
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
        return RedirectResponse(url="/admin/birthdays?err=" + quote_plus("ابتدا متن پیامک تولد را در صفحه پیامک بنویسید و فعال کنید."),
                                status_code=303)

    wanted = {int(value) for value in customer_ids if str(value).strip().isdigit()}
    if not wanted:
        return RedirectResponse(url="/admin/birthdays?err=" + quote_plus("هیچ مشتری انتخاب نشده است."), status_code=303)

    days_before = get_tier_config(db)["birthday_sms_days_before"]
    eligible = {customer.id: (customer, occasion)
                for customer, _days, occasion in get_customers_for_birthday_check(db, days_before)["eligible"]}

    sent = 0
    skipped = 0
    rejected = 0
    refused = 0
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
        else:
            # The queue said no — test-mode allowlist, or a body that came out
            # empty. Counted apart from the ineligible, so the message adds up.
            refused += 1
    db.commit()
    log_action(db, "birthday_sms", f"{sent} پیامک تولد ارسال شد ({skipped} تکراری، {rejected} خارج از پنجره)",
               request=request, target_type="customer")

    message = f"{sent} پیامک تولد در صف قرار گرفت."
    if skipped:
        message += f" {skipped} مورد قبلاً ارسال شده بود."
    if rejected:
        message += f" {rejected} مورد دیگر در پنجره تولد نیست و رد شد."
    if refused:
        message += f" {refused} مورد در صف نرفت (حالت آزمایشی یا متن خالی)."
    return RedirectResponse(url=f"/admin/birthdays?msg={quote_plus(message)}", status_code=303)


@router.get("/follow-ups", response_class=HTMLResponse)
async def admin_follow_ups(request: Request, db: Session = Depends(get_db)):
    """Review the customers due a follow-up, and send any of them now.

    The sibling of تولد and ارتقای سطح, with one difference the page states out
    loud instead of hiding: those two never send on their own, while a follow-up
    template keeps its own sweep. So this list is a snapshot of who is still
    waiting, not a queue that nothing leaves without — ticking sends now rather
    than at the next pass, and whoever the sweep reached first is simply no
    longer on the list.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms_triggers import SKIPPED_LABELS, auto_send_limit, follow_up_plans, triggered_templates

    per_page_raw = (request.query_params.get("per_page") or "25").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() and int(per_page_raw) in (10, 25, 50) else 25
    pages = {}
    for row in triggered_templates(db, "follow_up"):
        raw = (request.query_params.get(f"page_{row.id}") or "").strip()
        if raw.isdigit():
            pages[row.id] = int(raw)
    plans = follow_up_plans(db, per_page=per_page, pages=pages)
    total_due = sum(plan["total"] for plan in plans)
    return templates.TemplateResponse(request, "admin/follow-ups.html", {
        "plans": plans,
        "total_due": total_due,
        # Counts read as sentence material here, so they arrive as Persian digits
        # rather than being formatted inside the page.
        "total_due_label": to_persian_digits(str(total_due)),
        "plans_count_label": to_persian_digits(str(len(plans))),
        "auto_limit_label": to_persian_digits(str(auto_send_limit(db))),
        "per_page": per_page,
        "per_page_options": (10, 25, 50),
        "skipped_labels": SKIPPED_LABELS,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/follow-ups/send", response_class=HTMLResponse)
async def admin_follow_ups_send(request: Request, template_id: int = Form(0),
                                customer_ids: list[int] = Form([]),
                                db: Session = Depends(get_db)):
    """Queue the follow-ups the owner ticked on the review page.

    Nothing is taken on trust from the form. The template has to be one that is
    still ready to fire, and the customers are looked up again as they stand
    now — so a form left open while the sweep went past cannot message somebody
    twice. Both paths record the same per-purchase reference, and the second to
    arrive loses politely with a count the page reports.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms_triggers import send_follow_ups, triggered_templates

    ready = {row.id: row for row in triggered_templates(db, "follow_up")}
    template = ready.get(int(template_id or 0))
    if template is None:
        return RedirectResponse(
            url="/admin/follow-ups?err=این قالب دیگر برای پیگیری تنظیم یا فعال نیست.",
            status_code=303)

    wanted = [int(value) for value in customer_ids if str(value).strip().isdigit()]
    if not wanted:
        return RedirectResponse(url="/admin/follow-ups?err=هیچ مشتری انتخاب نشده است.",
                                status_code=303)

    result = await send_follow_ups(db, template=template, customer_ids=wanted)
    log_action(db, "follow_up_sms",
               f"{result['sent']} پیامک پیگیری فرستاده شد "
               f"({result['rejected']} مورد دیگر موعدش نبود)",
               request=request, target_type="sms_template", target_id=template.id)

    message = f"{result['sent']} پیامک پیگیری در صف قرار گرفت."
    if result["rejected"]:
        message += (f" {result['rejected']} مورد در این فاصله خودکار فرستاده شده یا دیگر "
                    f"واجد شرایط نیست.")
    if result["empty"]:
        message += f" {result['empty']} مورد متن خالی داشت و در صف نگذاشت."
    return RedirectResponse(url=f"/admin/follow-ups?msg={message}", status_code=303)


@router.get("/tier-downgrades", response_class=HTMLResponse)
async def admin_tier_downgrades(request: Request, db: Session = Depends(get_db)):
    """Who the downgrade rule would take a level from, before anything happens.

    Replaces the dashboard's one-click sweep. That button ran the same check the
    night clock already ran, told nobody who it had touched, and reported «۰»
    when the rule was simply switched off — so this page exists to make the
    decision visible, and to be the only way it is ever made.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    plan = downgrade_candidates(db)
    all_rows = plan["rows"]
    page_arg_value = (request.query_params.get("page") or "1").strip()
    page = int(page_arg_value) if page_arg_value.isdigit() else 1
    per_page_raw = (request.query_params.get("per_page") or "25").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() and int(per_page_raw) in (10, 25, 50) else 25
    total = len(all_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    return templates.TemplateResponse(request, "admin/tier_downgrades.html", {
        "rows": all_rows[(page - 1) * per_page: page * per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "per_page_options": (10, 25, 50),
        "rule": {"months": plan["months"], "enabled": plan["enabled"],
                 "months_label": plan["months_label"]},
        "skipped_archived": plan["skipped_archived"],
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/tier-downgrades/apply", response_class=HTMLResponse)
async def admin_tier_downgrades_apply(
    request: Request, customer_ids: list[int] = Form([]), db: Session = Depends(get_db)
):
    """Demote the customers the owner ticked, and nothing else.

    The ids are the only thing taken from the form: who still qualifies is
    decided again here, because the page can sit open while the shop keeps
    trading, and a purchase in the meantime is exactly what the rule says
    protects the tier.
    """
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    wanted = [int(value) for value in customer_ids if str(value).strip().isdigit()]
    if not wanted:
        return RedirectResponse(
            url="/admin/tier-downgrades?err=هیچ مشتری‌ای انتخاب نشده است.", status_code=303)

    result = apply_tier_downgrades(db, wanted)
    names = "، ".join(row["customer"].full_name for row in result["demoted"][:20])
    log_action(db, "tier_downgrade",
               f"{result['demoted_count']} مشتری کاهش سطح یافتند"
               + (f": {names}" if names else ""),
               request=request, target_type="customer")

    message = f"سطح {result['demoted_count']} مشتری یک پله پایین آمد."
    if result["refused"]:
        message += (f" {result['refused']} مورد در این فاصله خرید کرده یا دیگر واجد شرایط"
                    f" نیست — سطحشان دست‌نخورده ماند.")
    return RedirectResponse(url=f"/admin/tier-downgrades?msg={message}", status_code=303)


TIER_UP_SMS_LIMIT = 10


@router.get("/tier-up", response_class=HTMLResponse)
async def admin_tier_up(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms import get_sms_config
    from services.sms_send import message_block_reason
    from services.sms_templates import render_text, sms_metrics

    customers = sorted(
        tier_up_candidates(db),
        key=lambda c: TIER_RANK[c.tier],
        reverse=True,
    )
    blocked = sum(1 for customer in customers
                  if message_block_reason(customer, transactional=False))
    shown = [customer for customer in customers
             if not message_block_reason(customer, transactional=False)]

    sms_config = get_sms_config(db)
    patterns = {"gold": sms_config["tier_up_gold_pattern"],
                "diamond": sms_config["tier_up_diamond_pattern"]}
    pattern_ready = bool(patterns["gold"] and patterns["diamond"])

    rows = []
    for customer in shown:
        body = render_text(patterns[customer.tier] or "",
                           {"var1": customer.first_name or "مشتری",
                            "var2": str(customer.total_points)})
        rows.append({"customer": customer, "body": body,
                     "segments": sms_metrics(body)["segments"] if body else 0})

    page_arg_value = (request.query_params.get("page") or "1").strip()
    page = int(page_arg_value) if page_arg_value.isdigit() else 1
    per_page_raw = (request.query_params.get("per_page") or "25").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() and int(per_page_raw) in (10, 25, 50) else 25
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)

    return templates.TemplateResponse(request, "admin/tier_up.html", {
        "rows": rows[(page - 1) * per_page: page * per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "per_page_options": (10, 25, 50),
        "blocked": blocked,
        "pattern_ready": pattern_ready,
        "customers": shown,
        "limit": TIER_UP_SMS_LIMIT,
        "sent_msg": request.query_params.get("sent"),
        "skipped_msg": request.query_params.get("skipped"),
        "ineligible_msg": request.query_params.get("ineligible"),
        "refused_msg": request.query_params.get("refused"),
        "err": request.query_params.get("err"),
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/tier-up/send", response_class=HTMLResponse)
async def admin_tier_up_send(request: Request, customer_ids: list[str] = Form([]), db: Session = Depends(get_db)):
    """Send tier-up SMS to selected customers, capped so we never blast >10 at once."""
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard

    from services.sms import get_sms_config, queue_sms
    from services.sms_send import message_block_reason

    sms_config = get_sms_config(db)
    gold_pattern = sms_config["tier_up_gold_pattern"]
    diamond_pattern = sms_config["tier_up_diamond_pattern"]
    if not gold_pattern or not diamond_pattern:
        return RedirectResponse(url="/admin/tier-up?skipped=no_pattern", status_code=303)

    wanted = [int(value) for value in customer_ids if str(value).strip().isdigit()]
    if not wanted:
        return RedirectResponse(url="/admin/tier-up?err=" + quote_plus("هیچ مشتری انتخاب نشده است."),
                                status_code=303)

    sent = 0
    skipped_cap = 0
    skipped_ineligible = 0
    refused = 0
    for customer_id in wanted[:TIER_UP_SMS_LIMIT]:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer or customer.tier == "silver":
            skipped_ineligible += 1
            continue
        if tier_up_sent_rank(db, customer) >= TIER_RANK[customer.tier]:
            skipped_ineligible += 1
            continue
        if message_block_reason(customer, transactional=False):
            skipped_ineligible += 1
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
        else:
            refused += 1

    db.commit()
    log_action(db, "tier_up_sms", f"{sent} پیامک ارتقا ارسال شد", request=request, target_type="customer")

    skipped_cap = max(0, len(wanted) - TIER_UP_SMS_LIMIT)
    params = f"sent={sent}&skipped={skipped_cap}&ineligible={skipped_ineligible}&refused={refused}"
    return RedirectResponse(url=f"/admin/tier-up?{params}", status_code=303)


@router.get("/sales", response_class=HTMLResponse)
async def admin_sales_redirect(request: Request, db: Session = Depends(get_db)):
    """Redirect admin sales to sales list."""
    guard = require_html_role(request, db, "cashier")
    if not hasattr(guard, "role"):
        return guard
    return RedirectResponse(url="/sales/", status_code=303)
