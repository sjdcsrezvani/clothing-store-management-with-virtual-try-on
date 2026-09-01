"""Virtual Try-On — standalone, no customer required. Phone-friendly APIs."""
import asyncio
import base64
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import random

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Product, ProductVariant, GeneratedImage, to_english_digits
from services._common import check_admin, fmt, jalali_str
from services.image_gen import ImageGenService, ImageGenerationError, composite_logo, MAIN_PROMPT
from services.security import require_api_token, tryon_can_generate, tryon_record_generation, tryon_daily_remaining, log_action
from services.templating import templates
from config import TRYON_BACKGROUNDS, TRYON_POSE_MODES, TRYON_FACE_MODES

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin")
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_api_token)])
image_gen = ImageGenService()

UPLOAD_DIR = Path("static/uploads")
GENERATED_DIR = UPLOAD_DIR / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# Outfit modes — control whether the AI swaps just the target product or
# also rebuilds the rest of the child's outfit to match it.
OUTFIT_MODE_PRODUCT_ONLY = "product_only"
OUTFIT_MODE_HEAD_TO_TOE = "head_to_toe"
VALID_OUTFIT_MODES = {OUTFIT_MODE_PRODUCT_ONLY, OUTFIT_MODE_HEAD_TO_TOE}

# ── Target product categories ──
# The category tells the model what the product is actually made of, so the
# outfit rule never contradicts the product photo (a generic "never make a
# matching bottom" rule is wrong for pants and sets, and an "upper-body piece"
# assumption is wrong for jeans). One category dropdown + a compact per-category
# rule at the prompt end — instead of separate giant prompts per category.
CATEGORY_UPPER = "upper"      # t-shirt, sweatshirt, hoodie, sweater — single top
CATEGORY_LOWER = "lower"      # jeans, pants, shorts, skirt — single bottom
CATEGORY_SET = "set"          # top + bottom sold together (tee+shorts, sweatshirt+sweatpants)
CATEGORY_LAYERED = "layered"  # outer upper + visible inner upper (jacket+tee, hoodie+tee)
CATEGORY_DRESS = "dress"      # one-piece (dress/pinafore/overalls), may include a visible under-layer tee
VALID_CATEGORIES = {CATEGORY_UPPER, CATEGORY_LOWER, CATEGORY_SET, CATEGORY_LAYERED, CATEGORY_DRESS}

# Each rule starts with the same "OUTFIT RULE" marker and lands as the LAST text
# the model reads (after MAIN_PROMPT, background, pose, details) so it overrides
# any conflicting generic instruction via recency bias.

PRODUCT_ONLY_UPPER = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a single UPPER-BODY garment (top / t-shirt / "
    "sweatshirt / hoodie / sweater). Change ONLY that top: replace the person's "
    "existing top with EXACTLY the target — same category, cut, neckline, sleeves, "
    "fabric, print, and color. Everything else — the bottom, shoes, and "
    "accessories — stays EXACTLY as in the person's photo: same color, same "
    "fabric, same fit, same style. Do NOT add a matching bottom (a sweatshirt does "
    "NOT come with sweatpants, a t-shirt does NOT come with matching shorts) and "
    "do NOT add an inner layer that is not in the garment photo. Pay attention "
    "ONLY to the garment itself — ignore the model, background in the garment photo."
)
PRODUCT_ONLY_LOWER = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a single LOWER-BODY garment (jeans / pants / shorts / "
    "skirt). Change ONLY the bottom: replace the person's existing bottoms with "
    "EXACTLY the target — same category, cut, length, fit, fabric, wash, print, and "
    "color. Everything else — the top, outer layer, shoes, and accessories — stays "
    "EXACTLY as in the person's photo: same color, same fabric, same fit, same "
    "style. Do NOT add a matching top (jeans do NOT come with a matching denim "
    "jacket, sweatpants do NOT come with a matching sweatshirt) and never put a "
    "second bottom on the person. Pay attention ONLY to the garment itself — "
    "ignore the model, background in the garment photo."
)
PRODUCT_ONLY_SET = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a complete two-piece SET — top + bottom together. "
    "Change ONLY the set: the person wears BOTH pieces exactly as shown — same top, "
    "same bottom, same fabric, same color, same print, same proportions. Everything "
    "else — shoes, accessories, outer layers — stays EXACTLY as in the person's "
    "photo. Do NOT swap the set's bottom for jeans or anything else, "
    "do NOT add a piece made from the set's fabric. Pay attention ONLY to "
    "the two garment pieces — ignore the model, background in the garment photo."
)
PRODUCT_ONLY_LAYERED = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a layered outfit — an OUTER upper piece (jacket / "
    "hoodie / cardigan / coat) worn over a visible INNER piece (t-shirt / shirt) — "
    "BOTH pieces together are the target garment(product). Change ONLY those two upper pieces: "
    "reproduce the outer and the inner exactly — the outer's cut, fabric, print, "
    "and color, and the inner layer exactly as visible at the neckline, cuffs, and "
    "hem. Everything else — the bottom, shoes, and accessories — stays EXACTLY as "
    "in the person's photo. Never drop the inner layer, never pair the outer with a "
    "different top, never add a third piece, never fuse the two layers into one. "
    "Pay attention ONLY to the two garment layers — ignore the model, background in the garment photo."
)
PRODUCT_ONLY_DRESS = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a ONE-PIECE garment (dress / pinafore / overalls), and "
    "may include a visible under-layer (a t-shirt worn beneath it, as shown in the "
    "photo). Change ONLY that one piece: replace the person's existing one-piece "
    "with EXACTLY the target — same shape, cut, straps, length, print, and color — "
    "and keep the under-layer exactly as shown if one is visible. The piece must "
    "stay ONE garment: NEVER split it into a separate top + pants, NEVER invent "
    "pants beneath it, NEVER add a t-shirt the garment(product) photo does not show. "
    "Everything else — legs, shoes, accessories — stays EXACTLY as in the person's "
    "photo. Pay attention ONLY to the one-piece garment — ignore the model, "
    "background, in the garment(product) photo."
)

HEAD_TO_TOE_UPPER = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target garment (image 2) is a single upper-body piece — copy its category, "
    "color, fabric, and print exactly. Build a full coordinated outfit around it. "
    "CRITICAL — the bottom (pants/shorts/skirt) must NEVER match the target garment's fabric or color."
    "The bottom is ALWAYS a different category, different fabric, and different color. "
    "Concrete rules: Sweatshirt → jeans, chinos, or cargo pants (never sweatpants). "
    "T-shirt → jeans, chino shorts, or linen pants. "
    "Hoodie → jeans or joggers (never matching hoodie-fabric pants). "
    "Shoes must complement the new bottom — clean sneakers with jeans, "
    "sandals or canvas shoes with shorts. "
    "No recolored clones, no matching-sweatsuit looks. "
    "Pay attention ONLY to the garment itself — ignore the model and background in "
    "the garment(product) photo. "
)
HEAD_TO_TOE_LOWER = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a single LOWER-BODY piece — jeans / pants / shorts / "
    "skirt. Copy its category, cut, fit, color, fabric, wash, and print exactly onto "
    "the person's bottom. Build a full coordinated outfit around it. "
    "CRITICAL — the top must NEVER match the bottom's fabric or print, and must not "
    "echo its color head-to-toe (no denim-on-denim, no matching tracksuit). The top "
    "is ALWAYS a different category, different fabric, and different color. "
    "Concrete rules: Jeans → white/neutral t-shirt, henley, or plain shirt. "
    "Chinos/cargo → plain t-shirt or pullover sweater. Shorts → t-shirt or polo. "
    "Skirt → soft blouse or plain t-shirt. "
    "Shoes must complement the look — clean sneakers with jeans, sandals or canvas "
    "shoes with shorts, ballet flats with skirts. "
    "Only one bottom on the person — never two bottoms, never the garment(product) photo's "
    "matching look copied whole. "
    "Pay attention ONLY to the garment itself — ignore the model and background in "
    "the garment(product) photo. "
)
HEAD_TO_TOE_SET = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The product shown in image 2 is a complete two-piece SET (top + bottom together). "
    "Reproduce the ENTIRE set exactly as shown in the product photo — same top, same "
    "bottom, same fabric, same color, same print, same proportions on both pieces. "
    "Do NOT change the pants/shorts/skirt — they are part of the product. "
    "Do NOT swap the bottom for jeans or anything else. "
    "The ONLY thing you may change is the SHOES — pick shoes that complement "
    "the set (clean sneakers, canvas shoes, or sandals depending on style) and "
    "do NOT copy the shoes from the person's photo if they clash with the set. "
    "Pay attention ONLY to the two garment pieces. "
)
HEAD_TO_TOE_LAYERED = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a layered outfit — an OUTER upper piece (jacket / "
    "hoodie / cardigan) over a visible INNER piece (t-shirt / shirt) — BOTH upper "
    "pieces together are the product; reproduce both EXACTLY (outer cut, fabric, "
    "print, and color + inner layer exactly as visible at the neckline and cuffs). "
    "Build a full coordinated outfit around them. "
    "CRITICAL — the bottom must NEVER match either upper layer's fabric or color: it "
    "is ALWAYS a different category, different fabric, different color. Never drop "
    "the inner layer, never fuse the two layers into one garment, never swap the "
    "outer for a different category. "
    "Concrete rules: Hoodie + tee → jeans or chinos (never matching-print "
    "sweatpants). Jacket + tee → slim jeans, chinos, or cargo. "
    "Shoes: clean sneakers or boots that fit the look. "
    "No matching tracksuit look, no fabric clones — these are real separate "
    "garments a customer would buy. "
    "Pay attention ONLY to the two layers — ignore the model and background in the "
    "garment(product) photo."
)
HEAD_TO_TOE_DRESS = (
    "OUTFIT RULE (this is critical — do NOT ignore): "
    "The target (image 2) is a ONE-PIECE garment (dress / pinafore / overall), and "
    "may include the visible under-layer (a t-shirt beneath it) shown in the garment(product) "
    "photo. Reproduce the whole piece EXACTLY — shape, cut, straps, length, print, "
    "and color — and keep any under-layer exactly as visible. "
    "It must stay ONE piece on the person: NEVER split it into a separate top + "
    "bottom, NEVER invent pants beneath it, NEVER add a t-shirt that is not in the "
    "product photo. "
    "The rest of the outfit stays minimal and neutral: coordinate only the SHOES "
    "(and, if the legs need covering, optionally simple tights or leggings in a "
    "neutral or complementary tone — NEVER the garment's own fabric or print). "
    "Shoes: ballet flats, clean sneakers, or sandals to suit the style. "
    "Pay attention ONLY to the one-piece — ignore the model and background in the "
    "garment(product) photo."
)

_OUTFIT_RULES = {
    OUTFIT_MODE_PRODUCT_ONLY: {
        CATEGORY_UPPER: PRODUCT_ONLY_UPPER,
        CATEGORY_LOWER: PRODUCT_ONLY_LOWER,
        CATEGORY_SET: PRODUCT_ONLY_SET,
        CATEGORY_LAYERED: PRODUCT_ONLY_LAYERED,
        CATEGORY_DRESS: PRODUCT_ONLY_DRESS,
    },
    OUTFIT_MODE_HEAD_TO_TOE: {
        CATEGORY_UPPER: HEAD_TO_TOE_UPPER,
        CATEGORY_LOWER: HEAD_TO_TOE_LOWER,
        CATEGORY_SET: HEAD_TO_TOE_SET,
        CATEGORY_LAYERED: HEAD_TO_TOE_LAYERED,
        CATEGORY_DRESS: HEAD_TO_TOE_DRESS,
    },
}


def _outfit_clause(outfit_mode: str, category: str) -> str:
    """Pick the category-aware outfit rule for the selected mode.
    Unknown mode/category fall back to product-only + upper."""
    rules = _OUTFIT_RULES.get(outfit_mode) or _OUTFIT_RULES[OUTFIT_MODE_PRODUCT_ONLY]
    return rules.get(category) or rules[CATEGORY_UPPER]

# Background key that triggers a uniform-random pick across the catalog.
RANDOM_BG_KEY = "random"

# In-memory try-on state (single admin, no session). The kid photo is kept only
# in RAM; it is materialized into the operating system's temporary directory for
# the duration of a generation request, never under static/.
_tryon = {
    "kid_photo_bytes": None,
    "kid_photo_content_type": "image/jpeg",
    "kid_photo_preview": None,  # data URL for the current page only
    "scanned": [],               # list of product dicts
    "last_gen_url": None,     # latest generated image URL
    "last_gen_path": None,    # latest generated image disk path
    "last_gen_engine": None,  # engine_id of last generation (for display)
    "last_gen_prompt": None,  # full prompt of last generation (for save audit)
}


def _set_kid_photo(raw: bytes, content_type: str = "image/jpeg") -> None:
    """Keep the uploaded kid photo in memory, not in static/uploads."""
    if not raw:
        raise ValueError("Empty kid photo")
    mime = content_type if content_type and content_type.startswith("image/") else "image/jpeg"
    _tryon["kid_photo_bytes"] = raw
    _tryon["kid_photo_content_type"] = mime
    _tryon["kid_photo_preview"] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _kid_photo_temp_path(raw: Optional[bytes] = None, content_type: str = "image/jpeg") -> Optional[str]:
    """Write a RAM-held kid photo to an OS temp file for one provider request."""
    raw = _tryon["kid_photo_bytes"] if raw is None else raw
    if not raw:
        return None
    ext = {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }.get(content_type, ".jpg")
    fd, path = tempfile.mkstemp(prefix="tryon_kid_", suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    except Exception:
        os.close(fd)
        Path(path).unlink(missing_ok=True)
        raise
    return path


def _remove_temp_file(path: Optional[str]) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def _clear_kid_photo() -> None:
    _tryon["kid_photo_bytes"] = None
    _tryon["kid_photo_content_type"] = "image/jpeg"
    _tryon["kid_photo_preview"] = None


class ScanBarcodeRequest(BaseModel):
    barcode: str


class GenerateRequest(BaseModel):
    background: str = ""
    pose: str = "preserve"
    face: str = "preserve"  # face handling: preserve / beautify / beautify_expression
    mode: str = OUTFIT_MODE_PRODUCT_ONLY
    category: str = CATEGORY_UPPER  # product type: upper/lower/set/layered/dress
    barcodes: list[str] = []
    kid_photo_base64: str = ""  # optional: embed kid photo in the same request


def _variant_image_path(variant: ProductVariant) -> Optional[str]:
    """Real garment photo of a specific variant as a disk path (skips barcode images).
    Returns None if the variant has no usable product photo."""
    if variant.image_path and "/uploads/barcodes/" not in variant.image_path:
        return variant.image_path.lstrip("/")
    return None


def _product_image_paths(product: Product, barcode: Optional[str] = None) -> list[str]:
    """Get real garment photos of a product as disk paths (skips barcode images).
    When `barcode` is given, the scanned variant's own photo is preferred first
    (e.g. the red variant's photo when red was scanned). Other variants' photos
    are not included so the AI isn't confused by a different color. Falls back to
    the product-level image, then to one variant image only, if no specific
    variant photo is available."""
    paths = []
    # 1) Prefer the scanned variant's own photo.
    if barcode:
        for variant in product.variants:
            if variant.barcode == barcode:
                p = _variant_image_path(variant)
                if p and p not in paths:
                    paths.append(p)
                break
    # 2) Fall back to the product-level photo if no variant photo matched.
    if not paths and product.image_path and "/uploads/barcodes/" not in product.image_path:
        paths.append(product.image_path.lstrip("/"))
    # 3) Last resort: one real variant photo only. Never send every color as
    # fallback: that recreates the old cost/confusion problem. The caller still
    # gets a usable reference when possible, but the request stays bounded.
    if not paths:
        for variant in product.variants:
            p = _variant_image_path(variant)
            if p:
                paths.append(p)
                break
    return paths


def _background_prompt(key: str) -> str:
    """Resolve a background-select key to its (prompt, props) tuple.
    Empty key → ("", []). RANDOM_BG_KEY → uniform-random pick across all options
    in TRYON_BACKGROUNDS (kids and teens). Unknown key → ("", []).
    Returns the base backdrop prompt and a props list."""
    if not key:
        return "", []
    if key == RANDOM_BG_KEY:
        all_options = [o for g in TRYON_BACKGROUNDS for o in g["options"]]
        if not all_options:
            return "", []
        chosen = random.choice(all_options)
        return chosen.get("prompt", ""), chosen.get("props", []) or []
    for group in TRYON_BACKGROUNDS:
        for option in group["options"]:
            if option["key"] == key:
                return option.get("prompt", ""), option.get("props", []) or []
    return "", []


def _pick_props(props: list[str], n_min: int = 1, n_max: int = 2) -> list[str]:
    """Pick a random subset of props so each generation feels different."""
    if not props:
        return []
    n = min(len(props), random.randint(n_min, n_max))
    return random.sample(props, n)


def _pose_clause(key: str) -> str:
    """Resolve a pose-mode key to its prompt clause. Unknown / empty → ""."""
    if not key:
        return ""
    for mode in TRYON_POSE_MODES:
        if mode["key"] == key:
            return mode["prompt_clause"]
    return ""


def _face_clause(key: str) -> str:
    """Resolve a face-mode key to its FACE RULE clause. Unknown / empty → ""."""
    if not key:
        return ""
    for mode in TRYON_FACE_MODES:
        if mode["key"] == key:
            return mode["prompt_clause"]
    return ""


def _clean_garment_details(detail: str) -> str:
    """Strip Gemini template boilerplate out of variant `tryon_details` before it
    is embedded in the prompt. The admin pastes the full Gemini output
    (CATEGORY: / VARIANT DESCRIPTION: / IMPORTANT PRESERVATION DETAILS: /
    CONFIDENCE:) but only the descriptive content belongs in the prompt —
    labels like "CONFIDENCE: High" read like instructions to the model and
    waste input tokens. Safe for hand-written descriptions: it only removes the
    known template markers."""
    if not detail:
        return ""
    lines = detail.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = []
    # Everything on/after the CONFIDENCE line is template output — drop it.
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("confidence:"):
            break
        # Drop the label line itself; keep its content.
        if s.lower().startswith("category:"):
            continue
        for label in ("variant description:", "important preservation details:"):
            if s.lower().startswith(label):
                s = s[len(label):].strip()
                break
        if s:
            kept.append(s)
    return " ".join(kept).strip()


def _build_tryon_prompt(product_names: str,
                        background_key: str = "", pose_key: str = "preserve",
                        outfit_mode: str = OUTFIT_MODE_PRODUCT_ONLY,
                        garment_details=None, category: str = CATEGORY_UPPER,
                        face_key: str = "preserve") -> tuple[str, str, str]:
    """Build the three prompt parts so the final assembly is:
        prefix + MAIN_PROMPT + mid(background → pose → details → face) + suffix(outfit rule)
    The outfit rule (suffix) stays the literal last text for maximum recency bias;
    background, pose, variant details, and the face rule land AFTER MAIN_PROMPT
    so they override its generic defaults instead of being buried before it.
    Returns (prompt_prefix, prompt_mid, outfit_suffix); `category` picks the
    product-type rule (upper/lower/set/layered/dress) and `face_key` picks the
    face handling (preserve/beautify/beautify_expression)."""
    if outfit_mode not in VALID_OUTFIT_MODES:
        outfit_mode = OUTFIT_MODE_PRODUCT_ONLY
    if category not in VALID_CATEGORIES:
        category = CATEGORY_UPPER

    prefix = f"Virtual try-on photo of a person or teen wearing: {product_names}."

    mid_parts = []
    bg, props = _background_prompt(background_key)
    if bg:
        mid_parts.append(f"Background: {bg}.")
        chosen = _pick_props(props)
        for prop in chosen:
            mid_parts.append(f"Including {prop}.")
    pose_clause = _pose_clause(pose_key)
    if pose_clause:
        mid_parts.append(pose_clause)
    details = " ".join(d for d in (_clean_garment_details(x) for x in (garment_details or [])) if d)
    if details:
        mid_parts.append(f"Target garment details: {details}.")
    # Face rule last within `mid` so it overrides pose/REMEMBER expression wording.
    face_clause = _face_clause(face_key)
    if face_clause:
        mid_parts.append(face_clause)
    mid = " ".join(mid_parts)

    suffix = _outfit_clause(outfit_mode, category)
    return prefix, mid, suffix


def _scanned_product_dict(product: Product, barcode: str) -> dict:
    """Build the try-on card dict for a scanned variant.
    `image_path` is the scanned variant's own photo (matched by barcode), so
    scanning the red variant shows the red photo — not whichever variant sorts
    first. Falls back to the product-level image only if the variant has no
    real product photo."""
    image_path = None
    details = ""
    for v in product.variants:
        if v.barcode == barcode:
            details = v.tryon_details or ""
            if v.image_path and "/uploads/barcodes/" not in v.image_path:
                image_path = v.image_path
    if not image_path:
        image_path = product.image_path
    return {
        "id": product.id,
        "name": product.name,
        "barcode": barcode,
        "image_path": image_path,
        "details": details,
    }


# ─── Mobile web app (barcode scanner + photo upload) ───

@admin_router.get("/mobile", response_class=HTMLResponse)
async def mobile_app_page(request: Request):
    return templates.TemplateResponse(request, "mobile/index.html", {})


# ─── Admin page ───

@admin_router.get("/try-on", response_class=HTMLResponse)
async def admin_tryon_page(request: Request, msg: str = "", err: str = "", db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    saved = db.query(GeneratedImage).order_by(GeneratedImage.created_at.desc()).limit(20).all()

    return templates.TemplateResponse(request, "admin/tryon.html", {
        "kid_photo_preview": _tryon["kid_photo_preview"],
        "scanned": _tryon["scanned"],
        "last_gen_url": _tryon["last_gen_url"],
        "last_gen_engine": _tryon.get("last_gen_engine"),
        "backgrounds": TRYON_BACKGROUNDS,
        "pose_modes": TRYON_POSE_MODES,
        "face_modes": TRYON_FACE_MODES,
        "saved_count": len(saved),
        "msg": msg,
        "err": err,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


# ─── Add product to try-on (NO image generation) ───

@admin_router.post("/try-on/add-product", response_class=HTMLResponse)
async def admin_tryon_add_product(
    request: Request,
    barcode_input: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add a product to the try-on list by barcode. Does NOT generate an image."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    barcode = to_english_digits(barcode_input.strip())
    if not barcode:
        return RedirectResponse(url="/admin/try-on", status_code=303)

    # Barcodes live on variants, not products (see routers/sales.py)
    variant = db.query(ProductVariant).filter(
        ProductVariant.barcode == barcode,
        ProductVariant.is_active == True,
    ).first()
    if not variant:
        return RedirectResponse(url="/admin/try-on?err=بارکد یافت نشد.", status_code=303)
    product = variant.product
    if not any(s["id"] == product.id for s in _tryon["scanned"]):
        _tryon["scanned"].append(_scanned_product_dict(product, variant.barcode))
        return RedirectResponse(url="/admin/try-on?msg=محصول اضافه شد.", status_code=303)
    return RedirectResponse(url="/admin/try-on?msg=محصول از قبل اضافه شده است.", status_code=303)


# ─── Generate image ───

@admin_router.post("/try-on/generate", response_class=HTMLResponse)
async def admin_tryon_generate(
    request: Request,
    background: str = Form(""),
    pose: str = Form("preserve"),
    face: str = Form("preserve"),
    mode: str = Form(OUTFIT_MODE_PRODUCT_ONLY),
    category: str = Form(CATEGORY_UPPER),
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    scanned_ids = [s["id"] for s in _tryon["scanned"]]
    if not scanned_ids:
        return RedirectResponse(url="/admin/try-on?err=حداقل یک محصول انتخاب کنید.", status_code=303)

    # Paid AI — enforce the configurable daily cap before generating.
    if not tryon_can_generate(request, db):
        return RedirectResponse(
            url="/admin/try-on?err=سقف تولید روزانه تصویر رسیده است ("
                 f"{tryon_daily_remaining(db)} باقی‌مانده). تنظیمات → سقف تولید روزانه.",
            status_code=303,
        )

    # Outfit mode validation (form dropdown)
    if mode not in VALID_OUTFIT_MODES:
        mode = OUTFIT_MODE_PRODUCT_ONLY

    # Resolve scanned products by id (one query each — small list)
    products = [db.query(Product).get(s["id"]) for s in _tryon["scanned"]]
    products = [p for p in products if p]
    product_names = ", ".join(p.name for p in products)
    garment_details = [s["details"] for s in _tryon["scanned"] if s.get("details")]

    # Build reference images: kid photo first, then each scanned variant's garment photo.
    # Pass the scanned barcode so we send the right color (red variant photo for red),
    # not whichever variant image sorts first. The kid photo is materialized only
    # for this request and is deleted immediately afterward.
    reference_paths = []
    for s in _tryon["scanned"]:
        product = next((p for p in products if p.id == s["id"]), None)
        if product:
            reference_paths.extend(_product_image_paths(product, s["barcode"]))
    # Do not upload the same file twice if products share a fallback image.
    reference_paths = list(dict.fromkeys(reference_paths))

    kid_temp_path = _kid_photo_temp_path(
        content_type=_tryon["kid_photo_content_type"]
    )
    if kid_temp_path:
        reference_paths.insert(0, kid_temp_path)

    if not reference_paths:
        _remove_temp_file(kid_temp_path)
        return RedirectResponse(url="/admin/try-on?err=عکس کودک و تصویر محصول الزامی است.", status_code=303)

    prefix, prompt_mid, outfit_clause = _build_tryon_prompt(
        product_names, background, pose, mode,
        garment_details=garment_details, category=category, face_key=face,
    )

    try:
        image_bytes = await asyncio.to_thread(
            image_gen.generate,
            prompt=prefix,
            reference_image_paths=reference_paths,
            prompt_mid=prompt_mid,
            prompt_suffix=outfit_clause,
        )
    except ImageGenerationError as e:
        logger.error("Generation failed: %s", e)
        return RedirectResponse(url=f"/admin/try-on?err=تولید تصویر با خطا مواجه شد: {e}", status_code=303)
    finally:
        _remove_temp_file(kid_temp_path)

    # Save to temp (not yet saved to DB). The provider returns JPEG bytes
    # (output_format=jpeg), so store with the matching .jpg extension.
    filename = f"tryon_{uuid.uuid4().hex[:12]}.jpg"
    filepath = GENERATED_DIR / filename
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    _tryon["last_gen_url"] = f"/static/uploads/generated/{filename}"
    _tryon["last_gen_path"] = str(filepath)
    _tryon["last_gen_engine"] = "gpt-image-2"
    _tryon["last_gen_prompt"] = f"{prefix} [MAIN_PROMPT] {prompt_mid} {outfit_clause}"
    tryon_record_generation(request)

    return RedirectResponse(url="/admin/try-on?msg=تصویر با موفقیت تولید شد. برای ذخیره دائم، دکمه ذخیره را بزنید.", status_code=303)


# ─── Save generated image permanently ───

@admin_router.post("/try-on/save")
async def admin_tryon_save(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    if not _tryon["last_gen_path"] or not _tryon["last_gen_url"]:
        return RedirectResponse(url="/admin/try-on?err=تصویری برای ذخیره وجود ندارد.", status_code=303)

    barcodes = ",".join(s["barcode"] for s in _tryon["scanned"])
    record = GeneratedImage(
        image_path=_tryon["last_gen_url"],
        product_ids=barcodes,
        prompt_used=_tryon.get("last_gen_prompt") or "",
    )
    db.add(record)
    db.commit()

    _tryon["last_gen_url"] = None
    _tryon["last_gen_path"] = None
    _tryon["last_gen_prompt"] = None
    # Keep scanned products and kid photo for another round
    return RedirectResponse(url="/admin/try-on?msg=تصویر ذخیره شد. می‌توانید تصویر دیگری تولید کنید.", status_code=303)


# ─── Clear current state ───

# ─── Download latest generated image (with or without logo) ───

@admin_router.get("/try-on/download", response_class=Response)
async def admin_tryon_download(request: Request, logo: str = "no"):
    """Stream the most-recently generated image as a downloadable file.
    `logo=yes` composites the brand logo onto it via PIL (no AI call).
    `logo=no` (default) returns the raw generated image."""
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    path = _tryon.get("last_gen_path")
    if not path or not Path(path).exists():
        return RedirectResponse(url="/admin/try-on?err=تصویری برای دانلود وجود ندارد.", status_code=303)

    raw = Path(path).read_bytes()
    if logo == "yes":
        try:
            out = composite_logo(raw)
        except Exception as e:
            logger.error("Logo composite failed: %s", e)
            return RedirectResponse(url=f"/admin/try-on?err=خطا در افزودن لوگو: {e}", status_code=303)
        # Logo composite re-encodes to PNG (PIL) — that's intentionally lossless.
        media_type = "image/png"
        ext = "png"
        suffix = "with-logo"
    else:
        out = raw
        # Stored bytes are JPEG (output_format=jpeg) — label the download .jpg.
        media_type = "image/jpeg"
        ext = "jpg"
        suffix = "no-logo"

    stem = Path(path).stem
    headers = {
        "Content-Disposition": f'attachment; filename="{stem}-{suffix}.{ext}"'
    }
    return Response(content=out, media_type=media_type, headers=headers)


@admin_router.post("/try-on/clear")
async def admin_tryon_clear(request: Request):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    _clear_kid_photo()
    _tryon["scanned"] = []
    _tryon["last_gen_url"] = None
    _tryon["last_gen_path"] = None
    _tryon["last_gen_prompt"] = None
    return RedirectResponse(url="/admin/try-on?msg=حالت پاک شد.", status_code=303)


# ─── Saved images gallery ───

@admin_router.get("/try-on/saved", response_class=HTMLResponse)
async def admin_tryon_saved(request: Request, db: Session = Depends(get_db)):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    saved = db.query(GeneratedImage).order_by(GeneratedImage.created_at.desc()).all()

    # Resolve each saved image's stored barcodes back to real variant photos so
    # the gallery thumbnail row shows the scanned variant's own product photo
    # (e.g. the red variant photo), not just a bare barcode string. Uses the
    # same _variant_image_path logic as the live try-on page.
    all_barcodes = []
    for img in saved:
        for bc in (img.product_ids or "").split(","):
            bc = bc.strip()
            if bc and bc not in all_barcodes:
                all_barcodes.append(bc)
    variant_by_barcode = {}
    if all_barcodes:
        variants = db.query(ProductVariant).filter(
            ProductVariant.barcode.in_(all_barcodes),
        ).all()
        variant_by_barcode = {v.barcode: v for v in variants}

    # Attach a `variants` list to each saved image: [{name, color, image_path}]
    for img in saved:
        items = []
        for bc in (img.product_ids or "").split(","):
            bc = bc.strip()
            if not bc:
                continue
            v = variant_by_barcode.get(bc)
            if not v:
                items.append({"barcode": bc, "name": "", "color": "", "image_path": None})
                continue
            vp = _variant_image_path(v) or (v.product.image_path if v.product else None)
            items.append({
                "barcode": bc,
                "name": v.product.name if v.product else "",
                "color": v.color or "",
                "image_path": vp,
            })
        img.variants = items

    return templates.TemplateResponse(request, "admin/tryon_saved.html", {
        "saved_images": saved,
        "fmt": fmt,
        "jalali_str": jalali_str,
    })


# ─── Download a saved image (with or without logo) ───

@admin_router.get("/try-on/saved/{img_id}/download", response_class=Response)
async def admin_tryon_saved_download(
    request: Request,
    img_id: int,
    logo: str = "no",
    db: Session = Depends(get_db),
):
    if not check_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    record = db.query(GeneratedImage).filter(GeneratedImage.id == img_id).first()
    if not record:
        return RedirectResponse(url="/admin/try-on/saved?err=تصویر یافت نشد.", status_code=303)

    path = record.image_path.lstrip("/")
    if not Path(path).exists():
        return RedirectResponse(url="/admin/try-on/saved?err=فایل تصویر روی دیسک موجود نیست.", status_code=303)
    raw = Path(path).read_bytes()
    if logo == "yes":
        try:
            out = composite_logo(raw)
        except Exception as e:
            logger.error("Logo composite failed: %s", e)
            return RedirectResponse(url=f"/admin/try-on/saved?err=خطا در افزودن لوگو: {e}", status_code=303)
        # Logo composite re-encodes to PNG (PIL) — intentionally lossless.
        media_type = "image/png"
        ext = "png"
        suffix = "with-logo"
    else:
        out = raw
        # Stored bytes are JPEG — label .jpg.
        media_type = "image/jpeg"
        ext = "jpg"
        suffix = "no-logo"

    stem = Path(path).stem
    headers = {
        "Content-Disposition": f'attachment; filename="{stem}-{suffix}.{ext}"'
    }
    return Response(content=out, media_type=media_type, headers=headers)


# ─── JSON API: Scan barcode from phone (no customer) ───

@api_router.post("/try-on/scan-barcode")
async def scan_barcode(data: ScanBarcodeRequest, db: Session = Depends(get_db)):
    barcode = to_english_digits(data.barcode.strip())
    variant = db.query(ProductVariant).filter(
        ProductVariant.barcode == barcode,
        ProductVariant.is_active == True,
    ).first()
    if not variant:
        raise HTTPException(status_code=404, detail="محصول با این بارکد یافت نشد.")
    product = variant.product
    if not any(s["id"] == product.id for s in _tryon["scanned"]):
        _tryon["scanned"].append(_scanned_product_dict(product, variant.barcode))
    return {"status": "ok", "product": {"id": product.id, "name": product.name, "barcode": variant.barcode}, "scanned_count": len(_tryon["scanned"])}


# ─── JSON API: Upload kid photo from phone (no customer) ───

@api_router.post("/image-gen/upload-kid-photo")
async def upload_kid_photo(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        _set_kid_photo(raw, file.content_type or "image/jpeg")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # The image is held in RAM and is never written to static/uploads.
    return {"status": "ok", "path": None, "stored_in_static": False}


# ─── JSON API: Generate (stateless) ───

@api_router.post("/image-gen/generate")
async def api_generate(
    request: Request,
    data: GenerateRequest,
    db: Session = Depends(get_db),
):
    products = []
    details = []
    if data.barcodes:
        variants = db.query(ProductVariant).filter(
            ProductVariant.barcode.in_(data.barcodes),
            ProductVariant.is_active == True,
        ).all()
        for v in variants:
            if v.product not in products:
                products.append(v.product)
            if v.tryon_details and v.tryon_details not in details:
                details.append(v.tryon_details)
    if not products:
        raise HTTPException(status_code=400, detail="حداقل یک بارکد معتبر ارسال کنید.")

    # Paid AI — enforce the daily cap before generating.
    if not tryon_can_generate(request, db):
        raise HTTPException(status_code=429, detail="سقف تولید روزانه تصویر رسیده است.")

    names = ", ".join(p.name for p in products)
    mode = data.mode if data.mode in VALID_OUTFIT_MODES else OUTFIT_MODE_PRODUCT_ONLY
    prompt_prefix, prompt_mid, outfit_clause = _build_tryon_prompt(
        names, data.background, data.pose, mode,
        garment_details=details, category=data.category, face_key=data.face,
    )

    reference_paths = []

    # Kid photo: keep request-provided bytes in memory and materialize them only
    # for the provider call below. Nothing is written to static/uploads.
    request_kid_bytes = None
    request_kid_type = "image/png"
    if data.kid_photo_base64:
        try:
            request_kid_bytes = base64.b64decode(data.kid_photo_base64, validate=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"تصویر کودک نامعتبر است: {e}")

    # Map each scanned product to the barcode that was scanned so we send the
    # correct color variant's photo (not all variants' photos).
    barcode_by_product_id = {}
    if data.barcodes:
        variants_with_products = db.query(ProductVariant).filter(
            ProductVariant.barcode.in_(data.barcodes),
            ProductVariant.is_active == True,
        ).all()
        for v in variants_with_products:
            barcode_by_product_id.setdefault(v.product_id, v.barcode)
    for product in products:
        barcode = barcode_by_product_id.get(product.id)
        reference_paths.extend(_product_image_paths(product, barcode))
    # Do not upload the same file twice if products share a fallback image.
    reference_paths = list(dict.fromkeys(reference_paths))

    kid_temp_path = _kid_photo_temp_path(
        request_kid_bytes,
        request_kid_type if request_kid_bytes else _tryon["kid_photo_content_type"],
    )
    if kid_temp_path:
        reference_paths.insert(0, kid_temp_path)

    if not reference_paths:
        _remove_temp_file(kid_temp_path)
        raise HTTPException(status_code=400, detail="عکس کودک و تصویر محصول الزامی است.")

    try:
        img = await asyncio.to_thread(
            image_gen.generate,
            prompt=prompt_prefix,
            reference_image_paths=reference_paths,
            prompt_mid=prompt_mid,
            prompt_suffix=outfit_clause,
        )
    except ImageGenerationError as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")
    finally:
        _remove_temp_file(kid_temp_path)
    filename = f"tryon_api_{uuid.uuid4().hex[:12]}.jpg"
    filepath = GENERATED_DIR / filename
    with open(filepath, "wb") as f:
        f.write(img)
    tryon_record_generation(request)
    return {"status": "success", "image_url": f"/static/uploads/generated/{filename}"}
