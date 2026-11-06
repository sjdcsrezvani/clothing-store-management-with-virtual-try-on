"""Validation and job creation for Instagram Studio.

The router stays thin: this module decides which reference images are legal,
builds the prompt, reserves the output paths, and enqueues exactly one job.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from models import InstagramGeneration, Product, ProductVariant, ShopModel, to_persian_digits
from services.image_gen import get_tryon_selection
from services.instagram_prompts import (
    ASPECTS,
    BACKGROUNDS,
    COMPOSITIONS,
    FRAMINGS,
    IG_PROMPT_VERSION,
    LIGHTINGS,
    POSES,
    SCENARIO_GARMENT,
    SCENARIO_MODEL_SCENE,
    SCENARIO_PRODUCT_BRIEF,
    SCENARIO_TEXT,
    SCENARIOS,
    SCENES,
    build_garment_prompt,
    build_model_prompt,
    build_product_brief_prompt,
    build_text_prompt,
)
from services.jobs import enqueue
from services.shop_models import model_age, model_gender

MAX_REFERENCES = 3
OUTPUT_DIR = Path("static/uploads/instagram")
THUMB_DIR = OUTPUT_DIR / "thumbs"
OUTPUT_URL = "/static/uploads/instagram"


class InstagramValidationError(Exception):
    """A user-correctable refusal; the route redirects with the Persian text."""


def _disk_path(url: str) -> str:
    """DB image URLs are /static/...; absolute paths are allowed for tests."""
    path = Path(url)
    if path.is_absolute() and path.exists():
        return str(path)
    return url.lstrip("/")


def variant_reference_images(variant: ProductVariant) -> list[dict]:
    """Every usable photo of this exact variant, primary first.

    The gallery is authoritative; the legacy single image is the fallback. A
    barcode label is not a garment photo and is skipped.
    """
    frames = sorted(variant.images or [], key=lambda frame: (frame.sort_order, frame.id))
    candidates = [frame.image_path for frame in frames]
    if variant.image_path:
        candidates.append(variant.image_path)

    images: list[dict] = []
    seen: set[str] = set()
    for path in candidates:
        if not path or "/uploads/barcodes/" in path or path in seen:
            continue
        seen.add(path)
        images.append({"path": path, "sort_order": len(images)})
    for index, image in enumerate(images):
        image["label"] = "تصویر اصلی" if index == 0 else f"تصویر {to_persian_digits(str(index + 1))}"
    return images


def collect_reference_paths(variant: ProductVariant, selected_paths: list[str]) -> list[str]:
    """Validate the posted URLs against this variant's gallery and return disk paths."""
    allowed = [image["path"] for image in variant_reference_images(variant)]
    if not allowed:
        raise InstagramValidationError(
            "برای این تنوع عکس محصول ثبت نشده است — ابتدا تصویر تنوع را در محصولات ثبت کنید."
        )

    chosen = [path for path in selected_paths if path]
    if not chosen:
        raise InstagramValidationError("حداقل یک عکس مرجع محصول انتخاب کنید.")
    if len(chosen) > MAX_REFERENCES:
        raise InstagramValidationError(
            f"حداکثر {to_persian_digits(str(MAX_REFERENCES))} عکس مرجع برای هر تولید مجاز است."
        )

    ordered: list[str] = []
    for path in chosen:
        if path not in allowed:
            raise InstagramValidationError("عکس انتخاب‌شده متعلق به همین تنوع محصول نیست.")
        if path not in ordered:
            ordered.append(path)

    disk_paths = []
    for url in ordered:
        disk = _disk_path(url)
        if not Path(disk).exists():
            raise InstagramValidationError("فایل عکس مرجع پیدا نشد — دوباره از محصولات آپلود کنید.")
        disk_paths.append(disk)
    return disk_paths


def create_generation(
    db,
    *,
    staff,
    product: Product | None,
    variant: ProductVariant | None,
    selected_paths: list[str],
    scene_key: str,
    aspect_ratio: str,
    user_text: str = "",
    logo: bool = False,
    logo_path: str = "",
    scenario: str = SCENARIO_GARMENT,
    model_slot: str = "",
    pose_key: str = "",
    background_key: str = "",
    framing_key: str = "",
    lighting_key: str = "",
    composition_key: str = "",
    show_full_garment: bool = True,
) -> tuple[InstagramGeneration, object]:
    """Build the prompt, create the record, and enqueue one paid job."""
    if scenario not in SCENARIOS:
        raise InstagramValidationError("حالت تولید انتخاب‌شده معتبر نیست.")
    if aspect_ratio not in ASPECTS:
        raise InstagramValidationError("نسبت تصویر انتخاب‌شده معتبر نیست.")

    note = (user_text or "").strip()[:1000]
    model = None
    model_disk_path = ""
    if scenario == SCENARIO_TEXT:
        if len(note) < 10:
            raise InstagramValidationError("متن کمپین باید حداقل ۱۰ نویسه باشد.")
    elif scenario == SCENARIO_PRODUCT_BRIEF:
        if len(note) < 10:
            raise InstagramValidationError("توضیح کمپین باید حداقل ۱۰ نویسه باشد.")
        if scene_key and scene_key not in SCENES:
            raise InstagramValidationError("حالت عکاسی انتخاب‌شده معتبر نیست.")
    elif scenario == SCENARIO_MODEL_SCENE:
        if not model_slot:
            raise InstagramValidationError("مدل عکاسی را انتخاب کنید.")
        model = (db.query(ShopModel)
                 .filter(ShopModel.slot_key == model_slot,
                         ShopModel.is_active.is_(True))
                 .first())
        if model is None:
            raise InstagramValidationError("مدل انتخاب‌شده فعال نیست.")
        if not model.image_path:
            raise InstagramValidationError("برای این مدل عکس مرجع ثبت نشده است — ابتدا در تنظیمات آپلود کنید.")
        model_disk_path = _disk_path(model.image_path)
        if not Path(model_disk_path).exists():
            raise InstagramValidationError("عکس مرجع مدل پیدا نشد — دوباره در تنظیمات آپلود کنید.")
        if model_age(model_slot) is None:
            raise InstagramValidationError("سن مدل انتخاب‌شده معتبر نیست.")
        pose_key = pose_key or "standing_front"
        background_key = background_key or "studio_white"
        framing_key = framing_key or "full_body"
        lighting_key = lighting_key or "soft_daylight"
        composition_key = composition_key or "centered_catalog"
        if pose_key not in POSES:
            raise InstagramValidationError("ژست انتخاب‌شده معتبر نیست.")
        if background_key not in BACKGROUNDS:
            raise InstagramValidationError("پس‌زمینه انتخاب‌شده معتبر نیست.")
        if framing_key not in FRAMINGS:
            raise InstagramValidationError("کادر انتخاب‌شده معتبر نیست.")
        if lighting_key not in LIGHTINGS:
            raise InstagramValidationError("نور انتخاب‌شده معتبر نیست.")
        if composition_key not in COMPOSITIONS:
            raise InstagramValidationError("ترکیب‌بندی انتخاب‌شده معتبر نیست.")
    elif scene_key not in SCENES:
        raise InstagramValidationError("حالت عکاسی انتخاب‌شده معتبر نیست.")

    if scenario != SCENARIO_TEXT and (product is None or variant is None):
        raise InstagramValidationError("محصول و تنوع انتخاب‌شده معتبر نیست.")

    if scenario == SCENARIO_TEXT:
        garment_paths: list[str] = []
        reference_paths = []
        details = ""
    else:
        garment_paths = collect_reference_paths(variant, selected_paths)
        reference_paths = ([model_disk_path] + garment_paths
                           if scenario == SCENARIO_MODEL_SCENE else garment_paths)
        details = (variant.tryon_details or "").strip()
    provider_name, model_name, _api_key = get_tryon_selection(db)

    if scenario == SCENARIO_TEXT:
        prompt = build_text_prompt(user_text=note, aspect_ratio=aspect_ratio)
    elif scenario == SCENARIO_PRODUCT_BRIEF:
        prompt = build_product_brief_prompt(
            product_name=product.name,
            variant_color=variant.color or "",
            scene_key=scene_key,
            aspect_ratio=aspect_ratio,
            reference_count=len(reference_paths),
            user_brief=note,
            garment_details=details,
        )
    elif scenario == SCENARIO_MODEL_SCENE:
        prompt = build_model_prompt(
            product_name=product.name,
            variant_color=variant.color or "",
            model_age=model_age(model_slot),
            model_gender=model_gender(model_slot),
            pose_key=pose_key,
            background_key=background_key,
            framing_key=framing_key,
            lighting_key=lighting_key,
            composition_key=composition_key,
            aspect_ratio=aspect_ratio,
            garment_reference_count=len(garment_paths),
            user_note=note,
            garment_details=details,
            show_full_garment=show_full_garment,
        )
    else:
        prompt = build_garment_prompt(
            product_name=product.name,
            variant_color=variant.color or "",
            scene_key=scene_key,
            aspect_ratio=aspect_ratio,
            reference_count=len(reference_paths),
            user_note=note,
            garment_details=details,
        )

    filename = f"ig_{uuid.uuid4().hex[:12]}.jpg"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename
    thumbnail_path = THUMB_DIR / filename

    generation = InstagramGeneration(
        created_by_staff_id=staff.id,
        scenario=scenario,
        product_id=product.id if product else None,
        variant_id=variant.id if variant else None,
        garment_mode=scene_key or None,
        model_slot=model_slot or None,
        pose_key=pose_key or None,
        background_key=background_key or None,
        aspect_ratio=aspect_ratio,
        user_text=note or None,
        rendered_prompt=prompt,
        prompt_version=IG_PROMPT_VERSION,
        provider=provider_name,
        model=model_name,
    )
    db.add(generation)
    db.flush()

    job = enqueue(db, "instagram_image", {
        "generation_id": generation.id,
        "prompt": prompt,
        "reference_paths": reference_paths,
        "aspect_ratio": aspect_ratio,
        "output_path": str(output_path),
        "thumbnail_path": str(thumbnail_path),
        "result_url": f"{OUTPUT_URL}/{filename}",
        "thumbnail_url": f"{OUTPUT_URL}/thumbs/{filename}",
        "logo": bool(logo),
        "logo_path": logo_path,
        "framing_key": framing_key,
        "lighting_key": lighting_key,
        "composition_key": composition_key,
        "show_full_garment": bool(show_full_garment),
    })
    generation.job_id = job.id
    db.commit()
    db.refresh(generation)
    db.refresh(job)
    return generation, job


def _reference_url(path: str) -> str:
    """Turn a stored disk path back into the gallery URL the form posts."""
    text = str(path or "")
    if text.startswith("/static/"):
        return text
    if Path(text).is_absolute():
        return ""
    return "/" + text.lstrip("/")


def variation_payload(generation: InstagramGeneration, payload: dict) -> dict:
    """The original inputs needed to enqueue the same request again."""
    payload = payload or {}
    scenario = generation.scenario or SCENARIO_GARMENT
    references = [str(path) for path in payload.get("reference_paths") or []]
    if scenario == SCENARIO_MODEL_SCENE:
        references = references[1:]  # Image 1 is the model, not a garment photo
    return {
        "scenario": scenario,
        "product_id": generation.product_id,
        "variant_id": generation.variant_id,
        "image_paths": [url for url in (_reference_url(path) for path in references) if url],
        "garment_mode": generation.garment_mode or "",
        "aspect_ratio": generation.aspect_ratio or "4:5",
        "user_text": generation.user_text or "",
        "logo": bool(payload.get("logo")),
        "model_slot": generation.model_slot or "",
        "pose_key": generation.pose_key or "",
        "background_key": generation.background_key or "",
        "framing_key": payload.get("framing_key") or "full_body",
        "lighting_key": payload.get("lighting_key") or "soft_daylight",
        "composition_key": payload.get("composition_key") or "centered_catalog",
        "show_full_garment": bool(payload.get("show_full_garment", True)),
    }


def available_model_slots(db) -> list[dict]:
    """Active model slots whose reference file still exists on disk."""
    from services.shop_models import ensure_shop_models

    choices = []
    for row in ensure_shop_models(db):
        if not row.is_active or not row.image_path:
            continue
        if not Path(_disk_path(row.image_path)).exists():
            continue
        choices.append({
            "slot_key": row.slot_key,
            "label_fa": row.label_fa,
            "image_url": row.image_path,
        })
    return choices


def thumbnail_path_for(result_path: str) -> str:
    path = Path(result_path)
    return str(path.parent / "thumbs" / path.name)


def delete_generation_files(generation: InstagramGeneration, thumb_path: str = "") -> None:
    """Remove the generated image and its thumbnail; missing files are fine."""
    job = generation.job
    if job and job.result_path:
        Path(job.result_path).unlink(missing_ok=True)
        thumb_path = thumb_path or thumbnail_path_for(job.result_path)
    if thumb_path:
        Path(thumb_path).unlink(missing_ok=True)
