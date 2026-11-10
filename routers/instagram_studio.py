"""Instagram Studio: manager/owner product photography inside the POS."""
from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from PIL import Image
from sqlalchemy import case, or_
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (BackgroundJob, InstagramGeneration, Product, ProductVariant,
                    Settings, to_english_digits, to_persian_digits)
from services._common import int_arg
from services.image_gen import composite_logo, get_generation_selection, get_tryon_selection
from services.image_models import PROVIDERS
from services.image_slots import image_slots_for
from services.image_storage import compress_for_storage
from services.instagram_backgrounds import (
    background_choices_for_scenario,
    default_background,
)
from services.instagram_product_types import (
    ACCESSORY_FAMILIES,
    FAMILY_LABELS_FA,
    RECOMMENDATION_NOTES_FA,
    product_family,
    recommended_scene,
    scene_keys_for_family,
)
from services.instagram_prompts import (
    ASPECTS,
    COMPOSITIONS,
    FRAMINGS,
    LIGHTINGS,
    MODEL_OUTFIT_LABELS_FA,
    POSES,
    POSE_REFERENCE_VIEWS,
    ROOM_BACKGROUND_SCENES,
    SCENARIO_GARMENT,
    SCENARIO_MODEL_SCENE,
    SCENARIO_PRODUCT_BRIEF,
    SCENARIO_TEXT,
    SCENARIO_TEXT_IMAGE,
    SCENARIOS,
    SCENES,
    SCENE_KEYS,
    SCENE_REFERENCE_VIEWS,
    aspect_label,
    scene_label,
)
from services.instagram_studio import (
    InstagramValidationError,
    available_model_slots,
    create_generation,
    join_prompt_sections,
    preview_prompt_sections,
    resolve_outfit_family,
    variation_payload,
    variant_has_reference_photo,
    variant_reference_images,
)
from services.security import (
    instagram_can_generate,
    instagram_daily_limit,
    instagram_daily_remaining,
    log_action,
    require_html_role,
    require_role,
)
from services.templating import templates

router = APIRouter(prefix="/admin")


def _store_logo(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == "owner_logo_path").first()
    return (row.value or "").strip() if row else ""


def _model_hint(db: Session, scenario: str = "") -> tuple[str, str, str]:
    if scenario == SCENARIO_TEXT:
        provider, model, _key = get_generation_selection(db)
        models = PROVIDERS[provider]["generation_models"]
    else:
        provider, model, _key = get_tryon_selection(db)
        models = PROVIDERS[provider]["models"]
    entry = next((item for item in models if item["id"] == model), None)
    return provider, model, (entry or {}).get("cost", "—")


def _studio_url(*, product_id=None, variant_id=None, job_id=None,
                scenario="", msg="", err="") -> str:
    params = {}
    if product_id:
        params["product_id"] = product_id
    if variant_id:
        params["variant_id"] = variant_id
    if job_id:
        params["job_id"] = job_id
    if scenario and scenario != SCENARIO_GARMENT:
        params["scenario"] = scenario
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    return "/admin/instagram-studio" + (f"?{urlencode(params)}" if params else "")


def _load_product(db: Session, product_id: int) -> Product | None:
    product = db.get(Product, product_id) if product_id else None
    if product is None or product.is_active is False:
        return None
    return product


def _load_variant(db: Session, product: Product, variant_id: int) -> ProductVariant | None:
    variant = db.get(ProductVariant, variant_id) if variant_id else None
    if variant is None or variant.product_id != product.id or variant.is_active is False:
        return None
    return variant


def _instagram_usage(db: Session) -> dict:
    """Billable Instagram jobs in the last day, week, and month."""
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def count(start) -> int:
        return db.query(BackgroundJob).filter(
            BackgroundJob.job_type == "instagram_image",
            BackgroundJob.created_at >= start,
            BackgroundJob.status.in_(("pending", "processing", "completed")),
        ).count()

    return {
        "today": count(day),
        "week": count(day - timedelta(days=6)),
        "month": count(day - timedelta(days=29)),
    }


def _job_and_generation(db: Session, job_id: int):
    job = db.query(BackgroundJob).filter(
        BackgroundJob.id == job_id,
        BackgroundJob.job_type == "instagram_image",
    ).first()
    if job is None:
        return None, None
    generation = db.query(InstagramGeneration).filter(
        InstagramGeneration.job_id == job.id).first()
    return job, generation


def _result_context(db: Session, job: BackgroundJob, generation: InstagramGeneration | None,
                    is_owner: bool) -> dict:
    payload = json_payload(job)
    scenario = generation.scenario if generation else SCENARIO_GARMENT
    return {
        "job": job,
        "generation": generation,
        "has_logo": bool(_store_logo(db)),
        "logo_requested": bool(payload.get("logo")),
        "is_owner": is_owner,
        "variation": (variation_payload(generation, payload)
                      if generation and job.status == "completed"
                      and scenario != SCENARIO_TEXT_IMAGE else None),
        "retry_url": _studio_url(
            product_id=generation.product_id if generation else 0,
            variant_id=generation.variant_id if generation else 0,
            scenario=scenario),
    }


def _active_variants(product: Product) -> list[ProductVariant]:
    return sorted(
        [item for item in product.variants if item.is_active is not False],
        key=lambda item: ((item.color or ""), (item.size or ""), item.id),
    )


UPLOAD_MAX_BYTES = 15 * 1024 * 1024
UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


async def _save_uploaded_reference(upload) -> str:
    """Validate one uploaded campaign photo and keep it for the queued job."""
    if upload is None or not getattr(upload, "filename", ""):
        raise InstagramValidationError("یک عکس مرجع انتخاب کنید.")
    raw = await upload.read(UPLOAD_MAX_BYTES + 1)
    if not raw:
        raise InstagramValidationError("فایل انتخاب‌شده خالی است.")
    if len(raw) > UPLOAD_MAX_BYTES:
        raise InstagramValidationError("حجم عکس بیشتر از ۱۵ مگابایت است.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
    except Exception as error:
        raise InstagramValidationError("فایل انتخاب‌شده عکس معتبر نیست.") from error
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        suffix = ".jpg"
    data, suffix = compress_for_storage(raw, suffix)
    with tempfile.NamedTemporaryFile(prefix="ig_source_", suffix=suffix,
                                     delete=False) as handle:
        handle.write(data)
        return handle.name


def _variant_card_context(db: Session, scenario: str, product: Product,
                          variant: ProductVariant | None, guard) -> dict:
    """Everything the Step 2 card needs, shared by the page and the swap endpoint."""
    backgrounds = background_choices_for_scenario(db, scenario)
    default = default_background(db) if backgrounds else None
    family = product_family(product)
    allowed_scenes = scene_keys_for_family(family)
    scenes = [{"key": key, "label": scene_label(key),
               "views": SCENE_REFERENCE_VIEWS.get(key, ("main",))}
              for key in SCENE_KEYS
              if allowed_scenes is None or key in allowed_scenes]
    recommended = recommended_scene(family)
    first_scene = scenes[0]["key"] if scenes else ""
    if scenario == SCENARIO_PRODUCT_BRIEF:
        background_field_visible = True
    elif scenario == SCENARIO_GARMENT:
        background_field_visible = first_scene in ROOM_BACKGROUND_SCENES
    else:
        background_field_visible = False
    return {
        "product": product,
        "variant": variant,
        "reference_images": variant_reference_images(variant) if variant else [],
        "model_slots": (available_model_slots(db)
                        if scenario == SCENARIO_MODEL_SCENE else []),
        "scenes": scenes,
        "recommended_scene": recommended or "",
        "recommended_scene_label": scene_label(recommended) if recommended else "",
        "recommended_url": _studio_url(
            product_id=product.id,
            variant_id=variant.id if variant else 0,
            scenario=SCENARIO_GARMENT),
        "garment_family_label": FAMILY_LABELS_FA.get(family, ""),
        "recommendation_note": RECOMMENDATION_NOTES_FA.get(family, ""),
        "outfit_choices": [{"key": key, "label": label}
                           for key, label in MODEL_OUTFIT_LABELS_FA.items()],
        "detected_outfit": family or "",
        "detected_outfit_label": FAMILY_LABELS_FA.get(family, ""),
        "outfit_override_visible": (
            scenario == SCENARIO_MODEL_SCENE
            and family not in ACCESSORY_FAMILIES
        ),
        "poses": [{"key": key, "label": value["label"],
                   "views": POSE_REFERENCE_VIEWS.get(key, ("main",))}
                  for key, value in POSES.items()],
        "supported_views": [slot["view"] for slot in image_slots_for(product)],
        "backgrounds": backgrounds,
        "default_background_key": default.key if default else "",
        "default_background_source": "brand" if default else "",
        "room_background_scenes": list(ROOM_BACKGROUND_SCENES),
        "background_field_visible": background_field_visible,
        "framings": [{"key": key, "label": value["label"]}
                     for key, value in FRAMINGS.items()],
        "lightings": [{"key": key, "label": value["label"]}
                      for key, value in LIGHTINGS.items()],
        "compositions": [{"key": key, "label": value["label"]}
                         for key, value in COMPOSITIONS.items()],
        "aspects": [{"key": key, "label": aspect_label(key)} for key in ASPECTS],
        "scenario": scenario,
        "scenario_label": SCENARIOS.get(scenario, scenario),
        "has_logo": bool(_store_logo(db)),
        "is_owner": guard.role == "owner",
    }


@router.get("/instagram-studio/prompt-preview", response_class=HTMLResponse)
async def instagram_prompt_preview(request: Request, db: Session = Depends(get_db)):
    """The exact prompt for the form's current choices, section by section."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    params = request.query_params
    scenario = (params.get("scenario") or SCENARIO_GARMENT).strip()
    if scenario not in SCENARIOS:
        scenario = SCENARIO_GARMENT
    product = variant = None
    if scenario not in (SCENARIO_TEXT, SCENARIO_TEXT_IMAGE):
        product = _load_product(db, int_arg(params.get("product_id")))
        if product is not None:
            variant = _load_variant(db, product, int_arg(params.get("variant_id")))

    sections: list[dict] = []
    prompt = ""
    error = ""
    try:
        sections = preview_prompt_sections(
            db,
            scenario=scenario,
            product=product,
            variant=variant,
            selected_paths=params.getlist("image_paths"),
            scene_key=(params.get("garment_mode") or "").strip(),
            aspect_ratio=(params.get("aspect_ratio") or "4:5").strip(),
            user_text=params.get("user_text") or "",
            model_slot=(params.get("model_slot") or "").strip(),
            pose_key=(params.get("pose_key") or "").strip(),
            background_key=(params.get("background_key") or "").strip(),
            framing_key=(params.get("framing_key") or "").strip(),
            lighting_key=(params.get("lighting_key") or "").strip(),
            composition_key=(params.get("composition_key") or "").strip(),
            show_full_garment=bool(params.get("show_full_garment")),
            outfit_category=(params.get("outfit_category") or "").strip(),
        )
        prompt = join_prompt_sections(sections)
    except InstagramValidationError as failure:
        error = str(failure)
    except (KeyError, ValueError):
        error = "گزینه‌های انتخاب‌شده برای پیش‌نمایش کامل نیست."

    return templates.TemplateResponse(
        request, "partials/instagram_prompt_preview.html",
        {"sections": sections, "prompt": prompt, "error": error})


@router.get("/instagram-studio/variants")
async def instagram_variant_search(request: Request, q: str = "",
                                   db: Session = Depends(get_db)):
    """Live variant suggestions: product name or barcode, top five usable ones."""
    require_role(request, db, "manager")
    needle = to_english_digits((q or "").strip())[:50]
    if len(needle) < 2:
        return JSONResponse({"items": []})

    like = f"%{needle}%"
    rank = case(
        (ProductVariant.barcode == needle, 0),
        (ProductVariant.barcode.like(f"{needle}%"), 1),
        (Product.name.like(f"{needle}%"), 2),
        else_=3,
    )
    candidates = (
        db.query(ProductVariant)
        .join(Product)
        .options(joinedload(ProductVariant.images))
        .filter(
            ProductVariant.is_active.is_(True),
            Product.is_active.is_not(False),
            or_(Product.name.like(like), ProductVariant.barcode.like(like)),
        )
        .order_by(rank, Product.name, ProductVariant.id)
        .limit(25)
        .all()
    )
    items = []
    for variant in candidates:
        if not variant_has_reference_photo(variant):
            continue
        items.append({
            "variant_id": variant.id,
            "product_id": variant.product_id,
            "label": variant.display_name,
            "barcode": variant.barcode,
            "stock": variant.available_quantity,
            "stock_display": to_persian_digits(str(variant.available_quantity)),
            "exact": variant.barcode == needle,
        })
        if len(items) == 5:
            break
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "partials/instagram_variant_results.html", {"items": items})
    return JSONResponse({"items": items})


@router.get("/instagram-studio/variant-card", response_class=HTMLResponse)
async def instagram_variant_card(request: Request, scenario: str = "",
                                 product_id: str = "", variant_id: str = "",
                                 db: Session = Depends(get_db)):
    """One Step 2 card, for the typeahead to swap in without a reload."""
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    scenario = (scenario or SCENARIO_GARMENT).strip()
    if scenario not in SCENARIOS:
        scenario = SCENARIO_GARMENT
    product = _load_product(db, int_arg(product_id))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    requested_variant = int_arg(variant_id)
    variant = _load_variant(db, product, requested_variant) if requested_variant else None
    if requested_variant and variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    if variant is None:
        variants = _active_variants(product)
        variant = variants[0] if variants else None

    return templates.TemplateResponse(
        request, "partials/instagram_variant_card.html",
        _variant_card_context(db, scenario, product, variant, guard))


@router.get("/instagram-studio", response_class=HTMLResponse)
async def instagram_studio(
    request: Request,
    product_id: str = "",
    variant_id: str = "",
    job_id: str = "",
    scenario: str = "",
    msg: str = "",
    err: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    scenario = (scenario or SCENARIO_GARMENT).strip()
    if scenario not in SCENARIOS:
        scenario = SCENARIO_GARMENT
    product = None
    variant = None
    if scenario not in (SCENARIO_TEXT, SCENARIO_TEXT_IMAGE):
        product = _load_product(db, int_arg(product_id))
        if product is not None:
            variant = _load_variant(db, product, int_arg(variant_id))
            if variant is None:
                variants = _active_variants(product)
                variant = variants[0] if variants else None

    job = generation = None
    requested_job = int_arg(job_id)
    if requested_job:
        job, generation = _job_and_generation(db, requested_job)

    provider, model, model_cost = _model_hint(db, scenario)
    context = {
        "aspects": [{"key": key, "label": aspect_label(key)} for key in ASPECTS],
        "scenario_tabs": [{"key": key, "label": label}
                          for key, label in SCENARIOS.items()],
        "daily_remaining": instagram_daily_remaining(db),
        "daily_limit": instagram_daily_limit(db),
        "usage": _instagram_usage(db),
        "provider": provider,
        "model": model,
        "model_cost": model_cost,
        "has_logo": bool(_store_logo(db)),
        "is_owner": guard.role == "owner",
        "scenario": scenario,
        "selected_label": variant.display_name if variant else "",
        "retry_url": _studio_url(
            product_id=product.id if product else 0,
            variant_id=variant.id if variant else 0,
            scenario=scenario),
        "job": job,
        "generation": generation,
        "msg": msg,
        "err": err,
    }
    if product is not None:
        context.update(_variant_card_context(db, scenario, product, variant, guard))
    if job is not None:
        # A page reload keeps the completed/failed result actions (variation and
        # retry) instead of dropping them.
        context.update(_result_context(db, job, generation, guard.role == "owner"))
    return templates.TemplateResponse(request, "admin/instagram_studio.html", context)


@router.post("/instagram-studio/generate")
async def instagram_generate(request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    form = await request.form()
    scenario = str(form.get("scenario") or SCENARIO_GARMENT).strip()
    if scenario not in SCENARIOS:
        return RedirectResponse(
            _studio_url(err="حالت تولید انتخاب‌شده معتبر نیست."),
            status_code=303)

    product = None
    variant = None
    product_id = 0
    variant_id = 0
    if scenario not in (SCENARIO_TEXT, SCENARIO_TEXT_IMAGE):
        product_id = int_arg(form.get("product_id"))
        variant_id = int_arg(form.get("variant_id"))
        product = _load_product(db, product_id)
        if product is None:
            return RedirectResponse(_studio_url(err="محصول انتخاب‌شده پیدا نشد."), status_code=303)
        variant = _load_variant(db, product, variant_id)
        if variant is None:
            return RedirectResponse(
                _studio_url(product_id=product.id, err="تنوع/رنگ انتخاب‌شده معتبر نیست."),
                status_code=303)
        product_id = product.id
        variant_id = variant.id

    scene_key = str(form.get("garment_mode") or "").strip()
    aspect_ratio = str(form.get("aspect_ratio") or "4:5").strip()
    note = str(form.get("user_text") or "").strip()[:1000]
    model_slot = str(form.get("model_slot") or "").strip()
    pose_key = str(form.get("pose_key") or "").strip()
    background_key = str(form.get("background_key") or "").strip()
    framing_key = str(form.get("framing_key") or "").strip()
    lighting_key = str(form.get("lighting_key") or "").strip()
    composition_key = str(form.get("composition_key") or "").strip()
    outfit_category = str(form.get("outfit_category") or "").strip()
    show_full_garment = bool(form.get("show_full_garment"))
    if scenario == SCENARIO_MODEL_SCENE:
        pose_key = pose_key or "standing_front"
        background_key = background_key or "studio_white"
        framing_key = framing_key or "full_body"
        lighting_key = lighting_key or "soft_daylight"
        composition_key = composition_key or "centered_catalog"
    if not instagram_can_generate(db):
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id, scenario=scenario,
                        err="سقف تولید روزانه اینستاگرام رسیده است. فردا دوباره تلاش کنید."),
            status_code=303)

    recent = datetime.now(timezone.utc) - timedelta(minutes=2)
    duplicate = (
        db.query(InstagramGeneration)
        .join(BackgroundJob, InstagramGeneration.job_id == BackgroundJob.id)
        .filter(
            InstagramGeneration.created_by_staff_id == guard.id,
            InstagramGeneration.scenario == scenario,
            BackgroundJob.status.in_(("pending", "processing")),
            InstagramGeneration.created_at >= recent,
        )
    )
    if scenario in (SCENARIO_TEXT, SCENARIO_TEXT_IMAGE):
        duplicate = duplicate.filter(InstagramGeneration.user_text == note)
    else:
        duplicate = duplicate.filter(InstagramGeneration.variant_id == variant.id)
        if scene_key:
            duplicate = duplicate.filter(InstagramGeneration.garment_mode == scene_key)
        else:
            duplicate = duplicate.filter(InstagramGeneration.garment_mode.is_(None))
        if scenario == SCENARIO_MODEL_SCENE:
            duplicate = duplicate.filter(
                InstagramGeneration.model_slot == model_slot,
                InstagramGeneration.pose_key == pose_key,
                InstagramGeneration.background_key == background_key,
                InstagramGeneration.outfit_category == resolve_outfit_family(
                    product, outfit_category),
            )
        else:
            duplicate = duplicate.filter(
                InstagramGeneration.background_key == (background_key or None))
    if duplicate.first() is not None:
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id, scenario=scenario,
                        err="این تصویر همین حالا در صف است؛ چند لحظه صبر کنید."),
            status_code=303)

    _provider, _model, api_key = (
        get_generation_selection(db) if scenario == SCENARIO_TEXT
        else get_tryon_selection(db))
    if not api_key:
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id, scenario=scenario,
                        err="کلید ارائه‌دهنده تصویر ذخیره نشده است — تنظیمات → پیامک و هوش مصنوعی."),
            status_code=303)

    logo_path = _store_logo(db)
    logo = bool(form.get("logo")) and bool(logo_path)
    selected_paths = [str(path) for path in form.getlist("image_paths")]
    source_image_path = ""
    if scenario == SCENARIO_TEXT_IMAGE:
        try:
            source_image_path = await _save_uploaded_reference(
                form.get("reference_image"))
        except InstagramValidationError as error:
            return RedirectResponse(
                _studio_url(scenario=scenario, err=str(error)),
                status_code=303)

    try:
        generation, job = create_generation(
            db,
            staff=guard,
            product=product,
            variant=variant,
            selected_paths=selected_paths,
            scene_key=scene_key,
            aspect_ratio=aspect_ratio,
            user_text=str(form.get("user_text") or ""),
            logo=logo,
            logo_path=logo_path,
            scenario=scenario,
            source_image_path=source_image_path,
            model_slot=model_slot,
            pose_key=pose_key,
            background_key=background_key,
            framing_key=framing_key,
            lighting_key=lighting_key,
            composition_key=composition_key,
            show_full_garment=show_full_garment,
            outfit_category=outfit_category,
        )
    except InstagramValidationError as error:
        if source_image_path:
            Path(source_image_path).unlink(missing_ok=True)
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id,
                        scenario=scenario, err=str(error)),
            status_code=303)

    if scenario in (SCENARIO_TEXT, SCENARIO_TEXT_IMAGE):
        detail = f"کمپین متنی / {note[:60]}"
    else:
        detail = f"{product.name} / {variant.display_name} / {scenario} / {scene_key or '—'}"
    log_action(db, "instagram_generate", detail,
               request=request, target_type="instagram_generation", target_id=generation.id)
    return RedirectResponse(
        _studio_url(product_id=product_id, variant_id=variant_id, job_id=job.id,
                    scenario=scenario, msg="درخواست تولید در صف قرار گرفت."),
        status_code=303)


@router.get("/instagram-studio/jobs/{job_id}")
async def instagram_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    job, generation = _job_and_generation(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "partials/instagram_live.html",
            _result_context(db, job, generation, guard.role == "owner"))
    return JSONResponse({
        "job_id": job.id,
        "status": job.status,
        "image_url": job.result_url if job.status == "completed" else None,
        "error": job.error_message if job.status == "failed" else None,
    })


def json_payload(job: BackgroundJob) -> dict:
    import json
    try:
        return json.loads(job.payload or "{}")
    except Exception:
        return {}


@router.get("/instagram-studio/generations/{generation_id}/download")
async def instagram_download(generation_id: int, request: Request,
                             db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    generation = db.query(InstagramGeneration).options(
        joinedload(InstagramGeneration.job)).filter(
        InstagramGeneration.id == generation_id).first()
    if generation is None or generation.job is None or not generation.job.result_path:
        raise HTTPException(status_code=404, detail="Image not found")

    path = Path(generation.job.result_path)
    try:
        path.resolve().relative_to(Path("static/uploads/instagram").resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Image not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    if request.query_params.get("logo") == "yes":
        logo_path = _store_logo(db)
        if logo_path:
            raw = path.read_bytes()
            badged = composite_logo(raw, logo_path)
            return Response(
                content=badged, media_type="image/png",
                headers={"Content-Disposition":
                         f'attachment; filename="instagram-{generation.id}-logo.png"'})
    return FileResponse(path, media_type="image/jpeg",
                        filename=f"instagram-{generation.id}.jpg")
