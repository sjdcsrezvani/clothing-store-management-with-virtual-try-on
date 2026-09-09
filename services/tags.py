"""Validated product-tag configuration, A4 layout calculations, and rendering."""
from __future__ import annotations

import copy
import html
from datetime import datetime, timezone
import io
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any

from models import Settings, TagTemplate
from services.barcode import BARCODE_DENSITIES, BARCODE_DENSITY_DEFAULT, generate_barcode_image

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297
TAG_CONFIG_KEY = "tag_layout_config"
TAG_CONFIG_VERSION = 1
ALLOWED_BARCODE_MODES = {"real_barcode", "custom_image", "hidden"}
ALLOWED_LAYOUT_MODES = {"fixed", "auto"}
ALLOWED_ALIGNMENTS = {"left", "center", "right"}
ALLOWED_FIELDS = {
    "product_name",
    "barcode",
    "barcode_text",
    "price",
    "size",
    "color",
    "sku",
    "brand",
    "category",
    "store_name",
    "instagram",
    "product_image",
    "custom_text",
}
FIELD_LABELS = {
    "product_name": "نام محصول",
    "barcode": "تصویر بارکد",
    "barcode_text": "کد بارکد",
    "price": "قیمت فروش",
    "size": "سایز",
    "color": "رنگ",
    "sku": "کد SKU",
    "brand": "برند",
    "category": "دسته‌بندی",
    "store_name": "نام فروشگاه",
    "instagram": "اینستاگرام",
    "product_image": "تصویر محصول",
    "custom_text": "متن سفارشی",
}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TAG_ASSET_RE = re.compile(r"^/static/uploads/tag-assets/[A-Za-z0-9_.-]+$")


def _field(x, y, width, height, *, visible=True, font_size=8, bold=False,
           color="#27304A", align="center", z=0):
    return {
        "visible": visible,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "font_size": font_size,
        "bold": bold,
        "color": color,
        "align": align,
        "z": z,
    }


def _base_config(width=48, height=38, columns=4, rows=7, *, layout_mode="fixed"):
    width = float(width)
    height = float(height)
    content_width = width - 4
    half_width = content_width / 2
    footer_y = max(0, height - 3)
    # The barcode image (bars) and its code text are independent fields so
    # each can be shown, hidden, moved or removed on its own.
    barcode_top = 10.0
    barcode_image_height = min(6.0, max(4.0, height * 0.16))
    barcode_text_y = barcode_top + barcode_image_height + 0.7
    return {
        "version": TAG_CONFIG_VERSION,
        "preset": "kids_boutique",
        "tag_width_mm": width,
        "tag_height_mm": height,
        "margin_top_mm": 5,
        "margin_right_mm": 5,
        "margin_bottom_mm": 5,
        "margin_left_mm": 5,
        "gap_horizontal_mm": 2,
        "gap_vertical_mm": 2,
        "layout_mode": layout_mode,
        "columns": columns,
        "rows": rows,
        "background": "#FFFFFF",
        "border_color": "#D8CFC8",
        "border_width_mm": 0.3,
        "padding_mm": 1,
        "corner_radius_mm": 1.5,
        "barcode_mode": "real_barcode",
        "barcode_density": BARCODE_DENSITY_DEFAULT,
        "custom_image_path": None,
        "custom_text": "",
        "fields": {
            "store_name": _field(2, 1, content_width, 4, font_size=7, bold=True, color="#FF6B8A"),
            "product_name": _field(2, 5, content_width, 5, font_size=8, bold=True),
            "barcode": _field(2, barcode_top, width - 4, barcode_image_height, font_size=7, z=1),
            "barcode_text": _field(2, min(barcode_text_y, height - 4), content_width, 3, font_size=6, z=1),
            "price": _field(2, max(0, height - 16), content_width, 5, font_size=9, bold=True, color="#E85878"),
            "size": _field(2, max(0, height - 10), half_width, 4, font_size=7, align="right"),
            "color": _field(2 + half_width, max(0, height - 10), half_width, 4, font_size=7, align="left"),
            "sku": _field(2, footer_y, content_width, 2, font_size=6, visible=False),
            "brand": _field(2, footer_y, content_width, 2, font_size=6, visible=False),
            "category": _field(2, footer_y, content_width, 2, font_size=6, visible=False),
            "instagram": _field(2, footer_y, content_width, 2, font_size=5, visible=False, color="#777777"),
            "product_image": _field(2, 10, content_width, min(14, max(2, height - 12)), font_size=7, visible=False),
            "custom_text": _field(2, footer_y, content_width, 2, font_size=6, visible=False),
        },
    }


PRESETS = {
    "kids_boutique": _base_config(),
    "compact": _base_config(width=40, height=30, columns=4, rows=8),
    "large": _base_config(width=60, height=45, columns=3, rows=6),
    "custom": _base_config(),
}


def default_tag_config(preset: str = "kids_boutique") -> dict[str, Any]:
    """Return a fresh default configuration, never a shared mutable object."""
    return copy.deepcopy(PRESETS.get(preset, PRESETS["kids_boutique"]))


def _number(value, name, minimum, maximum, *, integer=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} is invalid")
    try:
        converted = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is invalid")
    if not math.isfinite(converted) or converted < minimum or converted > maximum:
        raise ValueError(f"{name} is outside the allowed range")
    if integer and isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} is invalid")
    return converted


def _validate_color(value, name):
    if not isinstance(value, str) or not _COLOR_RE.fullmatch(value):
        raise ValueError(f"{name} must be a six-digit hexadecimal color")
    return value.upper()


def calculate_a4_fit(config: dict[str, Any]) -> dict[str, Any]:
    """Calculate the usable A4 grid from a validated tag configuration."""
    available_width = A4_WIDTH_MM - config["margin_left_mm"] - config["margin_right_mm"]
    available_height = A4_HEIGHT_MM - config["margin_top_mm"] - config["margin_bottom_mm"]
    gap_x = config["gap_horizontal_mm"]
    gap_y = config["gap_vertical_mm"]
    max_columns = int(math.floor((available_width + gap_x + 1e-9) / (config["tag_width_mm"] + gap_x)))
    max_rows = int(math.floor((available_height + gap_y + 1e-9) / (config["tag_height_mm"] + gap_y)))
    if max_columns < 1 or max_rows < 1:
        raise ValueError("این اندازه تگ در صفحه A4 جا نمی‌شود")
    columns = config["columns"] if config["layout_mode"] == "fixed" else max_columns
    rows = config["rows"] if config["layout_mode"] == "fixed" else max_rows
    if columns < 1 or rows < 1 or columns > max_columns or rows > max_rows:
        raise ValueError("تعداد سطر یا ستون انتخاب‌شده در صفحه A4 جا نمی‌شود")
    used_width = columns * config["tag_width_mm"] + (columns - 1) * gap_x
    used_height = rows * config["tag_height_mm"] + (rows - 1) * gap_y
    return {
        "columns": columns,
        "rows": rows,
        "max_columns": max_columns,
        "max_rows": max_rows,
        "tags_per_page": columns * rows,
        "used_width_mm": round(used_width, 2),
        "used_height_mm": round(used_height, 2),
        "unused_width_mm": round(available_width - used_width, 2),
        "unused_height_mm": round(available_height - used_height, 2),
        "available_width_mm": round(available_width, 2),
        "available_height_mm": round(available_height, 2),
    }


def _migrate_legacy_barcode_fields(merged_fields: dict[str, Any], raw_fields: dict[str, Any]) -> None:
    """Split the old combined barcode field into image + separate code text.

    Older saved layouts had a single "barcode" field that rendered both the
    bars and the code digits under them. Keep that look by placing the new
    code-text field in the same bottom band of the old box until the owner
    repositions it.
    """
    if "barcode" not in raw_fields or "barcode_text" in raw_fields:
        return
    bar = merged_fields.get("barcode")
    text = merged_fields.get("barcode_text")
    if not bar or not text:
        return
    text["visible"] = bool(bar["visible"])
    text["x"] = bar["x"]
    text["width"] = bar["width"]
    text["height"] = min(bar["height"], max(2.0, bar["height"] * 0.34))
    text["y"] = max(0.0, bar["y"] + bar["height"] - text["height"])
    text["font_size"] = min(6.5, max(5.0, text["font_size"]))
    text["color"] = bar["color"]
    text["align"] = "center"
    text["z"] = bar["z"] + 1


def validate_tag_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a client-supplied tag configuration."""
    if not isinstance(raw, dict):
        raise ValueError("تنظیمات تگ معتبر نیست")
    config = default_tag_config()
    try:
        config["version"] = int(raw.get("version", TAG_CONFIG_VERSION))
    except (TypeError, ValueError):
        raise ValueError("نسخه تنظیمات تگ نامعتبر است")
    if config["version"] != TAG_CONFIG_VERSION:
        raise ValueError("نسخه تنظیمات تگ پشتیبانی نمی‌شود")

    for key, minimum, maximum in (
        ("tag_width_mm", 25, 100), ("tag_height_mm", 20, 100),
        ("margin_top_mm", 0, 50), ("margin_right_mm", 0, 50),
        ("margin_bottom_mm", 0, 50), ("margin_left_mm", 0, 50),
        ("gap_horizontal_mm", 0, 20), ("gap_vertical_mm", 0, 20),
        ("padding_mm", 0, 10), ("corner_radius_mm", 0, 10),
        ("border_width_mm", 0, 3),
    ):
        config[key] = _number(raw.get(key, config[key]), key, minimum, maximum)

    config["layout_mode"] = raw.get("layout_mode", config["layout_mode"])
    if config["layout_mode"] not in ALLOWED_LAYOUT_MODES:
        raise ValueError("حالت چیدمان نامعتبر است")
    config["columns"] = _number(raw.get("columns", config["columns"]), "columns", 0, 20, integer=True)
    config["rows"] = _number(raw.get("rows", config["rows"]), "rows", 0, 30, integer=True)
    if config["layout_mode"] == "fixed" and (config["columns"] == 0 or config["rows"] == 0):
        raise ValueError("در چیدمان ثابت تعداد سطر و ستون الزامی است")
    if config["margin_left_mm"] + config["margin_right_mm"] >= A4_WIDTH_MM:
        raise ValueError("حاشیه‌های افقی معتبر نیستند")
    if config["margin_top_mm"] + config["margin_bottom_mm"] >= A4_HEIGHT_MM:
        raise ValueError("حاشیه‌های عمودی معتبر نیستند")

    config["background"] = _validate_color(raw.get("background", config["background"]), "background")
    config["border_color"] = _validate_color(raw.get("border_color", config["border_color"]), "border_color")
    config["barcode_mode"] = raw.get("barcode_mode", config["barcode_mode"])
    if config["barcode_mode"] not in ALLOWED_BARCODE_MODES:
        raise ValueError("حالت بارکد نامعتبر است")
    config["barcode_density"] = raw.get("barcode_density", config["barcode_density"])
    if config["barcode_density"] not in BARCODE_DENSITIES:
        raise ValueError("تراکم بارکد نامعتبر است")

    custom_path = raw.get("custom_image_path", config.get("custom_image_path"))
    if custom_path:
        if not isinstance(custom_path, str) or not _TAG_ASSET_RE.fullmatch(custom_path):
            raise ValueError("تصویر سفارشی نامعتبر است")
    config["custom_image_path"] = custom_path or None

    custom_text = raw.get("custom_text", "")
    if not isinstance(custom_text, str):
        raise ValueError("متن سفارشی نامعتبر است")
    config["custom_text"] = custom_text[:120]

    fields = raw.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("فیلدهای تگ معتبر نیستند")
    if len(fields) > len(ALLOWED_FIELDS):
        raise ValueError("تعداد فیلدهای تگ بیش از حد مجاز است")
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise ValueError("فیلد ناشناخته در تنظیمات تگ")
    merged_fields = copy.deepcopy(config["fields"])
    for name, incoming in fields.items():
        if not isinstance(incoming, dict):
            raise ValueError(f"تنظیم فیلد {name} معتبر نیست")
        field = merged_fields[name]
        visible = incoming.get("visible", field["visible"])
        bold = incoming.get("bold", field["bold"])
        if not isinstance(visible, bool) or not isinstance(bold, bool):
            raise ValueError(f"تنظیم فیلد {name} معتبر نیست")
        field["visible"] = visible
        field["bold"] = bold
        for key, minimum, maximum in (
            ("x", 0, config["tag_width_mm"]), ("y", 0, config["tag_height_mm"]),
            ("width", 2, config["tag_width_mm"]), ("height", 2, config["tag_height_mm"]),
            ("font_size", 4, 48),
        ):
            field[key] = _number(incoming.get(key, field[key]), f"{name}.{key}", minimum, maximum)
        if field["x"] + field["width"] > config["tag_width_mm"] or field["y"] + field["height"] > config["tag_height_mm"]:
            raise ValueError(f"فیلد {name} خارج از محدوده تگ است")
        field["color"] = _validate_color(incoming.get("color", field["color"]), f"{name}.color")
        field["align"] = incoming.get("align", field["align"])
        if field["align"] not in ALLOWED_ALIGNMENTS:
            raise ValueError(f"تراز فیلد {name} نامعتبر است")
        field["z"] = _number(incoming.get("z", field["z"]), f"{name}.z", -100, 100, integer=True)
    _migrate_legacy_barcode_fields(merged_fields, fields)
    config["fields"] = merged_fields
    preset = raw.get("preset", "custom")
    if not isinstance(preset, str):
        raise ValueError("قالب تگ نامعتبر است")
    config["preset"] = preset[:40]
    calculate_a4_fit(config)
    return config


def load_tag_config(db) -> dict[str, Any]:
    row = db.query(Settings).filter(Settings.key == TAG_CONFIG_KEY).first()
    if not row or not row.value:
        return default_tag_config()
    try:
        validated = validate_tag_config(json.loads(row.value))
        # An all-hidden layout is not an editable or printable default. Treat
        # it as corrupted and restore the usable Kids Boutique baseline.
        if not any(field.get("visible") for field in validated.get("fields", {}).values()):
            return default_tag_config("kids_boutique")
        return validated
    except (ValueError, TypeError, json.JSONDecodeError):
        return default_tag_config("kids_boutique")


def save_tag_config(db, config: dict[str, Any]) -> dict[str, Any]:
    validated = validate_tag_config(config)
    payload = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
    row = db.query(Settings).filter(Settings.key == TAG_CONFIG_KEY).first()
    if row:
        row.value = payload
    else:
        db.add(Settings(key=TAG_CONFIG_KEY, value=payload))
    return validated


def list_tag_templates(db) -> list[TagTemplate]:
    return db.query(TagTemplate).filter(TagTemplate.is_active == True).order_by(TagTemplate.name.asc()).all()


def tag_template_config(template: TagTemplate | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not template or not template.config_json:
        return copy.deepcopy(fallback)
    try:
        validated = validate_tag_config(json.loads(template.config_json))
        if not any(field.get("visible") for field in validated.get("fields", {}).values()):
            return copy.deepcopy(fallback)
        return validated
    except (ValueError, TypeError, json.JSONDecodeError):
        return copy.deepcopy(fallback)


def save_tag_template(db, name: str, config: dict[str, Any], template_id: int | None = None) -> TagTemplate:
    clean_name = " ".join(str(name or "").split())[:100]
    if not clean_name:
        raise ValueError("نام قالب الزامی است")
    validated = validate_tag_config(config)
    query = db.query(TagTemplate).filter(TagTemplate.name == clean_name)
    if template_id is not None:
        query = query.filter(TagTemplate.id != template_id)
        if query.first():
            raise ValueError("قالبی با این نام از قبل وجود دارد")
    else:
        active_duplicate = query.filter(TagTemplate.is_active == True).first()
        if active_duplicate:
            raise ValueError("قالبی با این نام از قبل وجود دارد")
    template = db.query(TagTemplate).filter(TagTemplate.id == template_id, TagTemplate.is_active == True).first() if template_id is not None else None
    if template_id is not None and not template:
        raise ValueError("قالب انتخاب‌شده یافت نشد")
    if not template and template_id is None:
        # A soft-deleted layout can be recreated by name. Reusing that row
        # preserves old product references and avoids the unique-name conflict.
        template = db.query(TagTemplate).filter(TagTemplate.name == clean_name).first()
        if template:
            template.is_active = True
            template.config_json = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
            template.updated_at = datetime.now(timezone.utc)
    if not template:
        template = TagTemplate(name=clean_name, config_json=json.dumps(validated, ensure_ascii=False, separators=(",", ":")))
        db.add(template)
        db.flush()
    else:
        template.name = clean_name
        template.config_json = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
        template.updated_at = datetime.now(timezone.utc)
    return template


_IMG_FIT_STYLE = (
    "max-width:100%;max-height:100%;width:auto;height:auto;"
    "object-fit:contain;display:block;margin:0 auto;"
)


def _item_values(item: dict[str, Any], store: dict[str, Any], custom_text: str) -> dict[str, str]:
    return {
        "product_name": str(item.get("product_name") or ""),
        "barcode": str(item.get("barcode") or ""),
        "barcode_text": str(item.get("barcode") or ""),
        "price": str(item.get("price_display") or item.get("price") or ""),
        "size": str(item.get("size") or ""),
        "color": str(item.get("color") or ""),
        "sku": str(item.get("sku") or ""),
        "brand": str(item.get("brand") or ""),
        "category": str(item.get("category") or ""),
        "store_name": str(store.get("name") or ""),
        "instagram": str(store.get("instagram") or ""),
        "custom_text": custom_text or "",
    }


def render_tag_html(config: dict[str, Any], item: dict[str, Any], store: dict[str, Any]) -> str:
    """Render one tag for settings preview and printing."""
    config = validate_tag_config(config)
    values = _item_values(item, store, config.get("custom_text", ""))
    children = []
    for name, field in sorted(config["fields"].items(), key=lambda pair: (pair[1]["z"], pair[0])):
        if not field["visible"]:
            continue
        if name == "barcode":
            if config["barcode_mode"] == "hidden":
                continue
            if config["barcode_mode"] == "real_barcode" and not values["barcode"]:
                continue
            if config["barcode_mode"] == "real_barcode":
                code_value = values["barcode"]
                image_path = generate_barcode_image(code_value, density=config["barcode_density"])
                if not image_path:
                    continue
                # The raster contains ONLY the vertical bars. The digits are a
                # separate field ("barcode_text") the owner can move and show
                # or hide independently.
                content = (f'<img class="tag-barcode-image" style="{_IMG_FIT_STYLE}" '
                           f'src="{html.escape(image_path, quote=True)}" alt="" aria-hidden="true">')
            else:
                image_path = config.get("custom_image_path")
                if not image_path:
                    continue
                content = (f'<img class="tag-custom-image" style="{_IMG_FIT_STYLE}" '
                           f'src="{html.escape(image_path, quote=True)}" alt="تصویر سفارشی">')
            kind = "tag-field tag-image-field"
        elif name == "product_image":
            image_path = item.get("image_path")
            if not image_path:
                continue
            content = (f'<img class="tag-product-image" style="{_IMG_FIT_STYLE}" '
                       f'src="{html.escape(str(image_path), quote=True)}" alt="">')
            kind = "tag-field tag-image-field"
        else:
            value = values.get(name, "")
            if not value:
                continue
            content = html.escape(value)
            kind = "tag-field"
        justify = {"left": "flex-start", "center": "center", "right": "flex-end"}[field["align"]]
        style = (
            f'position:absolute;display:flex;align-items:center;overflow:hidden;box-sizing:border-box;'
            f'left:{field["x"]}mm;top:{field["y"]}mm;width:{field["width"]}mm;height:{field["height"]}mm;'
            f'color:{field["color"]};font-size:{field["font_size"]}pt;font-weight:{700 if field["bold"] else 400};'
            f'text-align:{field["align"]};justify-content:{justify};line-height:1.2;'
        )
        children.append(f'<div class="{kind}" data-field="{name}" style="{style}">{content}</div>')
    root_style = (
        f'position:relative;overflow:hidden;box-sizing:border-box;'
        f'width:{config["tag_width_mm"]}mm;height:{config["tag_height_mm"]}mm;'
        f'padding:{config["padding_mm"]}mm;background:{config["background"]};'
        f'border:{config["border_width_mm"]}mm solid {config["border_color"]};'
        f'border-radius:{config["corner_radius_mm"]}mm;'
    )
    return f'<div class="tag-render" dir="rtl" style="{root_style}">{"".join(children)}</div>'


def item_from_variant(variant, fmt=None) -> dict[str, Any]:
    product = variant.product
    return {
        "variant_id": variant.id,
        "barcode": variant.barcode,
        "product_name": product.name if product else "",
        "name": variant.display_name,
        "price": variant.price,
        "price_display": fmt(variant.price) if fmt else f"{variant.price:,}",
        "size": variant.size,
        "color": variant.color,
        "sku": variant.sku,
        "brand": product.brand if product else "",
        "category": product.category if product else "",
        "image_path": variant.image_path or (product.image_path if product else None),
        "reserved_quantity": variant.reserved_quantity or 0,
        "available_quantity": variant.available_quantity,
    }


def validate_tag_image(data: bytes, filename: str, content_type: str | None = None) -> str:
    """Validate raster image bytes and return a safe extension."""
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError("تصویر باید کوچک‌تر از ۵ مگابایت باشد")
    suffix = Path(filename or "").suffix.lower()
    allowed = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if suffix not in allowed or (content_type and content_type not in {"image/png", "image/jpeg", "image/webp"}):
        raise ValueError("فقط تصویر PNG، JPG یا WebP مجاز است")
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        if image.format != allowed[suffix] or image.width > 3000 or image.height > 3000:
            raise ValueError("ابعاد یا قالب تصویر مجاز نیست")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("فایل تصویر معتبر نیست") from exc
    return allowed[suffix].lower().replace("jpeg", "jpg")


def save_tag_image(data: bytes, filename: str, content_type: str | None = None) -> str:
    extension = validate_tag_image(data, filename, content_type)
    directory = Path("static/uploads/tag-assets")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.{extension}"
    path.write_bytes(data)
    return f"/static/uploads/tag-assets/{path.name}"
