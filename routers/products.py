import csv
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, quote_plus
from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Product,
    ProductVariant,
    ProductImage,
    Settings,
    Supplier,
    TagTemplate,
    TagPrintBatch,
    TagPrintBatchLine,
    generate_barcode,
    to_english_digits,
)
from services.security import log_action, require_html_role
from services._common import fmt, check_admin, jalali_str, page_arg
from services.barcode import BARCODE_DENSITIES, generate_barcode_image, generate_barcode_number
from services.templating import templates
from services.inventory import (
    LOW_STOCK_THRESHOLD,
    record_opening_stock,
    record_stock_adjustment,
    record_cost_adjustment,
    sellable_expression,
    stock_alerts,
)
from services.sorting import parse_sort
from services.store import get_store
from services.events import append_event
from services.tags import (
    ALLOWED_ALIGNMENTS,
    ALLOWED_BARCODE_MODES,
    ALLOWED_LAYOUT_MODES,
    FIELD_LABELS,
    PRESETS,
    TAG_INPUT_KEYS,
    TAG_NUMERIC_RULES,
    calculate_a4_fit,
    item_from_variant,
    load_tag_config,
    render_tag_html,
    save_tag_config,
    save_tag_image,
    list_tag_templates,
    save_tag_template,
    tag_template_config,
    FIELD_LABELS,
)

router = APIRouter(prefix="/admin")
MAX_TAG_PRINT_QUANTITY = 1000


def _form_text(form, key: str, default: str = "") -> str:
    value = form.get(key, default)
    return str(value).strip() if value is not None else default


def _form_nonnegative_int(form, key: str, default: int = 0) -> int:
    value = _form_text(form, key, str(default))
    try:
        return max(0, int(to_english_digits(value)))
    except (TypeError, ValueError):
        return default


def _form_optional_int(form, key: str):
    value = _form_text(form, key)
    if not value:
        return None
    try:
        return max(0, int(to_english_digits(value)))
    except (TypeError, ValueError):
        return None


def _form_tag_template_id(form, db):
    value = _form_optional_int(form, "tag_template_id")
    if not value:
        return None
    template = db.query(TagTemplate).filter(
        TagTemplate.id == value,
        TagTemplate.is_active == True,
    ).first()
    return template.id if template else None


_HEX_SHORT = re.compile(r"^#?([0-9a-fA-F]{3})$")
_HEX_FULL = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _normalize_hex_color(value) -> str | None:
    """A colour code as uppercase #RRGGBB, or None when left empty.

    Raises ValueError with a shop-readable reason when malformed — a code the
    tag printer cannot read must be refused on the form, not stored.
    """
    text = str(value or "").strip()
    if not text:
        return None
    short = _HEX_SHORT.match(text)
    if short:
        return "#" + "".join(ch * 2 for ch in short.group(1)).upper()
    full = _HEX_FULL.match(text)
    if full:
        return "#" + full.group(1).upper()
    raise ValueError(f"کد رنگ «{text}» معتبر نیست (مثال: #4AA3DF).")


def _product_form_error(request, db, product, edit_mode: bool, message: str):
    context = {
        "product": product,
        "edit_mode": edit_mode,
        "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
        "tag_templates": list_tag_templates(db),
        "error": message,
    }
    context.update(_catalog_datalists(db))
    if edit_mode:
        context.update({"fmt": fmt, "jalali_str": jalali_str})
    return templates.TemplateResponse(request, "admin/product_form.html", context)


def _catalog_datalists(db) -> dict:
    """Distinct categories and brands already on the shelf, so the form can
    suggest instead of demanding a fresh spelling every time. Typing a new
    value stays allowed — the list suggests, never restricts."""
    categories = [row[0] for row in db.query(Product.category).distinct().all() if row[0]]
    brands = [row[0] for row in db.query(Product.brand).distinct().all() if row[0]]
    return {"categories": sorted(categories), "brands": sorted(brands)}


def _selected_quantities(form) -> dict[int, int]:
    quantities = {}

    # Native form submission uses one quantity input per selected variant.
    # Keep this path independent of JavaScript so the print actions still work
    # when browser scripts are blocked or fail to load.
    for key in form.keys():
        if not str(key).startswith("selected_quantity_"):
            continue
        try:
            variant_id = int(str(key).removeprefix("selected_quantity_"))
            quantity = int(to_english_digits(str(form.get(key))))
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            quantities[variant_id] = quantity

    # The JSON payload remains supported for older clients and the enhanced
    # JavaScript submission path.
    raw_value = form.get("selected_quantities", "")
    if raw_value:
        try:
            values = json.loads(str(raw_value))
        except (TypeError, ValueError):
            values = {}
        if isinstance(values, dict):
            for variant_id, quantity in values.items():
                try:
                    variant_id_int = int(variant_id)
                    quantity_int = int(to_english_digits(str(quantity)))
                except (TypeError, ValueError):
                    continue
                if quantity_int > 0:
                    quantities[variant_id_int] = quantity_int
    return quantities


# The columns the products list may sort by, each with its own default
# direction — names ascend, stock descends to surface the fullest shelf first.
# Anything the query names outside this map answers the default list.
PRODUCT_SORTS = {
    "newest": "desc",
    "name": "asc",
    "category": "asc",
    "price": "asc",
    "stock": "desc",
}
PRODUCT_PER_PAGE_OPTIONS = (10, 25, 50)
PRODUCT_STOCK_FILTERS = ("all", "low", "out")


def _product_list_query(db, search: str = "", category: str = "", stock: str = "all"):
    """The filtered products query the list, the CSV and the bulk bar share.

    Price and stock are variant aggregates, so the query carries them as
    outer-joined subqueries: products without variants keep their row (with
    NULL aggregates) instead of vanishing from their own catalogue.
    """
    price_sq = (
        db.query(
            ProductVariant.product_id,
            func.min(ProductVariant.price).label("price_min"),
        )
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .group_by(ProductVariant.product_id)
        .subquery()
    )
    avail_sq = (
        db.query(
            ProductVariant.product_id,
            func.sum(sellable_expression()).label("sellable"),
        )
        .filter(ProductVariant.is_active == True)  # noqa: E712
        .group_by(ProductVariant.product_id)
        .subquery()
    )
    query = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .outerjoin(price_sq, price_sq.c.product_id == Product.id)
        .outerjoin(avail_sq, avail_sq.c.product_id == Product.id)
    )

    if search:
        # Search product name or variant barcode
        query = query.filter(
            Product.name.contains(search) |
            Product.base_sku.contains(search) |
            Product.category.contains(search) |
            Product.variants.any(ProductVariant.barcode.contains(search)) |
            Product.variants.any(ProductVariant.sku.contains(search))
        )

    if category:
        query = query.filter(Product.category == category)

    if stock == "out":
        query = query.filter(Product.variants.any(and_(
            ProductVariant.is_active == True,  # noqa: E712
            sellable_expression() <= 0,
        )))
    elif stock == "low":
        # Low *includes* the run-out shelf, exactly like the KPI above the
        # table — one definition of «کم‌موجود» everywhere, not two.
        query = query.filter(Product.variants.any(and_(
            ProductVariant.is_active == True,  # noqa: E712
            sellable_expression() <= LOW_STOCK_THRESHOLD,
        )))

    return query, price_sq, avail_sq


def _product_order(sort_key: str, sort_dir: str, price_sq, avail_sq):
    """ORDER BY for the list and the CSV — the same rows in both, so the file
    is the view the owner was looking at, not a reshuffled copy."""
    if sort_key == "name":
        column = Product.name
    elif sort_key == "category":
        column = Product.category
    elif sort_key == "price":
        # Variant-less products sort last in both directions: no price is not
        # the cheapest price, and it is not the dearest either.
        column = price_sq.c.price_min.nulls_last() if sort_dir == "asc" \
            else price_sq.c.price_min.desc().nulls_last()
        return column, Product.id.desc()
    elif sort_key == "stock":
        column = func.coalesce(avail_sq.c.sellable, 0)
    else:
        column = Product.created_at
    order = column.desc() if sort_dir == "desc" else column.asc()
    return order, Product.id.desc()


def _product_per_page(raw: str) -> int:
    try:
        per_page = int(raw)
    except (TypeError, ValueError):
        return PRODUCT_PER_PAGE_OPTIONS[0]
    if per_page not in PRODUCT_PER_PAGE_OPTIONS:
        return PRODUCT_PER_PAGE_OPTIONS[0]
    return per_page


def _products_csv_response(filename: str, rows: list[list]) -> Response:
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel opens Persian correctly
    csv.writer(buf).writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/products", response_class=HTMLResponse)
async def admin_products(
    request: Request,
    search: str = "",
    category: str = "",
    page: str = "1",
    per_page: str = "10",
    stock: str = "all",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    page = page_arg(page)
    per_page = _product_per_page(per_page)
    if stock not in PRODUCT_STOCK_FILTERS:
        stock = "all"
    # Ledger sorting, same convention as the sales list: an unknown key or
    # direction answers the default newest-first list, never an error.
    sort_key, sort_dir = parse_sort(request.query_params, PRODUCT_SORTS, "newest")

    query, price_sq, avail_sq = _product_list_query(db, search, category, stock)
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), total_pages)
    order, tiebreak = _product_order(sort_key, sort_dir, price_sq, avail_sq)
    products = query.order_by(order, tiebreak).offset((page - 1) * per_page).limit(per_page).all()

    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]
    # Shared with the dashboard's «موجودی کم» alert, so the two can never quote
    # different numbers for the same shelf.
    alerts = stock_alerts(db)

    return templates.TemplateResponse(request, "admin/products.html", {
        "products": products,
        "search": search,
        "category_filter": category,
        "categories": categories,
        "page": page,
        "total_pages": total_pages,
        "total_products": total,
        "low_stock_count": alerts["low_count"],
        "out_stock_count": alerts["out_count"],
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "per_page": per_page,
        "per_page_options": PRODUCT_PER_PAGE_OPTIONS,
        "stock_filter": stock,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


@router.get("/products/export")
async def admin_products_export(
    request: Request,
    search: str = "",
    category: str = "",
    stock: str = "all",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    if stock not in PRODUCT_STOCK_FILTERS:
        stock = "all"
    sort_key, sort_dir = parse_sort(request.query_params, PRODUCT_SORTS, "newest")
    query, price_sq, avail_sq = _product_list_query(db, search, category, stock)
    order, tiebreak = _product_order(sort_key, sort_dir, price_sq, avail_sq)

    rows = [["نام", "برند", "دسته‌بندی", "SKU پایه", "تنوع فعال",
             "بازه قیمت", "قابل فروش", "رزرو"]]
    for product in query.order_by(order, tiebreak).all():
        active_variants = [v for v in product.variants if v.is_active]
        rows.append([
            product.name, product.brand or "", product.category or "",
            product.base_sku or "", len(active_variants),
            product.price_range or "—", product.available_stock,
            product.total_reserved,
        ])
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _products_csv_response(f"products_{today}.csv", rows)


@router.post("/products/bulk-archive")
async def admin_products_bulk_archive(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    ids = [int(raw) for raw in form.getlist("ids") if str(raw).isdigit()]
    archived = 0
    if ids:
        products = db.query(Product).filter(
            Product.id.in_(ids),
            Product.is_active == True,  # noqa: E712
        ).all()
        for product in products:
            product.is_active = False
            for variant in product.variants:
                variant.is_active = False
            archived += 1
        db.commit()

    if archived:
        message = f"{archived} محصول بایگانی شد."
    else:
        message = "محصولی برای بایگانی انتخاب نشده بود."
    return RedirectResponse(
        url="/admin/products?msg=" + quote_plus(message), status_code=303)


def _safe_tag_preview(config, sample_item, store) -> str:
    """The preview, or an empty string when the layout cannot be re-validated.

    A broken rules table must not take the page down before its own drift
    checks can report; a layout that simply does not validate (an old saved
    shape, say) leaves the page without its preview rather than without its
    error screen.
    """
    try:
        return render_tag_html(config, sample_item, store)
    except (ValueError, TypeError, KeyError):
        return ""


@router.get("/settings/tags", response_class=HTMLResponse)
async def admin_tag_settings(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    # The context is built before the drift checks so the check render and the
    # page's own error path share one context: a template that hard-codes a
    # bound the table lacks — or a numeric field the map never told about —
    # fails visibly here, on the page the owner is looking at, instead of
    # surfacing later as a rule the browser showed and the server ignored.
    config = load_tag_config(db)
    tag_templates = list_tag_templates(db)
    sample_variant = (
        db.query(ProductVariant)
        .join(Product)
        .filter(Product.is_active == True, ProductVariant.is_active == True)
        .order_by(Product.name, ProductVariant.id)
        .first()
    )
    sample_item = item_from_variant(sample_variant, fmt) if sample_variant else {
        "variant_id": None, "barcode": "12345", "product_name": "نمونه محصول",
        "name": "نمونه محصول", "price": 250000, "price_display": fmt(250000),
        "size": "۴ سال", "color": "آبی", "sku": "SKU-001", "brand": "رای کیدز",
        "category": "لباس کودک", "image_path": None,
    }
    store = get_store(db)
    barcode_preview_sources = {
        density: generate_barcode_image(sample_item["barcode"], density=density)
        for density in ("compact", "standard")
    }
    context = {
        "request": request,
        "tag_config": config,
        # The one table of bounds the validator enforces; the template renders
        # its min/max attributes from it, so a bound is written once.
        "tag_numeric_rules": TAG_NUMERIC_RULES,
        # The field names the designer script shows, served from the service's
        # own map instead of a second copy hard-coded in the page.
        "field_labels": FIELD_LABELS,
        # The choices each select offers, from the same allow-lists the
        # validator enforces — an option the server would refuse cannot be
        # offered, and a new accepted value cannot be missing from the page.
        "allow_list_sources": {
            "layout_modes": sorted(ALLOWED_LAYOUT_MODES),
            "alignments": sorted(ALLOWED_ALIGNMENTS),
            "barcode_modes": sorted(ALLOWED_BARCODE_MODES),
            "barcode_densities": sorted(BARCODE_DENSITIES),
        },
        "tag_presets": PRESETS,
        "tag_field_options": [
            {
                "name": name,
                "label": FIELD_LABELS[name],
                "visible": bool(config["fields"].get(name, {}).get("visible")),
                "color": config["fields"].get(name, {}).get("color", "#888888"),
            }
            for name in (
                "product_name", "price", "size", "color", "barcode", "barcode_text",
                "sku", "brand", "category", "store_name", "instagram", "product_image", "custom_text",
            )
        ],
        "tag_templates": [
            {"id": template.id, "name": template.name, "config": tag_template_config(template, config)}
            for template in tag_templates
        ],
        "tag_fit": calculate_a4_fit(config),
        "sample_item": sample_item,
        # A corrupted rules table raises inside the preview's own re-validation;
        # the page must still render so the drift checks below can name the
        # problem instead of the route crashing on it.
        "preview_html": _safe_tag_preview(config, sample_item, store),
        "barcode_preview_sources": barcode_preview_sources,
        "store": store,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    }

    # Source check, source side: the template's own map must be exactly the
    # canonical one, every macro call must name a mapped input, and the table
    # must name no key no input ships — either way one of the sources drifted.
    template_source = (Path("templates") / "admin" / "settings_tags.html").read_text(encoding="utf-8")
    start = template_source.index("{% set tag_input_keys")
    map_block = template_source[start:template_source.index("} %}", start)]
    mapped = dict(re.findall(r"'([a-z-]+)':\s*'([a-z_]+)'", map_block))
    calls = set(re.findall(r"num\('([a-z-]+)'", template_source))
    if mapped != TAG_INPUT_KEYS:
        context["err"] = "خطای قالب: نقشه ورودی‌ها با جدول کلیدها هم‌خوان نیست — هر دو را با هم به‌روز کنید."
        return templates.TemplateResponse(request, "admin/settings_tags.html", context)
    unmapped = sorted(calls - set(TAG_INPUT_KEYS))
    if unmapped:
        context["err"] = "خطای قالب: «" + "، ".join(unmapped) + "» در نقشه ورودی‌ها نیست — صفحه از ذخیره‌سازی محافظت می‌کند."
        return templates.TemplateResponse(request, "admin/settings_tags.html", context)
    orphan_rules = sorted(set(TAG_NUMERIC_RULES) - set(TAG_INPUT_KEYS.values()))
    if orphan_rules:
        context["err"] = "خطای قالب: «" + "، ".join(orphan_rules) + "» قاعده‌ای بی‌میدان است."
        return templates.TemplateResponse(request, "admin/settings_tags.html", context)

    # Source check, painted side: the bounds are verified in what the page
    # actually paints, against the table, so a macro overridden per field or a
    # hand-written min/max names itself here as a figure the table never said.
    for processor in templates.context_processors:
        context.update(processor(request))
    rendered = templates.get_template("admin/settings_tags.html").render(context)
    hard_bounds = []
    for m in re.finditer(r'<input\b[^>]*>', rendered):
        tag = m.group(0)
        if 'type="number"' not in tag:
            continue
        id_m = re.search(r'id="([a-z-]+)"', tag)
        key = TAG_INPUT_KEYS.get(id_m.group(1)) if id_m else None
        rule = TAG_NUMERIC_RULES.get(key) if key else None
        min_m = re.search(r'\bmin="([0-9.]+)"', tag)
        max_m = re.search(r'\bmax="([0-9.]+)"', tag)
        painted = (float(min_m.group(1)) if min_m else None,
                   float(max_m.group(1)) if max_m else None)
        expected = (float(rule[0]), float(rule[1])) if rule else (None, None)
        if painted != expected:
            hard_bounds.append(id_m.group(1) if id_m else tag[:60])
    hard_bounds = sorted(set(hard_bounds))
    if hard_bounds:
        context["err"] = "خطای قالب: «" + "، ".join(hard_bounds) + "» مرز خودش را نوشته — از جدول قواعد استفاده کنید."
        context["msg"] = ""
        return templates.TemplateResponse(request, "admin/settings_tags.html", context)

    return templates.TemplateResponse(request, "admin/settings_tags.html", context)


@router.post("/settings/tags", response_class=HTMLResponse)
async def admin_tag_settings_save(request: Request, tag_config: str = Form(""), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        save_tag_config(db, json.loads(tag_config))
        db.commit()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, ValueError) else "داده طراحی تگ خوانده نشد؛ دوباره تلاش کنید."
        return RedirectResponse(url="/admin/settings/tags?err=" + quote_plus(message), status_code=303)
    # A settings change worth an audit row, like every other settings save.
    log_action(db, "tag_settings_update", "به‌روزرسانی طرح تگ و بارکد", request=request, target_type="settings")
    return RedirectResponse(url="/admin/settings/tags?msg=تنظیمات تگ ذخیره شد.", status_code=303)


@router.post("/settings/tags/template", response_class=HTMLResponse)
async def admin_tag_template_save(
    request: Request,
    template_name: str = Form(""),
    template_id: str = Form(""),
    tag_config: str = Form(""),
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    try:
        parsed_id = int(template_id) if template_id.strip() else None
        save_tag_template(db, template_name, json.loads(tag_config), parsed_id)
        db.commit()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, ValueError) else "داده طراحی تگ خوانده نشد؛ دوباره تلاش کنید."
        return RedirectResponse(url="/admin/settings/tags?err=" + quote_plus(message), status_code=303)
    log_action(db, "tag_template_update", "به‌روزرسانی قالب تگ", request=request, target_type="settings")
    return RedirectResponse(url="/admin/settings/tags?msg=" + quote_plus("قالب تگ ذخیره شد."), status_code=303)


@router.post("/settings/tags/template/{template_id}/delete", response_class=HTMLResponse)
async def admin_tag_template_delete(template_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    template = db.query(TagTemplate).filter(TagTemplate.id == template_id, TagTemplate.is_active == True).first()
    if template:
        if db.query(Product).filter(Product.tag_template_id == template.id, Product.is_active == True).first():
            return RedirectResponse(url="/admin/settings/tags?err=" + quote_plus("این قالب به محصول اختصاص دارد و حذف نمی‌شود."), status_code=303)
        template.is_active = False
        db.commit()
    return RedirectResponse(url="/admin/settings/tags?msg=" + quote_plus("قالب تگ حذف شد."), status_code=303)


@router.post("/settings/tags/image", response_class=HTMLResponse)
async def admin_tag_settings_image(request: Request, image: UploadFile | None = File(None), db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    if image is None or not (image.filename or "").strip():
        return RedirectResponse(
            url="/admin/settings/tags?err=" + quote_plus("ابتدا یک تصویر انتخاب کنید."),
            status_code=303)
    try:
        config = load_tag_config(db)
        config["custom_image_path"] = save_tag_image(await image.read(), image.filename or "", image.content_type)
        save_tag_config(db, config)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(url="/admin/settings/tags?err=" + quote_plus(str(exc)), status_code=303)
    log_action(db, "tag_image_update", "به‌روزرسانی تصویر سفارشی بارکد", request=request, target_type="settings")
    return RedirectResponse(url="/admin/settings/tags?msg=تصویر تگ ذخیره شد.", status_code=303)


@router.get("/products/add", response_class=HTMLResponse)
async def admin_product_add_form(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    return templates.TemplateResponse(request, "admin/product_form.html", {
        "product": None,
        "edit_mode": False,
        "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
        "tag_templates": list_tag_templates(db),
        **_catalog_datalists(db),
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

    if not name.strip():
        return _product_form_error(request, db, None, False, "نام محصول الزامی است.")

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
            "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
            "tag_templates": list_tag_templates(db),
            "error": "حداقل یک تنوع اضافه کنید.",
        })

    upload_dir = Path("static/uploads/products")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Create product
    product = Product(
        name=name.strip(),
        category=category.strip() or None,
        brand=brand.strip() or None,
        description=description.strip() or None,
        base_sku=_form_text(form, "base_sku") or None,
        base_barcode=to_english_digits(_form_text(form, "base_barcode")) or None,
        garment_type=_form_text(form, "garment_type") or None,
        gender=_form_text(form, "gender") or None,
        material=_form_text(form, "material") or None,
        season=_form_text(form, "season") or None,
        collection=_form_text(form, "collection") or None,
        care_instructions=_form_text(form, "care_instructions") or None,
        supplier_id=_form_optional_int(form, "supplier_id"),
        default_reorder_point=_form_nonnegative_int(form, "default_reorder_point"),
        default_reorder_quantity=_form_nonnegative_int(form, "default_reorder_quantity"),
        tag_template_id=_form_tag_template_id(form, db),
    )
    db.add(product)
    db.flush()
    error_product = None
    error_edit_mode = False

    # Create variants. Initial stock is recorded through the immutable ledger
    # after the new variant receives its database id.
    created_variants = []
    seen_barcodes = set()
    for idx in sorted(variant_indices):
        size = form.get(f"variant_size_{idx}", "")
        color = form.get(f"variant_color_{idx}", "")
        price_str = form.get(f"variant_price_{idx}", "")
        cost_price_str = form.get(f"variant_cost_price_{idx}", "0")
        stock_str = form.get(f"variant_stock_{idx}", "0")
        barcode = form.get(f"variant_barcode_{idx}", "")
        sku = form.get(f"variant_sku_{idx}", "")
        reorder_point = _form_nonnegative_int(form, f"variant_reorder_point_{idx}", product.default_reorder_point)
        reorder_quantity = _form_nonnegative_int(form, f"variant_reorder_quantity_{idx}", product.default_reorder_quantity)
        storage_location = _form_text(form, f"variant_storage_location_{idx}") or None
        size_system = _form_text(form, f"variant_size_system_{idx}") or None
        color_code = _form_text(form, f"variant_color_code_{idx}") or None
        variant_weight = _form_optional_int(form, f"variant_weight_{idx}")
        try:
            color_code = _normalize_hex_color(form.get(f"variant_color_code_{idx}", ""))
        except ValueError as problem:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, str(problem))

        if not price_str:
            continue

        try:
            price_int = int(to_english_digits(price_str))
        except ValueError:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت فروش معتبر نیست.")
        if price_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت فروش نمی‌تواند منفی باشد.")

        try:
            cost_price_int = int(to_english_digits(cost_price_str))
        except ValueError:
            cost_price_int = 0
        if cost_price_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت خرید نمی‌تواند منفی باشد.")

        try:
            stock_int = int(to_english_digits(stock_str))
        except ValueError:
            stock_int = 0
        if stock_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "موجودی اولیه نمی‌تواند منفی باشد.")

        # Generate or validate barcode
        barcode = to_english_digits(str(barcode or "").strip())
        if not barcode:
            while True:
                barcode = generate_barcode_number(db)
                if not db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first() and barcode not in seen_barcodes:
                    break
        else:
            existing = db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first()
            if existing or barcode in seen_barcodes:
                db.rollback()
                return _product_form_error(request, db, None, False, f"بارکد {barcode} تکراری است.")
        seen_barcodes.add(barcode)

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
            reorder_point=reorder_point,
            reorder_quantity=reorder_quantity,
            storage_location=storage_location,
            size_system=size_system,
            color_code=color_code,
            weight_grams=variant_weight,
        )
        db.add(variant)
        created_variants.append((variant, stock_int))

    if not created_variants and not product.variants:
        db.rollback()
        return _product_form_error(request, db, None, False, "حداقل یک تنوع با قیمت فروش معتبر اضافه کنید.")

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
        "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
        "tag_templates": list_tag_templates(db),
        **_catalog_datalists(db),
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
    if not name.strip():
        return _product_form_error(request, db, product, True, "نام محصول الزامی است.")

    # Update base product
    form = await request.form()
    product.name = name.strip()
    product.category = category.strip() or None
    product.brand = brand.strip() or None
    product.description = description.strip() or None
    product.base_sku = _form_text(form, "base_sku") or None
    product.base_barcode = to_english_digits(_form_text(form, "base_barcode")) or None
    product.garment_type = _form_text(form, "garment_type") or None
    product.gender = _form_text(form, "gender") or None
    product.material = _form_text(form, "material") or None
    product.season = _form_text(form, "season") or None
    product.collection = _form_text(form, "collection") or None
    product.care_instructions = _form_text(form, "care_instructions") or None
    product.supplier_id = _form_optional_int(form, "supplier_id")
    product.default_reorder_point = _form_nonnegative_int(form, "default_reorder_point")
    product.default_reorder_quantity = _form_nonnegative_int(form, "default_reorder_quantity")
    product.tag_template_id = _form_tag_template_id(form, db)
    product.updated_at = datetime.now(timezone.utc)

    upload_dir = Path("static/uploads/products")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Parse dynamic new variant fields
    variant_indices = set()
    for key in form.keys():
        if key.startswith("variant_index_"):
            try:
                variant_indices.add(int(key.split("_")[-1]))
            except ValueError:
                pass

    # Add new variants if any; their opening stock is ledgered below.
    created_variants = []
    seen_barcodes = set()
    error_product = product
    error_edit_mode = True
    for idx in sorted(variant_indices):
        size = form.get(f"variant_size_{idx}", "")
        color = form.get(f"variant_color_{idx}", "")
        price_str = form.get(f"variant_price_{idx}", "")
        cost_price_str = form.get(f"variant_cost_price_{idx}", "0")
        stock_str = form.get(f"variant_stock_{idx}", "0")
        barcode = form.get(f"variant_barcode_{idx}", "")
        sku = form.get(f"variant_sku_{idx}", "")
        reorder_point = _form_nonnegative_int(form, f"variant_reorder_point_{idx}", product.default_reorder_point)
        reorder_quantity = _form_nonnegative_int(form, f"variant_reorder_quantity_{idx}", product.default_reorder_quantity)
        storage_location = _form_text(form, f"variant_storage_location_{idx}") or None
        size_system = _form_text(form, f"variant_size_system_{idx}") or None
        color_code = _form_text(form, f"variant_color_code_{idx}") or None
        variant_weight = _form_optional_int(form, f"variant_weight_{idx}")
        try:
            color_code = _normalize_hex_color(form.get(f"variant_color_code_{idx}", ""))
        except ValueError as problem:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, str(problem))

        if not price_str:
            continue

        try:
            price_int = int(to_english_digits(price_str))
        except ValueError:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت فروش معتبر نیست.")
        if price_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت فروش نمی‌تواند منفی باشد.")

        try:
            cost_price_int = int(to_english_digits(cost_price_str))
        except ValueError:
            cost_price_int = 0
        if cost_price_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "قیمت خرید نمی‌تواند منفی باشد.")

        try:
            stock_int = int(to_english_digits(stock_str))
        except ValueError:
            stock_int = 0
        if stock_int < 0:
            db.rollback()
            return _product_form_error(request, db, error_product, error_edit_mode, "موجودی اولیه نمی‌تواند منفی باشد.")

        # Generate or validate barcode
        barcode = to_english_digits(str(barcode or "").strip())
        if not barcode:
            while True:
                barcode = generate_barcode_number(db)
                if not db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first() and barcode not in seen_barcodes:
                    break
        else:
            existing = db.query(ProductVariant).filter(ProductVariant.barcode == barcode).first()
            if existing or barcode in seen_barcodes:
                db.rollback()
                return _product_form_error(request, db, product, True, f"بارکد {barcode} تکراری است.")
        seen_barcodes.add(barcode)

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
            reorder_point=reorder_point,
            reorder_quantity=reorder_quantity,
            storage_location=storage_location,
            size_system=size_system,
            color_code=color_code,
            weight_grams=variant_weight,
        )
        db.add(variant)
        created_variants.append((variant, stock_int))

    if not created_variants and not product.variants:
        db.rollback()
        return _product_form_error(request, db, product, True, "حداقل یک تنوع با قیمت فروش معتبر اضافه کنید.")

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
    reorder_point: str = Form("0"),
    reorder_quantity: str = Form("0"),
    weight_grams: str = Form(""),
    storage_location: str = Form(""),
    size_system: str = Form(""),
    color_code: str = Form(""),
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
        return templates.TemplateResponse(request, "admin/variant_form.html", {
            "variant": variant,
            "product": variant.product,
            "error": "قیمت فروش معتبر نیست.",
        })
    if price_int < 0:
        return templates.TemplateResponse(request, "admin/variant_form.html", {
            "variant": variant,
            "product": variant.product,
            "error": "قیمت فروش نمی‌تواند منفی باشد.",
        })

    fake_cost_price_int = None
    if fake_cost_price.strip():
        try:
            fake_cost_price_int = max(0, int(to_english_digits(fake_cost_price.strip())))
        except ValueError:
            fake_cost_price_int = variant.fake_cost_price

    if fake_cost_price_int is not None or variant.fake_cost_price is not None:
        # A second (display-only) cost is in play, so the visible cost field is
        # only a mirror of it: never let it rewrite the real cost basis.
        cost_price_int = variant.cost_price
    else:
        try:
            cost_price_int = int(to_english_digits(cost_price))
        except ValueError:
            return templates.TemplateResponse(request, "admin/variant_form.html", {
                "variant": variant,
                "product": variant.product,
                "error": "قیمت خرید معتبر نیست.",
            })
        if cost_price_int < 0:
            return templates.TemplateResponse(request, "admin/variant_form.html", {
                "variant": variant,
                "product": variant.product,
                "error": "قیمت خرید نمی‌تواند منفی باشد.",
            })

    try:
        stock_int = int(to_english_digits(stock_quantity))
    except ValueError:
        stock_int = variant.stock_quantity
    if stock_int < 0 or stock_int < (variant.reserved_quantity or 0):
        return templates.TemplateResponse(request, "admin/variant_form.html", {
            "variant": variant,
            "product": variant.product,
            "error": "موجودی نمی‌تواند کمتر از موجودی رزروشده باشد.",
        })
    try:
        reorder_point_int = max(0, int(to_english_digits(reorder_point or "0")))
        reorder_quantity_int = max(0, int(to_english_digits(reorder_quantity or "0")))
    except ValueError:
        return templates.TemplateResponse(request, "admin/variant_form.html", {
            "variant": variant,
            "product": variant.product,
            "error": "مقدار نقطه سفارش معتبر نیست.",
        })

    weight_raw = (weight_grams or "").strip()
    if weight_raw:
        try:
            weight_int = max(0, int(to_english_digits(weight_raw)))
        except ValueError:
            return templates.TemplateResponse(request, "admin/variant_form.html", {
                "variant": variant,
                "product": variant.product,
                "error": "وزن معتبر نیست.",
            })
    else:
        weight_int = None

    # Validate unique barcode
    normalized_barcode = to_english_digits(str(barcode or "").strip())
    if not normalized_barcode:
        return templates.TemplateResponse(request, "admin/variant_form.html", {
            "variant": variant,
            "product": variant.product,
            "error": "بارکد الزامی است.",
        })
    if normalized_barcode != variant.barcode:
        existing = db.query(ProductVariant).filter(
            ProductVariant.barcode == normalized_barcode,
            ProductVariant.id != variant_id
        ).first()
        if existing:
            return templates.TemplateResponse(request, "admin/variant_form.html", {
                "variant": variant,
                "product": variant.product,
                "error": "بارکد تکراری است.",
            })
        variant.barcode = normalized_barcode

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
    variant.sku = sku.strip() if sku else None
    variant.tryon_details = tryon_details.strip() if tryon_details.strip() else None
    variant.reorder_point = reorder_point_int
    variant.reorder_quantity = reorder_quantity_int
    variant.weight_grams = weight_int
    variant.storage_location = storage_location.strip() if storage_location else None
    variant.size_system = size_system.strip() if size_system else None
    # Lenient where the add flow refuses: a legacy free-text colour already on
    # the row must not lock the whole edit. Valid codes still normalise.
    try:
        variant.color_code = _normalize_hex_color(color_code)
    except ValueError:
        variant.color_code = color_code.strip() if color_code and color_code.strip() else None
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
    from services.security import log_action
    log_action(db, "variant_demand_up", f"ثبت تقاضا برای تنوع #{variant.id}", request=request, target_type="variant", target_id=variant.id)
    return RedirectResponse(url=f"/admin/variants/{variant.id}/edit", status_code=303)


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
    from services.security import log_action
    log_action(db, "variant_demand_reset", f"صفر کردن تقاضای تنوع #{variant.id}", request=request, target_type="variant", target_id=variant.id)
    return RedirectResponse(url=f"/admin/variants/{variant.id}/edit", status_code=303)


@router.get("/barcodes/print", response_class=HTMLResponse)
async def admin_barcodes_print(
    request: Request,
    category: str = "",
    page: str = "1",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    page = page_arg(page)
    query = db.query(Product).filter(Product.is_active == True)

    if category:
        query = query.filter(Product.category == category)

    products = query.order_by(Product.name).all()
    categories = db.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    config = load_tag_config(db)
    tag_fit = calculate_a4_fit(config)

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

    # Keep one row per variant. The operator chooses the number of copies
    # instead of being forced to select one checkbox per unit in stock.
    barcode_items = []
    template_configs = {"default": config}
    for product in products:
        for variant in product.variants:
            if not variant.is_active:
                continue
            item = item_from_variant(variant, fmt)
            template_key = str(product.tag_template.id) if product.tag_template and product.tag_template.is_active else "default"
            item["tag_template_key"] = template_key
            item["tag_config"] = tag_template_config(product.tag_template, config)
            template_configs[template_key] = item["tag_config"]
            item["tag_template_name"] = product.tag_template.name if product.tag_template and product.tag_template.is_active else "قالب پیش‌فرض فروشگاه"
            printed = max(0, printed_counts.get(variant.id, 0))
            available = max(0, (variant.stock_quantity or 0) - (variant.reserved_quantity or 0))
            item["printed_count"] = printed
            item["available_quantity"] = available
            # Keep this value for compatibility with older templates, but label
            # printing is intentionally independent of current stock.
            item["unprinted_quantity"] = max(0, available - printed)
            item["printable_quantity"] = MAX_TAG_PRINT_QUANTITY
            item["is_out_of_stock"] = available <= 0
            item["already_printed"] = printed > 0
            barcode_items.append(item)

    barcode_items.sort(key=lambda x: (x["unprinted_quantity"] == 0, x["name"]))

    page_size = max(1, tag_fit["tags_per_page"])
    total_items = len(barcode_items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    page_items = barcode_items[start:start + page_size]

    # Render each product with its assigned layout. Products without an
    # assignment inherit the current store default.
    config = load_tag_config(db)
    store = get_store(db)
    millimeters_to_pixels = 96 / 25.4
    tag_width_px = config["tag_width_mm"] * millimeters_to_pixels
    tag_height_px = config["tag_height_mm"] * millimeters_to_pixels
    # Show the tag at real print size when it fits, otherwise scale it down
    # evenly; the preview frame is sized to the scaled tag so there is no
    # dead space or clipping around it.
    max_preview_width_px = 210
    max_preview_height_px = 150
    tag_preview_scale = min(
        1.0,
        max_preview_width_px / tag_width_px,
        max_preview_height_px / tag_height_px,
    )
    tag_preview_scale = round(max(0.2, tag_preview_scale), 3)
    tag_preview_width_px = round(tag_width_px * tag_preview_scale)
    tag_preview_height_px = round(tag_height_px * tag_preview_scale)
    rendered_items = []
    for item in page_items:
        rendered = dict(item)
        item_config = rendered.get("tag_config") or config
        item_fit = calculate_a4_fit(item_config)
        item_width_px = item_config["tag_width_mm"] * millimeters_to_pixels
        item_height_px = item_config["tag_height_mm"] * millimeters_to_pixels
        item_scale = min(1.0, max_preview_width_px / item_width_px, max_preview_height_px / item_height_px)
        rendered["tag_config"] = item_config
        rendered["tag_fit"] = item_fit
        rendered["tag_preview_scale"] = round(max(0.2, min(1.0, item_scale)), 3)
        rendered["tag_preview_width_px"] = round(item_width_px * rendered["tag_preview_scale"])
        rendered["tag_preview_height_px"] = round(item_height_px * rendered["tag_preview_scale"])
        rendered["tag_html"] = render_tag_html(item_config, rendered, store)
        rendered_items.append(rendered)

    return templates.TemplateResponse(request, "admin/barcode_print.html", {
        "barcode_items": rendered_items,
        "all_items_count": total_items,
        "page": page,
        "total_pages": total_pages,
        "categories": categories,
        "category_filter": category,
        "tag_config": config,
        "tag_fit": tag_fit,
        "tag_preview_scale": tag_preview_scale,
        "tag_preview_width_px": tag_preview_width_px,
        "tag_preview_height_px": tag_preview_height_px,
        "tag_template_configs": template_configs,
        "store": store,
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
    if not selected_ids:
        selected_ids = [value for value in str(form.get("selected_ids", "")).split(",") if value]
    category = str(form.get("category", ""))
    page = str(form.get("page", "1"))

    selected_quantities = _selected_quantities(form)

    selected_counts = {}
    for variant_id in selected_ids:
        try:
            variant_id_int = int(variant_id)
            selected_counts[variant_id_int] = selected_counts.get(variant_id_int, 0) + 1
        except (TypeError, ValueError):
            pass

    is_reprint = str(form.get("reprint", "")).lower() in {"1", "true", "yes"}
    batch_lines = []
    for variant_id, selected_count in selected_counts.items():
        requested_count = selected_quantities.get(variant_id, selected_count)
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == variant_id,
            ProductVariant.is_active == True,
        ).first()
        if not variant:
            continue

        key = f"barcode_printed_{variant_id}"
        existing = db.query(Settings).filter(Settings.key == key).first()
        try:
            printed_count = max(0, int(existing.value)) if existing and existing.value else 0
        except (TypeError, ValueError):
            printed_count = 0
        count = min(requested_count, MAX_TAG_PRINT_QUANTITY)
        if count <= 0:
            continue
        if not is_reprint:
            if existing:
                existing.value = str(printed_count + count)
            else:
                db.add(Settings(key=key, value=str(count)))
        batch_lines.append((variant, count, is_reprint))

    if batch_lines:
        default_config = load_tag_config(db)
        snapshot = {"default": default_config}
        for variant, _, _ in batch_lines:
            template = variant.product.tag_template if variant.product else None
            if template and template.is_active:
                snapshot[str(template.id)] = tag_template_config(template, default_config)
        batch = TagPrintBatch(
            operator_user_id=guard.id,
            template_snapshot=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            item_count=len(batch_lines),
            total_quantity=sum(line[1] for line in batch_lines),
        )
        db.add(batch)
        db.flush()
        for variant, count, line_is_reprint in batch_lines:
            db.add(TagPrintBatchLine(
                batch_id=batch.id,
                variant_id=variant.id,
                quantity=count,
                product_name=variant.product.name if variant.product else "",
                barcode=variant.barcode,
                sku=variant.sku,
                size=variant.size,
                color=variant.color,
                unit_price=variant.price,
                is_reprint=line_is_reprint,
            ))
        append_event(
            db,
            "TagBatchPrinted",
            "tag_print_batch",
            batch.id,
            idempotency_key=f"tag-batch:{batch.id}:printed",
            actor_user_id=guard.id,
            request_id=request.headers.get("X-Request-ID"),
            payload={"item_count": batch.item_count, "total_quantity": batch.total_quantity, "is_reprint": is_reprint},
        )

    db.commit()
    query_values = {"page": page, "category": category}
    if batch_lines:
        query_values["msg"] = "وضعیت چاپ تگ‌ها ثبت شد."
    else:
        query_values["err"] = "هیچ تنوعی برای این عملیات انتخاب نشده است."
    query = urlencode(query_values)
    return RedirectResponse(url=f"/admin/barcodes/print?{query}", status_code=303)


@router.post("/barcodes/reset", response_class=HTMLResponse)
async def admin_barcodes_reset(request: Request, db: Session = Depends(get_db)):
    """Reset printed counts for the selected tag copies only."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    selected_ids = form.getlist("selected_products")
    if not selected_ids:
        selected_ids = [value for value in str(form.get("selected_ids", "")).split(",") if value]
    category = str(form.get("category", ""))
    page = str(form.get("page", "1"))

    selected_quantities = _selected_quantities(form)

    selected_counts = {}
    for variant_id in selected_ids:
        try:
            variant_id_int = int(variant_id)
            selected_counts[variant_id_int] = selected_counts.get(variant_id_int, 0) + 1
        except (TypeError, ValueError):
            pass

    changed = False
    for variant_id, selected_count in selected_counts.items():
        count = min(selected_quantities.get(variant_id, selected_count), MAX_TAG_PRINT_QUANTITY)
        key = f"barcode_printed_{variant_id}"
        existing = db.query(Settings).filter(Settings.key == key).first()
        if not existing:
            continue
        try:
            printed_count = max(0, int(existing.value or 0))
        except (TypeError, ValueError):
            printed_count = 0
        remaining = max(0, printed_count - count)
        if remaining:
            existing.value = str(remaining)
        else:
            db.delete(existing)
        changed = True

    db.commit()
    query_values = {"page": page, "category": category}
    query_values["msg" if changed else "err"] = (
        "تعداد چاپ تگ‌ها بازگردانی شد." if changed else "هیچ تگ چاپ‌شده‌ای برای بازگردانی انتخاب نشده است."
    )
    query = urlencode(query_values)
    return RedirectResponse(url=f"/admin/barcodes/print?{query}", status_code=303)