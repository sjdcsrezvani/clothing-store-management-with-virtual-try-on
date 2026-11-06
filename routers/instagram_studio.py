"""Instagram Studio: manager/owner product photography inside the POS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import BackgroundJob, InstagramGeneration, Product, ProductVariant, Settings
from services._common import int_arg, jalali_str, page_arg
from services.image_gen import composite_logo, get_tryon_selection
from services.image_models import PROVIDERS
from services.instagram_prompts import (
    ASPECTS,
    BACKGROUNDS,
    COMPOSITIONS,
    FRAMINGS,
    LIGHTINGS,
    POSES,
    SCENARIO_GARMENT,
    SCENARIO_MODEL_SCENE,
    SCENARIO_PRODUCT_BRIEF,
    SCENARIO_TEXT,
    SCENARIOS,
    SCENES,
    SCENE_KEYS,
    aspect_label,
    scene_label,
)
from services.instagram_studio import (
    InstagramValidationError,
    available_model_slots,
    create_generation,
    delete_generation_files,
    variation_payload,
    variant_reference_images,
)
from services.shop_models import SLOT_LABELS
from services.security import (
    instagram_can_generate,
    instagram_daily_limit,
    instagram_daily_remaining,
    log_action,
    require_html_role,
)
from services.templating import templates

router = APIRouter(prefix="/admin")

PAGE_SIZE = 24


def _store_logo(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == "owner_logo_path").first()
    return (row.value or "").strip() if row else ""


def _model_hint(db: Session) -> tuple[str, str, str]:
    provider, model, _key = get_tryon_selection(db)
    entry = next((item for item in PROVIDERS[provider]["models"] if item["id"] == model), None)
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


def _history_url(*, page=1, scenario="", product_id=0, favorite="", msg="", err="") -> str:
    params = {"page": page}
    if scenario:
        params["scenario"] = scenario
    if product_id:
        params["product_id"] = product_id
    if favorite:
        params["favorite"] = favorite
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    return "/admin/instagram-studio/history?" + urlencode(params)


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


def _safe_back(value: str) -> str:
    """Only the studio may be named as a return address."""
    value = (value or "").strip()
    if value.startswith("/admin/instagram-studio"):
        return value
    return "/admin/instagram-studio/history"


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
                      if generation and job.status == "completed" else None),
        "favorite_back": _studio_url(
            product_id=generation.product_id if generation else 0,
            variant_id=generation.variant_id if generation else 0,
            job_id=job.id, scenario=scenario),
        "retry_url": _studio_url(
            product_id=generation.product_id if generation else 0,
            variant_id=generation.variant_id if generation else 0,
            scenario=scenario),
    }


@router.get("/instagram-studio", response_class=HTMLResponse)
async def instagram_studio(
    request: Request,
    q: str = "",
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
    search = (q or "").strip()
    products = []
    product = None
    variants = []
    variant = None
    reference_images = []
    if scenario != SCENARIO_TEXT:
        query = db.query(Product).filter(Product.is_active.is_not(False))
        if search:
            like = f"%{search}%"
            query = query.filter(or_(
                Product.name.ilike(like),
                Product.base_barcode.ilike(like),
                Product.variants.any(ProductVariant.barcode.ilike(like)),
            ))
        products = query.order_by(Product.id.desc()).limit(12).all()

        product = _load_product(db, int_arg(product_id))
        if product is not None:
            variants = sorted(
                [item for item in product.variants if item.is_active is not False],
                key=lambda item: ((item.color or ""), (item.size or ""), item.id),
            )
            variant = _load_variant(db, product, int_arg(variant_id))
            if variant is None and variants:
                variant = variants[0]
            if variant is not None:
                reference_images = variant_reference_images(variant)

    model_slots = available_model_slots(db) if scenario == SCENARIO_MODEL_SCENE else []
    job = generation = None
    requested_job = int_arg(job_id)
    if requested_job:
        job, generation = _job_and_generation(db, requested_job)

    provider, model, model_cost = _model_hint(db)
    context = {
        "q": search,
        "products": products,
        "product": product,
        "variants": variants,
        "variant": variant,
        "reference_images": reference_images,
        "scenes": [{"key": key, "label": scene_label(key)} for key in SCENE_KEYS],
        "aspects": [{"key": key, "label": aspect_label(key)} for key in ASPECTS],
        "scenario_tabs": [{"key": key, "label": label}
                          for key, label in SCENARIOS.items()],
        "model_slots": model_slots,
        "poses": [{"key": key, "label": value["label"]} for key, value in POSES.items()],
        "backgrounds": [{"key": key, "label": value["label"]}
                        for key, value in BACKGROUNDS.items()],
        "framings": [{"key": key, "label": value["label"]}
                     for key, value in FRAMINGS.items()],
        "lightings": [{"key": key, "label": value["label"]}
                      for key, value in LIGHTINGS.items()],
        "compositions": [{"key": key, "label": value["label"]}
                         for key, value in COMPOSITIONS.items()],
        "daily_remaining": instagram_daily_remaining(db),
        "daily_limit": instagram_daily_limit(db),
        "usage": _instagram_usage(db),
        "provider": provider,
        "model": model,
        "model_cost": model_cost,
        "has_logo": bool(_store_logo(db)),
        "is_owner": guard.role == "owner",
        "scenario": scenario,
        "retry_url": _studio_url(
            product_id=product.id if product else 0,
            variant_id=variant.id if variant else 0,
            scenario=scenario),
        "job": job,
        "generation": generation,
        "msg": msg,
        "err": err,
    }
    if job is not None:
        # A page reload keeps the completed/failed result actions (variation,
        # favorite, retry, logo download) instead of dropping them.
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
    if scenario != SCENARIO_TEXT:
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
    if scenario == SCENARIO_TEXT:
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
            )
    if duplicate.first() is not None:
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id, scenario=scenario,
                        err="این تصویر همین حالا در صف است؛ چند لحظه صبر کنید."),
            status_code=303)

    _provider, _model, api_key = get_tryon_selection(db)
    if not api_key:
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id, scenario=scenario,
                        err="کلید ارائه‌دهنده تصویر ذخیره نشده است — تنظیمات → پیامک و هوش مصنوعی."),
            status_code=303)

    logo_path = _store_logo(db)
    logo = bool(form.get("logo")) and bool(logo_path)
    selected_paths = [str(path) for path in form.getlist("image_paths")]

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
            model_slot=model_slot,
            pose_key=pose_key,
            background_key=background_key,
            framing_key=framing_key,
            lighting_key=lighting_key,
            composition_key=composition_key,
            show_full_garment=show_full_garment,
        )
    except InstagramValidationError as error:
        return RedirectResponse(
            _studio_url(product_id=product_id, variant_id=variant_id,
                        scenario=scenario, err=str(error)),
            status_code=303)

    if scenario == SCENARIO_TEXT:
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
        "thumbnail_url": (json_payload(job).get("thumbnail_url")
                          if job.status == "completed" else None),
        "error": job.error_message if job.status == "failed" else None,
    })


def json_payload(job: BackgroundJob) -> dict:
    import json
    try:
        return json.loads(job.payload or "{}")
    except Exception:
        return {}


@router.get("/instagram-studio/history", response_class=HTMLResponse)
async def instagram_history(
    request: Request,
    scenario: str = "",
    product_id: str = "",
    favorite: str = "",
    page: str = "1",
    msg: str = "",
    err: str = "",
    db: Session = Depends(get_db),
):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard

    if scenario not in SCENARIOS:
        scenario = ""
    current_page = page_arg(page)
    query = db.query(InstagramGeneration).options(
        joinedload(InstagramGeneration.job),
        joinedload(InstagramGeneration.product),
        joinedload(InstagramGeneration.variant),
        joinedload(InstagramGeneration.staff),
    )
    if scenario:
        query = query.filter(InstagramGeneration.scenario == scenario)
    selected_product = int_arg(product_id)
    if selected_product:
        query = query.filter(InstagramGeneration.product_id == selected_product)
    if favorite == "1":
        query = query.filter(InstagramGeneration.is_favorite.is_(True))

    total = query.count()
    rows = (query.order_by(InstagramGeneration.created_at.desc())
            .offset((current_page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
    cards = []
    for row in rows:
        job = row.job
        payload = json_payload(job) if job else {}
        cards.append({
            "id": row.id,
            "job_id": row.job_id,
            "status": job.status if job else "missing",
            "image_url": job.result_url if job and job.status == "completed" else "",
            "thumbnail_url": payload.get("thumbnail_url", ""),
            "error": job.error_message if job and job.status == "failed" else "",
            "product_name": (row.product.name if row.product
                             else ("کمپین متنی" if row.scenario == SCENARIO_TEXT else "—")),
            "variant_label": row.variant.display_name if row.variant else "",
            "brief": row.user_text or "",
            "scenario_label": SCENARIOS.get(row.scenario, row.scenario),
            "scene": (scene_label(row.garment_mode) if row.garment_mode in SCENES
                      else ("کمپین آزاد" if row.scenario == SCENARIO_PRODUCT_BRIEF else "—")),
            "model_slot": SLOT_LABELS.get(row.model_slot, row.model_slot or ""),
            "pose": POSES[row.pose_key]["label"] if row.pose_key in POSES else "",
            "background": (BACKGROUNDS[row.background_key]["label"]
                           if row.background_key in BACKGROUNDS else ""),
            "aspect": aspect_label(row.aspect_ratio) if row.aspect_ratio in ASPECTS else row.aspect_ratio,
            "created_at": jalali_str(row.created_at),
            "provider": row.provider or "—",
            "model": row.model or "—",
            "prompt": row.rendered_prompt,
            "is_favorite": row.is_favorite,
            "logo_requested": bool(payload.get("logo")),
            "created_by": row.staff.username if row.staff else "—",
            "variation": (variation_payload(row, payload)
                          if job and job.status == "completed" else None),
        })
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    filter_products = (db.query(Product).filter(Product.is_active.is_not(False))
                       .order_by(Product.name).limit(200).all())
    return templates.TemplateResponse(request, "admin/instagram_history.html", {
        "page_title": "تاریخچه استودیو اینستاگرام",
        "cards": cards,
        "page": current_page,
        "pages": pages,
        "total": total,
        "scenario": scenario,
        "scenarios": [{"key": key, "label": label} for key, label in SCENARIOS.items()],
        "selected_product": selected_product,
        "favorite": favorite,
        "filter_products": filter_products,
        "has_logo": bool(_store_logo(db)),
        "is_owner": guard.role == "owner",
        "msg": msg,
        "err": err,
    })


@router.post("/instagram-studio/history/{generation_id}/favorite")
async def instagram_favorite(generation_id: int, request: Request,
                             db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "manager")
    if not hasattr(guard, "role"):
        return guard
    generation = db.get(InstagramGeneration, generation_id)
    if generation is None:
        return RedirectResponse(_history_url(err="تصویر پیدا نشد."), status_code=303)
    form = await request.form()
    generation.is_favorite = not generation.is_favorite
    db.commit()
    log_action(db, "instagram_favorite",
               f"generation {generation_id} -> {generation.is_favorite}",
               request=request, target_type="instagram_generation",
               target_id=generation_id)
    return RedirectResponse(_safe_back(str(form.get("back") or "")), status_code=303)


@router.post("/instagram-studio/history/{generation_id}/delete")
async def instagram_delete(generation_id: int, request: Request, db: Session = Depends(get_db)):
    guard = require_html_role(request, db, "owner")
    if not hasattr(guard, "role"):
        return guard
    generation = db.query(InstagramGeneration).options(
        joinedload(InstagramGeneration.job)).filter(
        InstagramGeneration.id == generation_id).first()
    if generation is None:
        return RedirectResponse(_history_url(err="تصویر پیدا نشد."), status_code=303)
    job = generation.job
    if job and job.status in ("pending", "processing"):
        return RedirectResponse(
            _history_url(err="این تصویر در حال تولید است و حذف نمی‌شود."),
            status_code=303)

    delete_generation_files(generation)
    if job:
        job.result_path = None
        job.result_url = None
    db.delete(generation)
    db.commit()
    log_action(db, "instagram_delete", f"generation {generation_id}",
               request=request, target_type="instagram_generation", target_id=generation_id)
    return RedirectResponse(_history_url(msg="تصویر حذف شد."), status_code=303)


@router.get("/instagram-studio/history/{generation_id}/download")
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
