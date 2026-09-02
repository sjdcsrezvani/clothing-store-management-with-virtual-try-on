import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Product, ProductVariant, ProductImage, Settings, generate_barcode, to_english_digits
from services.security import require_html_role
from services._common import fmt, check_admin, jalali_str
from services.barcode import generate_barcode_number
from services.templating import templates
from services.inventory import record_opening_stock, record_stock_adjustment, record_cost_adjustment

router = APIRouter(prefix="/admin")


@router.get("/products", response_class=HTMLResponse)
async def admin_products(
    request: Request,
    search: str = "",
    category: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    query = db.query(Product).filter(Product.is_active == True)

    if search:
        # Search product name or variant barcode
        query = query.filter(
            Product.name.contains(search) |
            Product.base_sku.contains(search) |
            Product.variants.any(ProductVariant.barcode.contains(search))
        )

    if category:
        query = query.filter(Product.category == category)

    per_page = 10
    total = query.count()
    products = query.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)

    # Get unique categories
    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    return templates.TemplateResponse(request, "admin/products.html", {
        "products": products,
        "search": search,
        "category_filter": category,
        "categories": categories,
        "page": page,
        "total_pages": total_pages,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/products/add", response_class=HTMLResponse)
async def admin_product_add_form(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    return templates.TemplateResponse(request, "admin/product_form.html", {
        "product": None,
        "edit_mode": False,
    })


@router.post("/products/add", response_class=HTMLResponse)
async def admin_product_add(
    request: Request,
    name: str = Form(...),
    category: str = Form(""),
    brand: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    # Parse dynamic variant fields
    form = await request.form()
    variant_indices = set()
    for key in form.keys():
        if key.startswith("variant_index_"):
            try:
                variant_indices.add(int(key.split("_")[-1]))
            except ValueError:
                pass

    if not variant_indices:
        return templates.TemplateResponse(request, "admin/product_form.html", {
            "product": None,
            "edit_mode": False,
            "error": "حداقل یک تنوع اضافه کنید.",
        })

    upload_dir = Path("static/uploads/products")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Create product
    product = Product(
        name=name,
        category=category if category else None,
        brand=brand if brand else None,
        description=description if description else None,
    )
    db.add(product)
    db.flush()

    # Create variants. Initial stock is recorded through the immutable ledger
    # after the new variant receives its database id.
    created_variants = []
    for idx in sorted(variant_indices):
        size = form.get(f"variant_size_{idx}", "")
        color = form.get(f"variant_color_{idx}", "")
        price_str = form.get(f"variant_price_{idx}", "")
        cost_price_str = form.get(f"variant_cost_price_{idx}", "0")
        stock_str = form.get(f"variant_stock_{idx}", "0")
        barcode = form.get(f"variant_barcode_{idx}", "")
        sku = form.get(f"variant_sku_{idx}", "")

        if not price_str:
            continue

        try:
            price_int = int(to_english_digits(price_str))
        except ValueError:
            continue

        try:
            cost_price_int = int(to_english_digits(cost_price_str))
        except ValueError:
            cost_price_int = 0

        try:
            stock_int = max(0, int(to_english_digits(stock_str)))
        except ValueError:
            stock_int = 0

        # Generate or validate barcode
        if not barcode:
            barcode = generate_barcode_number()
            while db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first():
                barcode = generate_barcode_number()
        else:
            barcode = to_english_digits(barcode.strip())
            existing = db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first()
            if existing:
                db.rollback()
                return templates.TemplateResponse(request, "admin/product_form.html", {
                    "product": None,
                    "edit_mode": False,
                    "error": f"بارکد {barcode} تکراری است.",
                })

        # Handle variant image upload
        variant_image_path = None
        variant_image = form.get(f"variant_image_{idx}")
        if hasattr(variant_image, 'filename') and variant_image.filename:
            ext = Path(variant_image.filename).suffix or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = upload_dir / filename
            with open(filepath, "wb") as f:
                content = await variant_image.read()
                f.write(content)
            variant_image_path = f"/static/uploads/products/{filename}"

        # Create variant
        variant = ProductVariant(
            product_id=product.id,
            size=size if size else None,
            color=color if color else None,
            price=price_int,
            cost_price=cost_price_int,
            stock_quantity=0,
            barcode=barcode,
            sku=sku if sku else None,
            image_path=variant_image_path,
        )
        db.add(variant)
        created_variants.append((variant, stock_int))

    db.flush()
    for variant, initial_stock in created_variants:
        record_opening_stock(
            db,
            variant,
            initial_stock,
            "موجودی اولیه هنگام ایجاد تنوع",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
        )
    db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def admin_product_edit_form(product_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    return templates.TemplateResponse(request, "admin/product_form.html", {
        "product": product,
        "edit_mode": True,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/products/{product_id}", response_class=HTMLResponse)
async def admin_product_update(
    product_id: int,
    request: Request,
    name: str = Form(...),
    category: str = Form(""),
    brand: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="محصول یافت نشد")

    # Update base product
    product.name = name
    product.category = category if category else None
    product.brand = brand if brand else None
    product.description = description if description else None
    product.updated_at = datetime.now(timezone.utc)

    upload_dir = Path("static/uploads/products")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Parse dynamic new variant fields
    form = await request.form()
    variant_indices = set()
    for key in form.keys():
        if key.startswith("variant_index_"):
            try:
                variant_indices.add(int(key.split("_")[-1]))
            except ValueError:
                pass

    # Add new variants if any; their opening stock is ledgered below.
    created_variants = []
    for idx in sorted(variant_indices):
        size = form.get(f"variant_size_{idx}", "")
        color = form.get(f"variant_color_{idx}", "")
        price_str = form.get(f"variant_price_{idx}", "")
        cost_price_str = form.get(f"variant_cost_price_{idx}", "0")
        stock_str = form.get(f"variant_stock_{idx}", "0")
        barcode = form.get(f"variant_barcode_{idx}", "")
        sku = form.get(f"variant_sku_{idx}", "")

        if not price_str:
            continue

        try:
            price_int = int(to_english_digits(price_str))
        except ValueError:
            continue

        try:
            cost_price_int = int(to_english_digits(cost_price_str))
        except ValueError:
            cost_price_int = 0

        try:
            stock_int = max(0, int(to_english_digits(stock_str)))
        except ValueError:
            stock_int = 0

        # Generate or validate barcode
        if not barcode:
            barcode = generate_barcode_number()
            while db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first():
                barcode = generate_barcode_number()
        else:
            barcode = to_english_digits(barcode.strip())
            existing = db.query(ProductVariant).filter(
                ProductVariant.barcode == barcode,
                ProductVariant.product_id != product_id
            ).first()
            if existing:
                return templates.TemplateResponse(request, "admin/product_form.html", {
                    "product": product,
                    "edit_mode": True,
                    "error": f"بارکد {barcode} تکراری است.",
                    "fmt": fmt,
                    "jalali_str": jalali_str,
                })

        # Handle variant image upload
        variant_image_path = None
        variant_image = form.get(f"variant_image_{idx}")
        if hasattr(variant_image, 'filename') and variant_image.filename:
            ext = Path(variant_image.filename).suffix or ".jpg"
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = upload_dir / filename
            with open(filepath, "wb") as f:
                content = await variant_image.read()
                f.write(content)
            variant_image_path = f"/static/uploads/products/{filename}"

        # Create new variant
        variant = ProductVariant(
            product_id=product.id,
            size=size if size else None,
            color=color if color else None,
            price=price_int,
            cost_price=cost_price_int,
            stock_quantity=0,
            barcode=barcode,
            sku=sku if sku else None,
            image_path=variant_image_path,
        )
        db.add(variant)
        created_variants.append((variant, stock_int))

    db.flush()
    for variant, initial_stock in created_variants:
        record_opening_stock(
            db,
            variant,
            initial_stock,
            "موجودی اولیه هنگام ایجاد تنوع",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
        )
    db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/delete", response_class=HTMLResponse)
async def admin_product_delete(product_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        product.is_active = False
        for variant in product.variants:
            variant.is_active = False
        db.commit()

    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/variants/{variant_id}/edit", response_class=HTMLResponse)
async def admin_variant_edit_form(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Edit a specific variant."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    return templates.TemplateResponse(request, "admin/variant_form.html", {
        "variant": variant,
        "product": variant.product,
    })


@router.post("/variants/{variant_id}", response_class=HTMLResponse)
async def admin_variant_update(
    variant_id: int,
    request: Request,
    size: str = Form(""),
    color: str = Form(""),
    price: str = Form(...),
    cost_price: str = Form("0"),
    fake_cost_price: str = Form(""),
    stock_quantity: str = Form("0"),
    barcode: str = Form(""),
    sku: str = Form(""),
    tryon_details: str = Form(""),
    db: Session = Depends(get_db),
):
    """Update a specific variant."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    # Parse values
    try:
        price_int = int(to_english_digits(price))
    except ValueError:
        price_int = variant.price

    has_fake_cost_price = variant.fake_cost_price is not None
    if has_fake_cost_price:
        # The visible cost field is the display-only value; never overwrite the real cost.
        cost_price_int = variant.cost_price
    else:
        try:
            cost_price_int = int(to_english_digits(cost_price))
        except ValueError:
            cost_price_int = variant.cost_price

    fake_cost_price_int = None
    if fake_cost_price.strip():
        try:
            fake_cost_price_int = int(to_english_digits(fake_cost_price.strip()))
        except ValueError:
            fake_cost_price_int = variant.fake_cost_price

    try:
        stock_int = int(to_english_digits(stock_quantity))
    except ValueError:
        stock_int = variant.stock_quantity

    # Validate unique barcode
    if barcode and barcode != variant.barcode:
        barcode = to_english_digits(barcode.strip())
        existing = db.query(ProductVariant).filter(
            ProductVariant.barcode == barcode,
            ProductVariant.id != variant_id
        ).first()
        if existing:
            return templates.TemplateResponse(request, "admin/variant_form.html", {
                "variant": variant,
                "product": variant.product,
                "error": "بارکد تکراری است.",
            })
        variant.barcode = barcode

    # Handle variant image upload
    form = await request.form()
    variant_image = form.get("variant_image")
    if hasattr(variant_image, 'filename') and variant_image.filename:
        upload_dir = Path("static/uploads/products")
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(variant_image.filename).suffix or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = upload_dir / filename
        with open(filepath, "wb") as f:
            content = await variant_image.read()
            f.write(content)
        variant.image_path = f"/static/uploads/products/{filename}"

    # Update variant. Cost edits are also recorded so the current cost can be
    # explained without rewriting historical SaleItem costs.
    old_cost_price = variant.cost_price
    variant.size = size if size else None
    variant.color = color if color else None
    variant.price = price_int
    variant.cost_price = cost_price_int
    record_cost_adjustment(
        db,
        variant,
        old_cost_price,
        cost_price_int,
        note="اصلاح دستی بهای تمام‌شده توسط مدیر",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
    )
    variant.fake_cost_price = fake_cost_price_int
    record_stock_adjustment(
        db,
        variant,
        stock_int,
        note="اصلاح دستی موجودی توسط مدیر",
        actor_user_id=guard.id,
        request_id=request.headers.get("X-Request-ID"),
    )
    variant.sku = sku if sku else None
    variant.tryon_details = tryon_details.strip() if tryon_details.strip() else None
    variant.updated_at = datetime.now(timezone.utc)

    db.commit()
    from services.security import log_action
    log_action(db, "variant_update", f"ویرایش تنوع #{variant.id}", request=request, target_type="variant", target_id=variant.id)

    return RedirectResponse(url="/admin/products/" + str(variant.product_id), status_code=303)


@router.post("/variants/{variant_id}/delete", response_class=HTMLResponse)
async def admin_variant_delete(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a specific variant."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if variant:
        product_id = variant.product_id
        variant.is_active = False
        db.commit()
        return RedirectResponse(url="/admin/products/" + str(product_id), status_code=303)

    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/variants/{variant_id}/demand", response_class=HTMLResponse)
async def admin_variant_demand_up(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Record one customer asking for this variant (out-of-stock)."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    variant.demand_count = (variant.demand_count or 0) + 1
    db.commit()
    return RedirectResponse(url=f"/admin/products/{variant.product_id}", status_code=303)


@router.post("/variants/{variant_id}/demand/reset", response_class=HTMLResponse)
async def admin_variant_demand_reset(variant_id: int, request: Request, db: Session = Depends(get_db)):
    """Clears the counted demand for a variant (owner filled the backlog)."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="تنوع یافت نشد")

    variant.demand_count = 0
    db.commit()
    return RedirectResponse(url=f"/admin/products/{variant.product_id}", status_code=303)


@router.get("/barcodes/print", response_class=HTMLResponse)
async def admin_barcodes_print(
    request: Request,
    category: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    query = db.query(Product).filter(Product.is_active == True)

    if category:
        query = query.filter(Product.category == category)

    products = query.order_by(Product.name).all()
    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    # Get printed barcode counts
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

    # Expand variants by stock quantity
    barcode_items = []
    for product in products:
        for variant in product.variants:
            if not variant.is_active:
                continue
            qty = variant.stock_quantity if variant.stock_quantity > 0 else 1
            printed = printed_counts.get(variant.id, 0)
            for i in range(qty):
                barcode_items.append({
                    "variant_id": variant.id,
                    "barcode": variant.barcode,
                    "name": variant.display_name,
                    "price": variant.price,
                    "already_printed": i < printed,
                })

    # Sort: unprinted first
    barcode_items.sort(key=lambda x: (x["already_printed"], x["name"]))

    # Paginate
    page_size = 28
    total_items = len(barcode_items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    page_items = barcode_items[start:start + page_size]

    return templates.TemplateResponse(request, "admin/barcode_print.html", {
        "barcode_items": page_items,
        "all_items_count": total_items,
        "page": page,
        "total_pages": total_pages,
        "categories": categories,
        "category_filter": category,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.post("/barcodes/mark-printed", response_class=HTMLResponse)
async def admin_barcodes_mark_printed(request: Request, db: Session = Depends(get_db)):
    """Mark the selected unprinted tag copies as printed."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    selected_ids = form.getlist("selected_products")
    category = str(form.get("category", ""))
    page = str(form.get("page", "1"))

    selected_counts = {}
    for variant_id in selected_ids:
        try:
            variant_id_int = int(variant_id)
            selected_counts[variant_id_int] = selected_counts.get(variant_id_int, 0) + 1
        except (TypeError, ValueError):
            pass

    for variant_id, requested_count in selected_counts.items():
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue

        maximum = variant.stock_quantity if variant.stock_quantity > 0 else 1
        key = f"barcode_printed_{variant_id}"
        existing = db.query(Settings).filter(Settings.key == key).first()
        printed_count = int(existing.value) if existing and existing.value else 0
        count = min(requested_count, max(0, maximum - printed_count))
        if count <= 0:
            continue
        if existing:
            existing.value = str(printed_count + count)
        else:
            db.add(Settings(key=key, value=str(count)))

    db.commit()
    query = urlencode({"page": page, "category": category})
    return RedirectResponse(url=f"/admin/barcodes/print?{query}", status_code=303)


@router.post("/barcodes/reset", response_class=HTMLResponse)
async def admin_barcodes_reset(request: Request, db: Session = Depends(get_db)):
    """Reset printed counts for the selected tag copies only."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    selected_ids = form.getlist("selected_products")
    category = str(form.get("category", ""))
    page = str(form.get("page", "1"))

    selected_counts = {}
    for variant_id in selected_ids:
        try:
            variant_id_int = int(variant_id)
            selected_counts[variant_id_int] = selected_counts.get(variant_id_int, 0) + 1
        except (TypeError, ValueError):
            pass

    for variant_id, count in selected_counts.items():
        key = f"barcode_printed_{variant_id}"
        existing = db.query(Settings).filter(Settings.key == key).first()
        if not existing:
            continue
        remaining = max(0, int(existing.value or 0) - count)
        if remaining:
            existing.value = str(remaining)
        else:
            db.delete(existing)

    db.commit()
    query = urlencode({"page": page, "category": category})
    return RedirectResponse(url=f"/admin/barcodes/print?{query}", status_code=303)