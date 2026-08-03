import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Customer, Product, Sale, SaleItem, generate_referral_code, to_english_digits
from services._common import fmt, get_setting_int as get_discount_setting, parse_persian_birthday
from services.discount import calculate_discounts, apply_discounts_after_sale
from services.tier import update_customer_after_purchase, get_tier_config
from services.invoice import generate_invoice_text, generate_invoice_pdf
from services.sms import send_welcome_sms

router = APIRouter(prefix="/sales")
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def sales_list(
    request: Request,
    search: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(Sale).filter(Sale.payment_confirmed == True)
    
    if search:
        query = query.join(Customer, Sale.customer_id == Customer.id, isouter=True).filter(
            Customer.phone.contains(search) | Customer.first_name.contains(search) | Customer.last_name.contains(search)
        )
    
    sales = query.order_by(Sale.created_at.desc()).limit(100).all()
    
    return templates.TemplateResponse(request, "admin/sales.html", {
        "sales": sales,
        "search": search,
        "fmt": fmt,
    })


@router.get("/new", response_class=HTMLResponse)
async def sales_new(request: Request, db: Session = Depends(get_db)):
    """Start a new sale - enter customer phone."""
    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "customer",
        "basket": [],
        "basket_json": "[]",
        "total_amount": 0,
        "customer": None,
        "fmt": fmt,
    })


@router.post("/lookup-customer", response_class=HTMLResponse)
async def sales_lookup_customer(
    request: Request,
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    """Look up customer by phone, or create new."""
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
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "scan",
            "customer": customer,
            "basket": [],
            "basket_json": "[]",
            "total_amount": 0,
            "tier_config": get_tier_config(db),
            "fmt": fmt,
        })
    
    # Customer not found, show create form
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
    """Create the new customer now — entering name/birthday is commitment. Reuse if exists."""
    phone = to_english_digits(phone.strip())

    existing = db.query(Customer).filter(Customer.phone == phone).first()
    if existing:
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "scan",
            "customer": existing,
            "basket": [],
            "basket_json": "[]",
            "total_amount": 0,
            "tier_config": get_tier_config(db),
            "fmt": fmt,
        })

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

    # Send welcome SMS now (the customer is committed, not "pending")
    await send_welcome_sms(customer.phone, customer.first_name or "", customer.referral_code, db)

    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "scan",
        "customer": customer,
        "basket": [],
        "basket_json": "[]",
        "total_amount": 0,
        "tier_config": get_tier_config(db),
        "fmt": fmt,
    })


@router.post("/add-to-basket", response_class=HTMLResponse)
async def sales_add_to_basket(
    request: Request,
    customer_id: int = Form(...),
    barcode: str = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a product to the basket by barcode."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    barcode = to_english_digits(barcode.strip())
    basket = json.loads(basket_json)
    total_amount = sum(item["total_price"] for item in basket)

    def _scan_step(error: str | None = None, success: str | None = None):
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "scan",
            "customer": customer,
            "basket": basket,
            "basket_json": json.dumps(basket),
            "total_amount": total_amount,
            "error": error,
            "success": success,
            "referrer_code": referrer_code,
            "referrer_phone": referrer_phone,
            "tier_config": get_tier_config(db),
            "fmt": fmt,
        })

    product = db.query(Product).filter(
        Product.barcode == barcode,
        Product.is_active == True
    ).first()
    if not product:
        return _scan_step(error=f"محصولی با بارکد {barcode} یافت نشد.")
    if product.stock_quantity <= 0:
        return _scan_step(error=f"موجودی محصول {product.name} تمام شده است.")

    existing_item = next((it for it in basket if it["product_id"] == product.id), None)
    if existing_item:
        if existing_item["quantity"] >= product.stock_quantity:
            return _scan_step(error=f"موجودی محصول {product.name} کافی نیست.")
        existing_item["quantity"] += 1
        existing_item["total_price"] = existing_item["quantity"] * existing_item["unit_price"]
    else:
        basket.append({
            "product_id": product.id,
            "name": product.name,
            "size": product.size,
            "color": product.color,
            "unit_price": product.price,
            "quantity": 1,
            "total_price": product.price,
            "image_path": product.image_path,
        })

    total_amount = sum(item["total_price"] for item in basket)
    return _scan_step(success=f"محصول {product.name} به سبد خرید اضافه شد.")


@router.post("/remove-from-basket", response_class=HTMLResponse)
async def sales_remove_from_basket(
    request: Request,
    customer_id: int = Form(...),
    product_id: int = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    """Remove a product from the basket."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    basket = [it for it in json.loads(basket_json) if it["product_id"] != int(product_id)]
    total_amount = sum(item["total_price"] for item in basket)

    return templates.TemplateResponse(request, "sales/checkout.html", {
        "step": "scan",
        "customer": customer,
        "basket": basket,
        "basket_json": json.dumps(basket),
        "total_amount": total_amount,
        "referrer_code": referrer_code,
        "referrer_phone": referrer_phone,
        "tier_config": get_tier_config(db),
        "fmt": fmt,
    })


@router.post("/confirm-sale", response_class=HTMLResponse)
async def sales_confirm(
    request: Request,
    customer_id: int = Form(...),
    basket_json: str = Form("[]"),
    referrer_code: str = Form(""),
    referrer_phone: str = Form(""),
    payment_method: str = Form("card"),
    use_referrer_discount: str = Form(""),
    custom_discount_amount: str = Form(""),
    custom_discount_percent: str = Form(""),
    db: Session = Depends(get_db),
):
    """Confirm and complete the sale."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    basket = json.loads(basket_json)
    if not basket:
        return templates.TemplateResponse(request, "sales/checkout.html", {
            "step": "scan",
            "customer": customer,
            "basket": [], "basket_json": "[]", "total_amount": 0,
            "error": "سبد خرید خالی است.",
            "tier_config": get_tier_config(db), "fmt": fmt,
        })

    total_amount = sum(item["total_price"] for item in basket)

    # Resolve referrer (code or phone)
    referrer = None
    if referrer_code:
        referrer = db.query(Customer).filter(
            Customer.referral_code == to_english_digits(referrer_code).upper()
        ).first()
    elif referrer_phone:
        referrer = db.query(Customer).filter(
            Customer.phone == to_english_digits(referrer_phone.strip())
        ).first()

    # Grant the first-buy referred discount the moment a referrer is entered,
    # so it applies to THIS purchase (gated by min_purchase + carry-over).
    # NOTE: do NOT set customer.referred_by here — apply_discounts_after_sale
    # sets it and the Referral row, and its `not customer.referred_by` guard
    # must still see it as unset so the reward fires exactly once.
    if referrer and not customer.referred_by:
        customer.referred_discount = get_discount_setting(db, "default_referred_discount", 30000)

    discounts = calculate_discounts(
        customer,
        total_amount,
        db,
        use_referrer_discount=(use_referrer_discount == "1"),
        custom_amount=int(custom_discount_amount or 0),
        custom_percent=int(custom_discount_percent or 0),
    )

    sale = Sale(
        customer_id=customer.id,
        total_amount=total_amount,
        discount_amount=discounts["total_discount"],
        discount_details=json.dumps(discounts["details"], ensure_ascii=False),
        final_amount=total_amount - discounts["total_discount"],
        payment_method=payment_method,
        payment_confirmed=True,
    )
    db.add(sale)
    db.flush()

    for item in basket:
        product = db.query(Product).filter(Product.id == item["product_id"]).first()
        if not product:
            continue
        db.add(SaleItem(
            sale_id=sale.id,
            product_id=product.id,
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            unit_cost=product.cost_price,
            total_price=item["total_price"],
        ))
        product.stock_quantity -= item["quantity"]

    points_earned = update_customer_after_purchase(customer, sale.final_amount, db)
    sale.points_earned = points_earned
    apply_discounts_after_sale(customer, discounts, db, referrer)

    db.commit()

    sale_items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    for item in sale_items:
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
        "points_earned": points_earned,
    })


@router.get("/invoice/{sale_id}", response_class=HTMLResponse)
async def sales_invoice_view(
    sale_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """View/print an invoice."""
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="فاکتور یافت نشد")
    
    customer = db.query(Customer).filter(Customer.id == sale.customer_id).first() if sale.customer_id else None
    items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    
    for item in items:
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
        "points_earned": sale.points_earned,
    })


@router.get("/api/barcode/{barcode}", response_class=JSONResponse)
async def api_lookup_barcode(barcode: str, db: Session = Depends(get_db)):
    """API endpoint to lookup a product by barcode."""
    barcode = to_english_digits(barcode.strip())
    
    product = db.query(Product).filter(
        Product.barcode == barcode,
        Product.is_active == True
    ).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")
    
    return {
        "id": product.id,
        "barcode": product.barcode,
        "name": product.name,
        "price": product.price,
        "stock_quantity": product.stock_quantity,
        "size": product.size,
        "color": product.color,
        "image_path": product.image_path,
    }


@router.post("/{sale_id}/refund", response_class=HTMLResponse)
async def sale_refund(
    sale_id: int,
    request: Request,
    refund_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    """Refund/void a sale."""
    from datetime import datetime, timezone
    
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="فاکتور یافت نشد")
    
    if sale.is_refunded:
        return RedirectResponse(url=f"/sales/invoice/{sale_id}", status_code=303)
    
    # Mark as refunded
    sale.is_refunded = True
    sale.refund_amount = sale.final_amount
    sale.refund_reason = refund_reason if refund_reason else "ابطال فاکتور"
    sale.refund_date = datetime.now(timezone.utc)
    
    # Restore stock
    sale_items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
    for item in sale_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            product.stock_quantity += item.quantity
    
    # Reverse customer stats
    if sale.customer_id:
        customer = db.query(Customer).filter(Customer.id == sale.customer_id).first()
        if customer:
            customer.total_points = max(0, customer.total_points - sale.points_earned)
            customer.total_purchases = max(0, customer.total_purchases - 1)
            customer.total_spent = max(0, customer.total_spent - sale.final_amount)
    
    db.commit()
    
    return RedirectResponse(url=f"/sales/invoice/{sale_id}", status_code=303)
