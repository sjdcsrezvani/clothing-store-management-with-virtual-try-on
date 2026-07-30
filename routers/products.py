import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Product, Settings, generate_barcode, to_english_digits
from services._common import fmt, check_admin
from services.barcode import generate_barcode_image, generate_barcode_number

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/products", response_class=HTMLResponse)
async def admin_products(
    request: Request,
    search: str = "",
    category: str = "",
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    query = db.query(Product).filter(Product.is_active == True)
    
    if search:
        query = query.filter(
            Product.name.contains(search) |
            Product.barcode.contains(search) |
            Product.sku.contains(search)
        )
    
    if category:
        query = query.filter(Product.category == category)
    
    products = query.order_by(Product.created_at.desc()).all()
    
    # Get unique categories for filter
    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    return templates.TemplateResponse(request, "admin/products.html", {
        "products": products,
        "search": search,
        "category_filter": category,
        "categories": categories,
        "fmt": fmt,
    })


@router.get("/products/add", response_class=HTMLResponse)
async def admin_product_add_form(request: Request):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    return templates.TemplateResponse(request, "admin/product_form.html", {
        "product": None,
        "edit_mode": False,
    })


@router.post("/products/add", response_class=HTMLResponse)
async def admin_product_add(
    request: Request,
    name: str = Form(...),
    price: str = Form(...),
    cost_price: str = Form("0"),
    stock_quantity: str = Form("0"),
    size: str = Form(""),
    color: str = Form(""),
    category: str = Form(""),
    brand: str = Form(""),
    sku: str = Form(""),
    description: str = Form(""),
    barcode: str = Form(""),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    # Parse price
    try:
        price_int = int(to_english_digits(price))
    except ValueError:
        price_int = 0

    # Parse cost_price
    try:
        cost_price_int = int(to_english_digits(cost_price))
    except ValueError:
        cost_price_int = 0

    # Parse stock
    try:
        stock_int = int(to_english_digits(stock_quantity))
    except ValueError:
        stock_int = 0

    # Generate or use provided barcode
    if not barcode:
        barcode = generate_barcode_number()
        while db.query(Product).filter(Product.barcode == barcode).first():
            barcode = generate_barcode_number()
    else:
        barcode = to_english_digits(barcode.strip())
        existing = db.query(Product).filter(Product.barcode == barcode).first()
        if existing:
            return templates.TemplateResponse(request, "admin/product_form.html", {
                "product": None,
                "edit_mode": False,
                "error": "بارکد تکراری است.",
            })

    # Handle image upload
    image_path = None
    if image and image.filename:
        upload_dir = Path("static/uploads/products")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        ext = Path(image.filename).suffix or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = upload_dir / filename
        
        with open(filepath, "wb") as f:
            content = await image.read()
            f.write(content)
        
        image_path = f"/static/uploads/products/{filename}"

    # Generate barcode image
    barcode_image_path = generate_barcode_image(barcode, name)

    # Create product
    product = Product(
        barcode=barcode,
        name=name,
        price=price_int,
        cost_price=cost_price_int,
        stock_quantity=stock_int,
        size=size if size else None,
        color=color if color else None,
        category=category if category else None,
        brand=brand if brand else None,
        sku=sku if sku else None,
        description=description if description else None,
        image_path=image_path or barcode_image_path,
    )
    db.add(product)
    db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def admin_product_edit_form(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    return templates.TemplateResponse(request, "admin/product_form.html", {
        "product": product,
        "edit_mode": True,
    })


@router.post("/products/{product_id}", response_class=HTMLResponse)
async def admin_product_update(
    product_id: int,
    request: Request,
    name: str = Form(...),
    price: str = Form(...),
    cost_price: str = Form("0"),
    stock_quantity: str = Form("0"),
    size: str = Form(""),
    color: str = Form(""),
    category: str = Form(""),
    brand: str = Form(""),
    sku: str = Form(""),
    description: str = Form(""),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    # Parse price, cost_price, and stock
    try:
        price_int = int(to_english_digits(price))
    except ValueError:
        price_int = product.price

    try:
        cost_price_int = int(to_english_digits(cost_price))
    except ValueError:
        cost_price_int = product.cost_price

    try:
        stock_int = int(to_english_digits(stock_quantity))
    except ValueError:
        stock_int = product.stock_quantity

    # Handle image upload
    if image and image.filename:
        upload_dir = Path("static/uploads/products")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        ext = Path(image.filename).suffix or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = upload_dir / filename
        
        with open(filepath, "wb") as f:
            content = await image.read()
            f.write(content)
        
        product.image_path = f"/static/uploads/products/{filename}"

    # Update product fields
    product.name = name
    product.price = price_int
    product.cost_price = cost_price_int
    product.stock_quantity = stock_int
    product.size = size if size else None
    product.color = color if color else None
    product.category = category if category else None
    product.brand = brand if brand else None
    product.sku = sku if sku else None
    product.description = description if description else None
    product.updated_at = datetime.now(timezone.utc)
    
    db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/delete", response_class=HTMLResponse)
async def admin_product_delete(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        product.is_active = False
        db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/barcodes/print", response_class=HTMLResponse)
async def admin_barcodes_print(
    request: Request,
    category: str = "",
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    query = db.query(Product).filter(Product.is_active == True)
    
    if category:
        query = query.filter(Product.category == category)
    
    products = query.order_by(Product.name).all()
    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]
    
    # Get printed barcode counts from settings
    printed_settings = db.query(Settings).filter(
        Settings.key.like("barcode_printed_%")
    ).all()
    printed_counts = {}
    for s in printed_settings:
        try:
            product_id = int(s.key.replace("barcode_printed_", ""))
            printed_counts[product_id] = int(s.value)
        except (ValueError, TypeError):
            pass
    
    # Expand products by stock quantity, each as a separate printable item
    barcode_items = []
    for product in products:
        qty = product.stock_quantity if product.stock_quantity > 0 else 1
        printed = printed_counts.get(product.id, 0)
        for i in range(qty):
            barcode_items.append({
                "product_id": product.id,
                "barcode": product.barcode,
                "name": product.name,
                "price": product.price,
                "already_printed": i < printed,
            })

    return templates.TemplateResponse(request, "admin/barcode_print.html", {
        "barcode_items": barcode_items,
        "products": products,
        "categories": categories,
        "category_filter": category,
        "fmt": fmt,
    })


@router.post("/barcodes/mark-printed", response_class=HTMLResponse)
async def admin_barcodes_mark_printed(
    request: Request,
    db: Session = Depends(get_db),
):
    """Mark selected barcodes as printed."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    form = await request.form()
    selected_ids = form.getlist("selected_products")
    
    # Count how many times each product was selected
    product_counts = {}
    for pid in selected_ids:
        try:
            pid_int = int(pid)
            product_counts[pid_int] = product_counts.get(pid_int, 0) + 1
        except ValueError:
            pass
    
    # Update printed counts
    for product_id, count in product_counts.items():
        key = f"barcode_printed_{product_id}"
        existing = db.query(Settings).filter(Settings.key == key).first()
        if existing:
            existing.value = str(int(existing.value) + count)
        else:
            db.add(Settings(key=key, value=str(count)))
    
    db.commit()
    
    return RedirectResponse(url="/admin/barcodes/print", status_code=303)


@router.post("/barcodes/reset", response_class=HTMLResponse)
async def admin_barcodes_reset(
    request: Request,
    db: Session = Depends(get_db),
):
    """Reset all printed barcode counts."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    db.query(Settings).filter(Settings.key.like("barcode_printed_%")).delete()
    db.commit()

    return RedirectResponse(url="/admin/barcodes/print", status_code=303)
