import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Customer, Product, ProductVariant, Sale, SaleItem, SaleCampaign,
    Referral, Settings, POSTransaction, generate_referral_code, to_english_digits,
)
from services._common import fmt, get_setting_int as get_discount_setting, parse_persian_birthday, jalali_str
from services.accounting import credit_sale_allowed, apply_credit_surcharge
from services.discount import calculate_discounts, apply_discounts_after_sale
from services.security import log_action
from services.tier import (
    update_customer_after_purchase, get_tier_config, check_tier_upgrade,
    tier_up_marker_key, TIER_RANK,
)
from services.invoice import generate_invoice_text, generate_invoice_pdf
from services.pos_terminal import (
    send_sale as send_terminal_sale,
    get_terminal_config,
    check_connection as check_terminal_connection,
)
from services.inventory import record_stock_movement


POS_APPROVAL_SESSION_KEY = "pos_approval"
POS_APPROVAL_MAX_AGE_SECONDS = 15 * 60
ALLOWED_PAYMENT_METHODS = {"card", "cash", "credit"}


def _clear_pos_approval(request: Request) -> None:
    request.session.pop(POS_APPROVAL_SESSION_KEY, None)


def _approval_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_pos_approval(request: Request, db: Session, amount: int, token: str, checkout_nonce: str = "") -> POSTransaction | None:
    approval = request.session.get(POS_APPROVAL_SESSION_KEY)
    if not isinstance(approval, dict) or not token:
        return None
    if approval.get("token") != token or approval.get("status") != "approved":
        return None
    try:
        is_fresh = time.time() - float(approval.get("created_at", 0)) <= POS_APPROVAL_MAX_AGE_SECONDS
    except (TypeError, ValueError):
        is_fresh = False
    if (
        not is_fresh
        or int(approval.get("amount", -1)) != int(amount)
        or approval.get("checkout_nonce", "") != checkout_nonce
    ):
        return None

    transaction_id = approval.get("transaction_id")
    transaction = db.query(POSTransaction).filter(POSTransaction.id == transaction_id).first()
    if not transaction or transaction.status != "approved" or transaction.sale_id:
        return None
    if not hmac.compare_digest(transaction.approval_token_hash or "", _approval_hash(token)):
        return None
    if transaction.amount != int(amount) or transaction.checkout_nonce != checkout_nonce:
        return None
    return transaction


def _transaction_response(transaction: POSTransaction, approval_token: str | None = None) -> dict:
    result = {
        "ok": transaction.status == "approved",
        "status": transaction.status,
        "label": transaction.response_label or transaction.error_message or "Unknown response",
        "response_code": transaction.response_code,
        "transaction_id": transaction.id,
    }
    if approval_token:
        result["approval_token"] = approval_token
    if transaction.sale_id:
        result["sale_id"] = transaction.sale_id
    return result


def _reuse_pos_transaction(request: Request, db: Session, transaction: POSTransaction, amount: int):
    """Return a prior nonce's result without contacting the terminal again."""
    if transaction.amount != int(amount):
        return JSONResponse(status_code=409, content={
            "detail": "این سبد قبلاً با مبلغ دیگری برای کارت‌خوان ارسال شده است.",
            "transaction_id": transaction.id,
        })
    if transaction.status in {"sent", "uncertain"}:
        return JSONResponse(status_code=409, content={
            "detail": "وضعیت این تراکنش نامشخص است؛ ابتدا آن را در صفحه تطبیق کارت‌خوان بررسی کنید.",
            "status": transaction.status,
            "transaction_id": transaction.id,
        })
    if transaction.status == "approved" and not transaction.sale_id:
        approval_token = secrets.token_urlsafe(32)
        transaction.approval_token_hash = _approval_hash(approval_token)
        db.commit()
        request.session[POS_APPROVAL_SESSION_KEY] = {
            "token": approval_token,
            "status": "approved",
            "amount": transaction.amount,
            "checkout_nonce": transaction.checkout_nonce,
            "transaction_id": transaction.id,
            "response_code": transaction.response_code,
            "created_at": time.time(),
        }
        return _transaction_response(transaction, approval_token)
    return _transaction_response(transaction)


def _discount_int(v: str) -> int:
    """Tolerant parse for client-supplied discount strings: empty/non-numeric -> 0."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _resolve_referrer(referrer_code: str, referrer_phone: str, db):
    """Resolve a referrer from referral code (preferred) or phone."""
    if referrer_code:
        return db.query(Customer).filter(
            Customer.referral_code == to_english_digits(referrer_code).upper()
        ).first()
    elif referrer_phone:
        return db.query(Customer).filter(
            Customer.phone == to_english_digits(referrer_phone.strip())
        ).first()
    return None


def _grant_referred_discount(referrer, customer, db) -> None:
    if referrer and not customer.referred_by and referrer.id != customer.id:
        customer.referred_discount = get_discount_setting(db, "default_referred_discount", 30000)


from services.sms import send_welcome_sms
from services.templating import templates

router = APIRouter(prefix="/sales")


def _resolve_customer(customer_id: int, db) -> Customer | None:
    """Fetch the customer for a form's customer_id; 0/None means anonymous."""
    if not customer_id:
        return None
    return db.query(Customer).filter(Customer.id == customer_id).first()


def _render_scan(request, customer, basket, total_amount, db,
                 referrer_code="", referrer_phone="",
                 use_referrer_discount="1", custom_discount_amount=0,
                 custom_discount_percent=0, error=None, success=None):
    checkout_nonce = secrets.token_urlsafe(18)
    if customer:
        _grant_referred_discount(_resolve_referrer(referrer_code, referrer_phone, db), customer, db)
    discounts = calculate_discounts(
        customer, total_amount, db,
        use_referrer_discount=(use_referrer_discount == "1"),
        custom_amount=custom_discount_amount,
        custom_percent=custom_discount_percent,
    )
    # Owner-only profit meter: gross margin minus everything already discounted.
    gross_profit = sum(
        (it.get("unit_price", 0) - it.get("unit_cost", 0)) * it.get("quantity", 0)
        for it in basket
    )
    net_profit = gross_profit - discounts["total_discount"]
    # Credit-limit hint for the نسیه radio: would this basket exceed the cap?
    # Uses the surcharge-inclusive amount, since that's what the customer owes.
    pos_config = get_terminal_config(db)
    credit_limit_info = None
    if customer:
        surcharge, credit_final = apply_credit_surcharge(db, total_amount, discounts["total_discount"])
        allowed, limit = credit_sale_allowed(db, customer, credit_final)
        credit_limit_info = {
            "limit": limit,
            "debt": customer.total_debt or 0,
            "blocked": not allowed,
            "surcharge_amount": surcharge,
            "surcharge_percent": get_discount_setting(db, "credit_surcharge_percent", 0),
        }
    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "scan",
        "customer": customer,
        "customer_id": customer.id if customer else 0,
        "checkout_nonce": checkout_nonce,
        "basket": basket,
        "basket_json": json.dumps(basket),
        "total_amount": total_amount,
        "tier_config": get_tier_config(db),
        "referrer_code": referrer_code,
        "referrer_phone": referrer_phone,
        "use_referrer_discount": use_referrer_discount,
        "custom_discount_amount": custom_discount_amount,
        "custom_discount_percent": custom_discount_percent,
        "discounts": discounts,
        "net_profit": net_profit,
        "credit_limit_info": credit_limit_info,
        # A saved POS configuration enables the strict approval gate. Before
        # setup, card remains available as the app's existing manual-card
        # fallback; the terminal button can still use the default address.
        "pos_requires_approval": pos_config["configured"],
        "error": error,
        "success": success,
        "fmt": fmt,
    })


@router.get("/", response_class=HTMLResponse)
async def sales_list(request: Request, search: str = "", page: int = 1, db: Session = Depends(get_db)):
    query = db.query(Sale).filter(Sale.payment_confirmed == True)

    if search:
        query = query.join(Customer, Sale.customer_id == Customer.id, isouter=True).filter(
            Customer.phone.contains(search) | Customer.first_name.contains(search) | Customer.last_name.contains(search)
        )

    per_page = 15
    total = query.count()
    sales = query.order_by(Sale.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)

    return templates.TemplateResponse(request, "admin/sales.html", {
        "sales": sales,
        "search": search,
        "page": page,
        "total_pages": total_pages,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/new", response_class=HTMLResponse)
async def sales_new(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "customer",
        "basket": [],
        "basket_json": "[]",
        "total_amount": 0,
        "customer": None,
        "fmt": fmt,
    })


@router.post("/skip-customer", response_class=HTMLResponse)
async def sales_skip_customer(request: Request, db: Session = Depends(get_db)):
    """Start a sale without a phone number — anonymous walk-in (no Customer row)."""
    return _render_scan(request, None, [], 0, db)


@router.post("/lookup-customer", response_class=HTMLResponse)
async def sales_lookup_customer(request: Request, phone: str = Form(""), db: Session = Depends(get_db)):
    phone = to_english_digits(phone.strip())

    if not phone or not phone.startswith("09") or len(phone) != 11:
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "customer",
            "error": "شماره موبایل نامعتبر است.",
            "basket": [],
            "basket_json": "[]",
            "total_amount": 0,
            "fmt": fmt,
        })

    customer = db.query(Customer).filter(Customer.phone == phone).first()

    if customer:
        return _render_scan(request, customer, [], 0, db)

    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "create_customer",
        "phone": phone,
        "basket": [],
        "basket_json": "[]",
        "total_amount": 0,
        "fmt": fmt,
    })


def _parse_persian_birthday(value: str) -> str | None:
    return parse_persian_birthday(value)


@router.post("/create-customer", response_class=HTMLResponse)
async def sales_create_customer(
    request: Request,
    phone: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    child_name: str = Form(""),
    child_birthday: str = Form(""),
    db: Session = Depends(get_db),
):
    phone = to_english_digits(phone.strip())

    existing = db.query(Customer).filter(Customer.phone == phone).first()
    if existing:
        return _render_scan(request, existing, [], 0, db)

    if not phone.startswith("09") or len(phone) != 11:
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "customer",
            "error": "شماره موبایل نامعتبر است.",
            "basket": [], "basket_json": "[]", "total_amount": 0, "fmt": fmt,
        })

    code = generate_referral_code()
    while db.query(Customer).filter(Customer.referral_code == code).first():
        code = generate_referral_code()

    customer = Customer(
        phone=phone,
        first_name=first_name or None,
        last_name=last_name or None,
        referral_code=code,
        child_name=child_name or None,
        child_birthday=_parse_persian_birthday(child_birthday),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    await send_welcome_sms(customer.phone, customer.first_name or "", customer.referral_code, db)

    return _render_scan(request, customer, [], 0, db)


@router.post("/apply-discount", response_class=HTMLResponse)
async def sales_apply_discount(
    request: Request,
    customer_id: int = Form(0),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    use_referrer_discount: str = Form("1"),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
    customer = _resolve_customer(customer_id, db)

    basket = json.loads(basket_json)
    total_amount = sum(item["total_price"] for item in basket)

    return _render_scan(
        request, customer, basket, total_amount, db,
        referrer_code=referrer_code,
        referrer_phone=referrer_phone,
        use_referrer_discount=use_referrer_discount,
        custom_discount_amount=_discount_int(custom_discount_amount),
        custom_discount_percent=_discount_int(custom_discount_percent),
    )


@router.post("/add-to-basket", response_class=HTMLResponse)
async def sales_add_to_basket(
    request: Request,
    customer_id: int = Form(0),
    barcode: str = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    use_referrer_discount: str = Form("1"),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a product to the basket by barcode - looks up variant."""
    customer = _resolve_customer(customer_id, db)

    barcode = to_english_digits(barcode.strip())
    basket = json.loads(basket_json)
    total_amount = sum(item["total_price"] for item in basket)

    def _scan_step(error: str | None = None, success: str | None = None):
        return _render_scan(
            request, customer, basket, total_amount, db,
            referrer_code=referrer_code,
            referrer_phone=referrer_phone,
            use_referrer_discount=use_referrer_discount,
            custom_discount_amount=_discount_int(custom_discount_amount),
            custom_discount_percent=_discount_int(custom_discount_percent),
            error=error,
            success=success,
        )

    # Look up variant by barcode (variants have unique barcodes)
    variant = db.query(ProductVariant).filter(
        ProductVariant.barcode == barcode,
        ProductVariant.is_active == True
    ).first()

    if not variant:
        return _scan_step(error=f"محصولی با بارکد {barcode} یافت نشد.")
    if variant.stock_quantity <= 0:
        return _scan_step(error=f"موجودی {variant.display_name} تمام شده است.")

    product = variant.product

    # Check if this variant is already in basket
    existing_item = next((it for it in basket if it["variant_id"] == variant.id), None)
    if existing_item:
        if existing_item["quantity"] >= variant.stock_quantity:
            return _scan_step(error=f"موجودی {variant.display_name} کافی نیست.")
        existing_item["quantity"] += 1
        existing_item["total_price"] = existing_item["quantity"] * existing_item["unit_price"]
    else:
        basket.append({
            "variant_id": variant.id,
            "product_id": product.id,
            "name": variant.display_name,
            "unit_price": variant.price,
            "unit_cost": variant.cost_price,
            "quantity": 1,
            "total_price": variant.price,
            "image_path": variant.image_path or product.image_path,
        })

    total_amount = sum(item["total_price"] for item in basket)
    return _scan_step(success=f"محصول {variant.display_name} به سبد خرید اضافه شد.")


@router.post("/remove-from-basket", response_class=HTMLResponse)
async def sales_remove_from_basket(
    request: Request,
    customer_id: int = Form(0),
    variant_id: int = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    use_referrer_discount: str = Form("1"),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
    """Remove a variant from the basket."""
    customer = _resolve_customer(customer_id, db)

    basket = [it for it in json.loads(basket_json) if it["variant_id"] != int(variant_id)]
    total_amount = sum(item["total_price"] for item in basket)

    return _render_scan(
        request, customer, basket, total_amount, db,
        referrer_code=referrer_code,
        referrer_phone=referrer_phone,
        use_referrer_discount=use_referrer_discount,
        custom_discount_amount=_discount_int(custom_discount_amount),
        custom_discount_percent=_discount_int(custom_discount_percent),
    )


@router.get("/terminal-status", response_class=JSONResponse)
async def sales_terminal_status(db: Session = Depends(get_db)):
    """Live terminal reachability for the checkout indicator. Read-only TCP
    probe — never sends a payment amount, safe to poll."""
    cfg = get_terminal_config(db)
    if not cfg["enabled"]:
        return {**cfg, "online": False, "latency_ms": None}
    conn = await asyncio.to_thread(check_terminal_connection, cfg["host"], cfg["port"])
    return {**cfg, **conn}


@router.post("/send-to-terminal", response_class=JSONResponse)
async def sales_send_to_terminal(
    request: Request,
    amount: int = Form(...),
    checkout_nonce: str = Form(""),
    customer_id: int = Form(0),
    basket_json: str = Form("[]"),
    db: Session = Depends(get_db),
):
    """Start one durable, idempotent Parsian sale attempt.

    The row is committed before network I/O. A retry with the same checkout
    nonce returns the stored result and never sends a second terminal charge.
    Only an approved row receives a session capability for ``confirm-sale``.
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="مبلغ نامعتبر است.")
    if not checkout_nonce or len(checkout_nonce) > 100:
        raise HTTPException(status_code=400, detail="شناسه سبد خرید نامعتبر است.")

    cfg = get_terminal_config(db)
    if not cfg["enabled"]:
        return JSONResponse(status_code=400, content={
            "detail": "کارت‌خوان در تنظیمات پیکربندی نشده است (تنظیمات ← کارت‌خوان).",
            "terminal": cfg,
        })

    existing = db.query(POSTransaction).filter(
        POSTransaction.checkout_nonce == checkout_nonce,
    ).first()
    if existing:
        return _reuse_pos_transaction(request, db, existing, amount)

    snapshot = basket_json if len(basket_json) <= 10000 else basket_json[:10000]
    transaction = POSTransaction(
        checkout_nonce=checkout_nonce,
        customer_id=customer_id or None,
        amount=int(amount),
        host=cfg["host"],
        port=cfg["port"],
        status="sent",
        basket_snapshot=snapshot,
    )
    db.add(transaction)
    try:
        db.commit()  # Durable before the terminal can approve the payment.
    except IntegrityError:
        # Another request may have won the unique checkout_nonce race.
        db.rollback()
        existing = db.query(POSTransaction).filter(
            POSTransaction.checkout_nonce == checkout_nonce,
        ).first()
        if existing:
            return _reuse_pos_transaction(request, db, existing, amount)
        raise
    db.refresh(transaction)
    _clear_pos_approval(request)

    try:
        result = await asyncio.to_thread(
            send_terminal_sale, cfg["host"], cfg["port"], amount,
        )
    except Exception as error:
        transaction.status = "uncertain"
        transaction.error_message = str(error)[:500]
        transaction.response_label = "نیازمند تطبیق دستی"
        db.commit()
        return JSONResponse(status_code=502, content={
            "detail": str(error) or "ارتباط با دستگاه کارت‌خوان ناموفق بود.",
            "status": "uncertain",
            "transaction_id": transaction.id,
            "terminal": {"host": cfg["host"], "port": cfg["port"]},
        })

    transaction.status = result["status"]
    transaction.response_code = result.get("response_code")
    transaction.response_label = result.get("label")
    transaction.response_text = result.get("response")
    db.commit()

    if transaction.status != "approved":
        return _transaction_response(transaction)

    approval_token = secrets.token_urlsafe(32)
    transaction.approval_token_hash = _approval_hash(approval_token)
    db.commit()
    request.session[POS_APPROVAL_SESSION_KEY] = {
        "token": approval_token,
        "status": "approved",
        "amount": transaction.amount,
        "checkout_nonce": transaction.checkout_nonce,
        "transaction_id": transaction.id,
        "response_code": transaction.response_code,
        "created_at": time.time(),
    }
    return _transaction_response(transaction, approval_token)


@router.post("/confirm-sale", response_class=HTMLResponse)
async def sales_confirm(
    request: Request,
    customer_id: int = Form(0),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    payment_method: str = Form("card"),
    pos_approval_token: str = Form(""),
    checkout_nonce: str = Form(""),
    use_referrer_discount: str = Form(""),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
    """Confirm and complete the sale.

    Integrity: prices, quantities and totals are recomputed from the database
    here — the client-supplied basket_json is treated as an untrusted list of
    variant IDs, never as a source of price."""
    if payment_method not in ALLOWED_PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="روش پرداخت نامعتبر است.")

    customer = _resolve_customer(customer_id, db)

    raw_basket = json.loads(basket_json)
    if not raw_basket:
        return _render_scan(request, customer, [], 0, db, error="سبد خرید خالی است.")

    # Credit sales (نسیه) need a registered customer to owe the money.
    if payment_method == "credit" and customer is None:
        return _render_scan(request, customer, [], 0, db,
                            error="فروش نسیه فقط برای مشتری ثبت‌شده ممکن است — ابتدا شماره مشتری را جستجو کنید.")

    # Rebuild the basket from the database: current price, real cost, and stock-
    # clamped quantity. Anything the client sent for price/quantity is ignored.
    verified_basket = []
    for item in raw_basket:
        try:
            variant_id = int(item.get("variant_id"))
            requested_qty = max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            continue
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue
        qty = min(requested_qty, variant.stock_quantity)
        if qty <= 0:
            continue
        unit_price = variant.price
        verified_basket.append({
            "variant_id": variant.id,
            "product_id": variant.product_id,
            "name": variant.display_name,
            "unit_price": unit_price,
            "unit_cost": variant.cost_price,
            "quantity": qty,
            "total_price": unit_price * qty,
        })

    if not verified_basket:
        return _render_scan(request, customer, [], 0, db, error="هیچ محصول معتبری در سبد نیست.")

    total_amount = sum(item["total_price"] for item in verified_basket)
    basket = verified_basket

    # Resolve referrer and grant referred discount — only for a known customer.
    referrer = None
    if customer:
        referrer = _resolve_referrer(referrer_code, referrer_phone, db)
        _grant_referred_discount(referrer, customer, db)

    discounts = calculate_discounts(
        customer,
        total_amount,
        db,
        use_referrer_discount=(use_referrer_discount == "1"),
        custom_amount=_discount_int(custom_discount_amount),
        custom_percent=_discount_int(custom_discount_percent),
    )

    # نسیه policy: no discounts apply on credit sales, and a configurable
    # surcharge percent is added to the subtotal instead. Zero out every
    # discount line so the ledger and invoice reflect the surcharge-only total.
    credit_surcharge = 0
    if payment_method == "credit":
        for k in ("referred_discount", "referrer_discount", "tier_discount",
                 "birthday_discount", "custom_discount"):
            discounts[k] = 0
        discounts["details"] = ["فروش نسیه: تخفیف اعمال نمی‌شود"]
        discounts["total_discount"] = 0
        credit_surcharge, final_amount = apply_credit_surcharge(db, total_amount, 0)
        discounts["details"].append(f"افزایش نسیه: {credit_surcharge:,} تومان")
    else:
        final_amount = total_amount - discounts["total_discount"]

    # Card sales must be backed by an approved response from the tested
    # Parsian protocol. Sending a payload or merely reaching the terminal is
    # never enough to create a paid sale.
    approval = None
    if payment_method == "card":
        pos_config = get_terminal_config(db)
        approval = _get_pos_approval(request, db, final_amount, pos_approval_token, checkout_nonce)
        if pos_config["configured"] and approval is None:
            return _render_scan(
                request, customer, basket, total_amount, db,
                referrer_code=referrer_code,
                referrer_phone=referrer_phone,
                use_referrer_discount=use_referrer_discount,
                custom_discount_amount=_discount_int(custom_discount_amount),
                custom_discount_percent=_discount_int(custom_discount_percent),
                error="پرداخت کارت تأیید نشده است — ابتدا مبلغ را به کارت‌خوان ارسال کنید و نتیجه «تأیید شد» بگیرید.",
            )

    # نسیه: block the sale when it would push the customer past their credit limit.
    if payment_method == "credit" and customer:
        allowed, limit = credit_sale_allowed(db, customer, final_amount)
        if not allowed:
            return _render_scan(
                request, customer, basket, total_amount, db,
                referrer_code=referrer_code,
                referrer_phone=referrer_phone,
                use_referrer_discount=use_referrer_discount,
                custom_discount_amount=_discount_int(custom_discount_amount),
                custom_discount_percent=_discount_int(custom_discount_percent),
                error=(f"سقف اعتبار این مشتری {fmt(limit)} تومان است و بدهی فعلی {fmt(customer.total_debt or 0)} تومان — "
                       f"این خرید ({fmt(final_amount)} تومان) از سقف رد می‌شود. روش پرداخت را عوض کنید یا سقف را بالا ببرید."),
            )

    sale = Sale(
        customer_id=customer.id if customer else None,
        total_amount=total_amount,
        discount_amount=discounts["total_discount"],
        discount_details=json.dumps(discounts["details"], ensure_ascii=False),
        final_amount=final_amount,
        payment_method=payment_method,
        pos_transaction=approval if payment_method == "card" else None,
        payment_confirmed=True,
        credit_settled=False,
        credit_paid_amount=0,
    )
    # Persist the نسیه surcharge on the sale row for the ledger/invoice.
    if credit_surcharge:
        sale.credit_surcharge = credit_surcharge
    db.add(sale)
    db.flush()

    # نسیه: the customer now owes final_amount.
    if payment_method == "credit" and customer:
        customer.total_debt = (customer.total_debt or 0) + sale.final_amount

    for item in basket:
        variant = db.query(ProductVariant).filter(ProductVariant.id == item["variant_id"]).first()
        if not variant:
            continue

        db.add(SaleItem(
            sale_id=sale.id,
            product_id=item["product_id"],
            variant_id=variant.id,
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            unit_cost=variant.cost_price,
            total_price=item["total_price"],
        ))
        record_stock_movement(
            db, variant, -item["quantity"], "sale",
            sale_id=sale.id,
            note=f"فروش فاکتور #{sale.id}",
        )

    points_earned = 0
    if customer:
        points_earned = update_customer_after_purchase(customer, sale.final_amount, db)
        sale.points_earned = points_earned
        apply_discounts_after_sale(customer, discounts, db, referrer)

    if approval is not None:
        approval.status = "linked_to_sale"
        approval.reconciled = True
        approval.reconciled_at = datetime.now(timezone.utc)
    db.commit()
    # Make the terminal approval one-time-use. Cash/credit sales also clear an
    # older approval so it cannot accidentally authorize a later card sale.
    _clear_pos_approval(request)

    sale_items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    for item in sale_items:
        item.variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        item.product = db.query(Product).filter(Product.id == item.product_id).first()

    invoice_path = generate_invoice_pdf(sale, customer, sale_items)
    invoice_text = generate_invoice_text(sale, customer, sale_items)

    return templates.TemplateResponse(request, "sales/invoice.html", {
        "sale": sale,
        "customer": customer,
        "items": sale_items,
        "discounts": discounts,
        "invoice_path": invoice_path,
        "invoice_text": invoice_text,
        "fmt": fmt,
        "jalali_str": jalali_str,
        "points_earned": points_earned,
    })


@router.get("/invoice/{sale_id}", response_class=HTMLResponse)
async def sales_invoice_view(sale_id: int, request: Request, db: Session = Depends(get_db)):
    """View/print an invoice."""
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="فاکتور یافت نشد")

    customer = db.query(Customer).filter(Customer.id == sale.customer_id).first() if sale.customer_id else None
    items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()

    for item in items:
        item.variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        item.product = db.query(Product).filter(Product.id == item.product_id).first()

    invoice_path = generate_invoice_pdf(sale, customer, items)
    invoice_text = generate_invoice_text(sale, customer, items)

    discounts = {}
    if sale.discount_amount > 0 and sale.discount_details:
        discounts = {
            "total_discount": sale.discount_amount,
            "details": json.loads(sale.discount_details),
        }

    return templates.TemplateResponse(request, "sales/invoice.html", {
        "sale": sale,
        "customer": customer,
        "items": items,
        "discounts": discounts,
        "invoice_path": invoice_path,
        "invoice_text": invoice_text,
        "fmt": fmt,
        "jalali_str": jalali_str,
        "points_earned": sale.points_earned,
    })


@router.get("/api/barcode/{barcode}", response_class=JSONResponse)
async def api_lookup_barcode(barcode: str, db: Session = Depends(get_db)):
    """API endpoint to lookup a product variant by barcode."""
    barcode = to_english_digits(barcode.strip())

    variant = db.query(ProductVariant).filter(
        ProductVariant.barcode == barcode,
        ProductVariant.is_active == True
    ).first()

    if not variant:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    return {
        "variant_id": variant.id,
        "product_id": variant.product_id,
        "barcode": variant.barcode,
        "name": variant.display_name,
        "price": variant.price,
        "stock_quantity": variant.stock_quantity,
        "size": variant.size,
        "color": variant.color,
        "image_path": variant.image_path or variant.product.image_path,
        "product_name": variant.product.name,
    }


@router.post("/{sale_id}/refund", response_class=HTMLResponse)
async def sale_refund(sale_id: int, request: Request, refund_reason: str = Form(""), db: Session = Depends(get_db)):
    """Refund/void a sale."""
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="فاکتور یافت نشد")

    if sale.is_refunded:
        return RedirectResponse(url=f"/sales/invoice/{sale_id}", status_code=303)

    sale.is_refunded = True
    sale.refund_amount = sale.final_amount
    sale.refund_reason = refund_reason if refund_reason else "ابطال فاکتور"
    sale.refund_date = datetime.now(timezone.utc)

    # Restore stock to variants
    sale_items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    for item in sale_items:
        variant = db.query(ProductVariant).filter(ProductVariant.id == item.variant_id).first()
        if variant:
            record_stock_movement(
                db, variant, item.quantity, "sale_refund",
                sale_id=sale.id,
                note=f"برگشت موجودی فاکتور #{sale.id}",
            )

    # Reverse customer stats
    if sale.customer_id:
        customer = db.query(Customer).filter(Customer.id == sale.customer_id).first()
        if customer:
            customer.total_points = max(0, customer.total_points - sale.points_earned)
            customer.total_purchases = max(0, customer.total_purchases - 1)
            customer.total_spent = max(0, customer.total_spent - sale.final_amount)

            # Re-flag the referred discount if this sale consumed it, so the
            # customer can use it on a future purchase.
            details = []
            if sale.discount_details:
                try:
                    details = json.loads(sale.discount_details) or []
                except (ValueError, TypeError):
                    details = []
            referred_applied = any(
                str(d).startswith("تخفیف معرفی:") or ("تخفیف معرفی" in str(d) and "دیگران" not in str(d))
                for d in details
            )
            if referred_applied and customer.has_used_referred_discount:
                customer.has_used_referred_discount = False

            # Void the referral this sale established (if any): remove the
            # Referral row, unlink the customer, and decrement the referrer's
            # active count. Already-spent referrer credit is NOT clawed back.
            if referred_applied and customer.referred_by:
                referral = db.query(Referral).filter(
                    Referral.referred_id == customer.id,
                    Referral.referrer_id == customer.referred_by,
                ).first()
                if referral:
                    referrer = db.query(Customer).filter(Customer.id == referral.referrer_id).first()
                    if referrer:
                        referrer.active_referral_count = max(0, (referrer.active_referral_count or 0) - 1)
                    db.delete(referral)
                    customer.referred_by = None

            # نسیه: a refund cancels the remaining unpaid debt (payments already
            # received stay in the ledger; the owner refunds cash separately).
            if sale.payment_method == "credit":
                remaining = sale.final_amount - (sale.credit_paid_amount or 0)
                customer.total_debt = max(0, (customer.total_debt or 0) - remaining)

            # Recompute tier after the points reversal (points may have been the
            # only thing holding gold/diamond). Clear the tier-up SMS marker if
            # the customer dropped a tier, so climbing back re-queues them.
            config = get_tier_config(db)
            new_tier = check_tier_upgrade(customer, config)
            if new_tier != customer.tier:
                customer.tier = new_tier
                marker = db.query(Settings).filter(Settings.key == tier_up_marker_key(customer.id)).first()
                if marker and TIER_RANK.get(new_tier, 0) < TIER_RANK.get(marker.value or "silver", 0):
                    db.delete(marker)

    # Remove campaign-discount links for the voided sale.
    db.query(SaleCampaign).filter(SaleCampaign.sale_id == sale.id).delete()

    db.commit()
    log_action(db, "refund", f"ابطال فاکتور #{sale.id}")

    return RedirectResponse(url=f"/sales/invoice/{sale_id}", status_code=303)