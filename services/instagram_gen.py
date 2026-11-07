"""Instagram Studio image generation.

Deliberately independent from the try-on generator: try-on always sends two or
more references to /images/edits, while Instagram needs one reference now and
zero references later. Only stable helpers are reused.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from services.image_gen import ImageGenerationError, classify_response
from services.image_models import (
    GENERATION_JSON,
    SEEDREAM_JSON,
    api_base,
    resolve_generation_selection,
    resolve_selection,
)
from services.instagram_prompts import ASPECTS

logger = logging.getLogger(__name__)

TARGET_SIZES = {key: tuple(value["target"]) for key, value in ASPECTS.items()}
MAX_DIM = 1024
READ_TIMEOUT = 300.0
OUTPUT_RETENTION_DAYS = 7


def _prep_image(path: str) -> bytes:
    """EXIF-fix, flatten alpha, cap at 1024px, JPEG. Same contract as try-on's
    preprocessing but copied so the try-on path never changes."""
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, image).convert("RGB")
        else:
            image = image.convert("RGB")
        image.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90, optimize=True, subsampling=0)
        return buffer.getvalue()


def _data_uri(raw: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _logo_layer(path: str, width: int) -> Image.Image | None:
    try:
        with Image.open(path) as opened:
            logo = opened.convert("RGBA")
            logo.load()
    except Exception as error:
        logger.warning("Instagram logo could not be loaded at %s: %s", path, error)
        return None
    scale = max(1, int(width * 0.12)) / max(logo.width, 1)
    size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
    return logo.resize(size, Image.LANCZOS)


def normalize_image(
    raw: bytes,
    aspect_ratio: str,
    *,
    logo: bool = False,
    logo_path: str = "",
) -> bytes:
    """Center-crop to the exact Instagram ratio, resize, optionally badge, JPEG.

    Raises KeyError for an unknown aspect and ImageGenerationError for bytes
    that are not an image.
    """
    target_width, target_height = TARGET_SIZES[aspect_ratio]
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.load()
    except Exception as error:
        raise ImageGenerationError(
            "تصویر بازگشتی ارائه‌دهنده معتبر نبود.",
            category="bad_response", code="bad_image",
        ) from error

    source_width, source_height = image.size
    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_width = max(1, int(source_height * target_ratio))
        left = (source_width - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, source_height))
    elif source_ratio < target_ratio:
        crop_height = max(1, int(source_width / target_ratio))
        top = (source_height - crop_height) // 2
        image = image.crop((0, top, source_width, top + crop_height))
    image = image.resize((target_width, target_height), Image.LANCZOS)

    if logo and logo_path:
        layer = _logo_layer(logo_path, target_width)
        if layer is not None:
            canvas = image.convert("RGBA")
            canvas.alpha_composite(layer, (0, 0))
            image = canvas.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def write_output(image_bytes: bytes, output_path: str) -> None:
    """Write the final JPEG and drop this folder's expired outputs.

    The owner downloads and keeps the images outside the app, so the on-disk
    copy only has to outlive the result view.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)
    cutoff = time.time() - OUTPUT_RETENTION_DAYS * 86400
    folders = (path.parent, path.parent / "thumbs")
    for folder in folders:
        for old in folder.glob("ig_*.jpg"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
            except OSError:
                continue


def _first_image(body: dict) -> str:
    for key in ("data", "images"):
        for item in body.get(key) or []:
            if isinstance(item, dict):
                raw = item.get("b64_json") or item.get("url")
                if raw:
                    return raw
    for choice in body.get("choices") or []:
        message = choice.get("message") or {}
        for item in message.get("images") or []:
            if isinstance(item, dict):
                url = (item.get("image_url") or {}).get("url")
                if url:
                    return url
    return ""


def _error_text(provider: str, category: str, code: str, status: int, detail: str) -> str:
    """Actionable Persian text; provider codes stay in the log."""
    logger.warning(
        "instagram/%s failed category=%s code=%s status=%s detail=%s",
        provider, category, code, status, (detail or "").strip()[:300])
    if category == "content_policy":
        return ("ارائه‌دهنده این تصویر را به دلیل خط‌مشی محتوا رد کرد. "
                "متن کمپین را ساده‌تر کنید یا عبارت‌های حساس را حذف کنید.")
    if category == "auth":
        return (f"کلید {provider} معتبر نیست یا دسترسی ندارد. "
                "کلید را در تنظیمات → پیامک و هوش مصنوعی بررسی کنید.")
    if category == "model_unavailable":
        return ("مدل انتخاب‌شده روی ارائه‌دهنده کانال فعال ندارد. "
                "در تنظیمات مدل دیگری انتخاب کنید.")
    if category == "rate_limit":
        return "محدودیت نرخ ارائه‌دهنده؛ کمی بعد دوباره تلاش کنید."
    if category == "server":
        return "ارائه‌دهنده خطای سرور داد؛ کمی بعد دوباره تلاش کنید."
    if category == "network":
        return ("ارتباط با ارائه‌دهنده برقرار نشد؛ اتصال شبکه را بررسی و "
                "دوباره تلاش کنید.")
    if category in ("no_image", "refusal"):
        return ("ارائه‌دهنده تصویری برنگرداند. متن کمپین را روشن‌تر و کوتاه‌تر "
                "کنید و دوباره تلاش کنید.")
    if category in ("no_op", "source_echo"):
        return ("ارائه‌دهنده همان تصویر مرجع را برگرداند. صحنه، ژست یا عکس "
                "مرجع دیگری انتخاب کنید.")
    if category == "bad_response":
        return "پاسخ ارائه‌دهنده قابل خواندن نبود؛ دوباره تلاش کنید."
    return "درخواست ارائه‌دهنده رد شد؛ گزینه‌ها را بررسی و دوباره تلاش کنید."


def _build_request(
    model_entry: dict,
    prompt: str,
    images: list[bytes],
    aspect_ratio: str,
    *,
    size_fallback: bool = False,
) -> dict:
    aspect = ASPECTS[aspect_ratio]
    if model_entry["body"] == GENERATION_JSON:
        # Text-to-image only: no image field, no edit options.
        sizes = model_entry.get("sizes") or {}
        size = "1024x1024" if size_fallback else sizes.get(aspect_ratio, "1024x1024")
        return {"json": {
            "model": model_entry["id"],
            "prompt": prompt,
            "n": 1,
            "size": size,
        }}
    if model_entry["body"] == SEEDREAM_JSON:
        payload = {
            "model": model_entry["id"],
            "prompt": prompt,
            "size": "2K" if size_fallback else aspect["seedream_size"],
            "response_format": "b64_json",
            "sequential_image_generation": "disabled",
            "watermark": False,
        }
        if images:
            payload["image"] = [_data_uri(blob) for blob in images]
        return {"json": payload}

    data = {
        "model": model_entry["id"],
        "prompt": prompt,
        "size": aspect["gpt_size"],
        "quality": "medium",
        "output_format": "jpeg",
        "n": "1",
    }
    if not images:
        return {"json": data}
    files = [
        ("image[]", (f"reference{i}.jpg", blob, "image/jpeg"))
        for i, blob in enumerate(images, 1)
    ]
    return {"data": data, "files": files}


def generate_instagram_image(
    *,
    prompt: str,
    reference_paths: list[str],
    aspect_ratio: str,
    provider: str,
    model: str,
    api_key: str,
    logo: bool = False,
    logo_path: str = "",
    job_id: int | None = None,
) -> bytes:
    """Send one request and return the normalized JPEG bytes."""
    if reference_paths:
        provider_id, entry = resolve_selection(provider, model)
    else:
        provider_id, entry = resolve_generation_selection(provider, model)
    if not (api_key or "").strip():
        raise ImageGenerationError(
            f"کلید {provider_id} ذخیره نشده است.",
            category="auth", code="missing_key",
        )

    existing = [path for path in reference_paths if path and Path(path).exists()]
    if reference_paths and len(existing) != len([path for path in reference_paths if path]):
        raise ImageGenerationError(
            "یکی از عکس‌های مرجع محصول پیدا نشد — فایل حذف شده یا مسیر نامعتبر است.",
            category="bad_request", code="missing_reference",
        )
    prepared = [_prep_image(path) for path in existing]
    request = _build_request(entry, prompt, prepared, aspect_ratio)
    url = api_base(provider_id) + ("/images/edits" if prepared else "/images/generations")
    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info(
        "instagram/%s %s: %d reference(s), aspect=%s, job=%s",
        provider_id, entry["id"], len(prepared), aspect_ratio, job_id,
    )

    try:
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=READ_TIMEOUT,
                                                write=60.0, pool=30.0)) as client:
            response = client.post(url, headers=headers, **request)
            # Seedream accepts the exact Instagram size per its docs, but a
            # rejected custom size is a free 4xx: retry once at 2K and crop.
            # Generation models fall back to a square, then crop locally.
            if (response.status_code in (400, 422)
                    and entry["body"] in (SEEDREAM_JSON, GENERATION_JSON)
                    and "size" in response.text.lower()):
                logger.info("instagram/%s rejected custom size; retrying smaller",
                            provider_id)
                request = _build_request(entry, prompt, prepared, aspect_ratio,
                                         size_fallback=True)
                response = client.post(url, headers=headers, **request)
            if response.status_code != 200:
                category, code = classify_response(response.status_code, response.text)
                raise ImageGenerationError(
                    _error_text(provider_id, category, code, response.status_code, response.text),
                    category=category, code=code, status=response.status_code,
                    body=response.text[:500],
                )
            try:
                body = response.json()
            except ValueError as error:
                raise ImageGenerationError(
                    f"پاسخ {provider_id} JSON نبود.",
                    category="bad_response", code="not_json",
                    status=200, body=response.text[:500],
                ) from error
            raw = _first_image(body if isinstance(body, dict) else {})
            if not raw:
                category, code = classify_response(200, response.text)
                if category == "content_policy":
                    raise ImageGenerationError(
                        _error_text(provider_id, category, code, 200, response.text),
                        category=category, code=code, status=200,
                        body=response.text[:500],
                    )
                raise ImageGenerationError(
                    "ارائه‌دهنده هیچ تصویری برنگرداند.",
                    category="no_image", code="no_image",
                    body=json.dumps(body, ensure_ascii=False)[:500] if isinstance(body, dict) else "",
                )
            if raw.startswith("http"):
                fetched = client.get(
                    raw, timeout=httpx.Timeout(connect=15.0, read=60.0,
                                               write=15.0, pool=15.0))
                if fetched.status_code != 200:
                    raise ImageGenerationError(
                        f"دانلود تصویر خروجی ناموفق بود (HTTP {fetched.status_code}).",
                        category="network", code="download_failed",
                    )
                image_bytes = fetched.content
            else:
                if raw.startswith("data:"):
                    raw = raw.split(",", 1)[1]
                try:
                    image_bytes = base64.b64decode(raw)
                except Exception as error:
                    raise ImageGenerationError(
                        "تصویر بازگشتی base64 معتبر نبود.",
                        category="bad_response", code="bad_base64",
                    ) from error
    except httpx.ReadTimeout as error:
        raise ImageGenerationError(
            f"مهلت پاسخ {provider_id} تمام شد — ممکن است هزینه گرفته شده باشد.",
            category="network", code="read_timeout",
        ) from error
    except httpx.HTTPError as error:
        raise ImageGenerationError(
            f"دسترسی به {provider_id} برقرار نشد: {error.__class__.__name__}",
            category="network", code=error.__class__.__name__,
        ) from error

    return normalize_image(image_bytes, aspect_ratio, logo=logo, logo_path=logo_path)
