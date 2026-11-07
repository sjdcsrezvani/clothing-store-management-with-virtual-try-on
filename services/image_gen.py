"""Virtual try-on image generation through the configured provider.

Kid photo and garment photos are sent as reference images. The provider and
model come from Settings; the catalog decides the exact body each model gets
(multipart edits or Seedream JSON) and the cheapest options it accepts. Every
attempt lands in the in-memory debug log for the try-on page.
"""
import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageOps

from services.image_models import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    SEEDREAM_JSON,
    api_base,
    key_setting,
    resolve_selection,
)

logger = logging.getLogger(__name__)

TRYON_PROVIDER_SETTING = "tryon_provider"
TRYON_MODEL_SETTING = "tryon_model"
GENERATION_MODEL_SETTING = "instagram_generation_model"
TRYON_KEY_SETTINGS = ("tryon_api_key", "tryon_api_key_avalai")


def get_tryon_selection(db) -> tuple[str, str, str]:
    """(provider, model, key) for image edits from Settings, resolved at run time.

    No environment or built-in fallback: a key is owner-managed. An unknown
    provider/model falls back to the catalog default instead of breaking the
    page after a rename.
    """
    from models import Settings
    rows = {r.key: (r.value or "") for r in db.query(Settings).filter(
        Settings.key.in_((TRYON_PROVIDER_SETTING, TRYON_MODEL_SETTING) + TRYON_KEY_SETTINGS)
    ).all()}
    provider, entry = resolve_selection(
        rows.get(TRYON_PROVIDER_SETTING, ""), rows.get(TRYON_MODEL_SETTING, ""))
    key = rows.get(key_setting(provider), "").strip()
    return provider, entry["id"], key


def get_generation_selection(db) -> tuple[str, str, str]:
    """(provider, model, key) for text-to-image, a separate model setting.

    The provider is shared with try-on so one key still covers both paths, but
    the model comes from the provider's generation-only catalog.
    """
    from models import Settings
    from services.image_models import resolve_generation_selection
    rows = {r.key: (r.value or "") for r in db.query(Settings).filter(
        Settings.key.in_((TRYON_PROVIDER_SETTING, GENERATION_MODEL_SETTING)
                         + TRYON_KEY_SETTINGS)
    ).all()}
    provider, entry = resolve_generation_selection(
        rows.get(TRYON_PROVIDER_SETTING, ""),
        rows.get(GENERATION_MODEL_SETTING, ""))
    key = rows.get(key_setting(provider), "").strip()
    return provider, entry["id"], key


def mask_tryon_key(key: str) -> str:
    """Display form of a stored key: tail only, never the secret."""
    key = (key or "").strip()
    if len(key) <= 8:
        return "••••" if key else ""
    return "…" + key[-4:]

MAX_DIM = 1024
# S24 Ultra native JPEGs are routinely 8–25 MB; raise the ceiling so the
# preprocessor never has to resize (which the user explicitly wants to avoid).
MAX_BYTES = 25_000_000

def _load_logo(path: str) -> Optional[Image.Image]:
    """Open a shop logo from disk; None when unset or unreadable."""
    if not path:
        return None
    file = Path(path.lstrip("/"))
    try:
        logo = Image.open(file)
        logo.load()
        return logo
    except Exception as e:
        logger.warning("Could not load shop logo at %s: %s", file, e)
        return None


def composite_logo(image_bytes: bytes, logo_path: str = "", logo_width: int = 230) -> bytes:
    """Return PNG bytes with the shop's logo pinned to the top-left corner.
    No configured logo means no composite — a plain PNG re-encode."""
    base = Image.open(io.BytesIO(image_bytes))
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    logo = _load_logo(logo_path)
    if logo is None:
        buf = io.BytesIO()
        base.save(buf, "PNG")
        return buf.getvalue()

    if logo.mode != "RGBA":
        logo = logo.convert("RGBA")
    # Scale logo preserving aspect ratio.
    scale = logo_width / max(logo.width, 1)
    new_size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
    logo_resized = logo.resize(new_size, Image.LANCZOS)

    # Pinned to the top-left edge of the image, no inset.
    base.alpha_composite(logo_resized, (0, 0))

    buf = io.BytesIO()
    base.save(buf, "PNG", optimize=True)
    return buf.getvalue()

MAIN_PROMPT = (
    # ── Roles (context) ──
    "Image 1 is the person — use for identity ONLY (face, skin, hair, body shape). "
    "Images 2 is the target garment on their own — use for the garment ONLY. "
    "Do not transfer, copy, or be inspired by any clothing visible in image 1. "

    # ── Person identity — preserve structure exactly ──
    # (Expression/beautify are intentionally NOT here — they're controlled by the
    # per-request FACE RULE clause, which lands after this prompt.)
    "Preserve the person's face structure exactly: same face shape, eye shape/spacing/size, "
    "nose, lip shape/fullness, brow shape, jawline, chin, ear shape, hairline, skin tone and "
    "undertones. Preserve body shape, build, and apparent age unchanged. "

    # ── Garment fidelity — the core mission ──
    "PRIMARY RULE — TARGET GARMENT FIDELITY: the target garment shown in the product photos must be "
    "reproduced with 1:1 precision — same category, silhouette, sleeve length, neckline, hood "
    "presence, pockets, hem, cuffs, fabric, prints, and color. No guessing, no substitutions. "
    "A sweatshirt stays a sweatshirt (not a hoodie). A long-sleeve tee stays long-sleeve (not short). "
    "A boilersuit stays a one-piece (not separate top+bottom). A crew-neck stays crew-neck (not v-neck). "

    # ── Fit ──
    "Believable natural fit: real fabric weight, natural folds at joints, no floating cloth, no warped prints. "

    # ── Framing — wider shot, person further from camera ──
    "Framing: full-length shot head-to-toe, 50mm lens, slight low angle, subject occupies roughly half "
    "the frame height, generous headroom and footroom, ample clean negative space all around, "
    "person must NOT fill the frame, backdrop clearly visible. Centered composition, no joint crops. "

    # ── Pose (overridden by per-request pose clause when present) ──
    "Natural catalog stance — relaxed, no rigid symmetry, no mannequin pose. "

    # ── Studio & lighting (merged) ──
    "Studio lighting: large softbox key at 45° camera-right above subject, two diffused fills camera-left "
    "and behind, rim/hair light from behind for separation, silver reflector camera-left for face fill. "
    "Soft wraparound shadows, even exposure, balanced white. All shadows fall in the key-light direction. "
    "No floating subject, no missing contact shadows. "

    # ── Camera & color (merged and compressed) ──
    "Photorealistic editorial catalog look: tack-sharp on garment and face, shallow depth of field "
    "with soft bokeh backdrop, natural catchlights in both eyes. Natural true-to-life color, "
    "accurate skin tones, no filters, no oversaturation, no crushed blacks, no clipped highlights, "
    "no color cast, no banding in gradients. No sensor noise, no film grain, no chromatic aberration. "

    # ── Detail quality ──
    "Skin: visible pores, fine peach-fuzz, subtle subsurface warmth, no airbrushed or porcelain look. "
    "Garment: visible fabric texture, real seam stitching, real buttonholes, real zipper teeth. "
    "No painted-on or vector-graphic finish. "

    # ── Quality guardrails (tight checklist) ──
    "Image must be a flawless studio photograph — no AI artifacts, no smudged edges, no halos, "
    "no painterly or 3D-render look, no blur on the subject, no motion blur, no jpeg compression noise. "
    "Anatomically correct: exactly two arms, two hands (five fingers each), two legs, two feet. "
    "No twisted or fused limbs, no extra/missing fingers, no warped hands, no asymmetric clothing, "
    "no floating fabric disconnected from the body. One coherent person only — no duplicates, no mirror reflections. "

    # ── Reminder — the 3 most critical rules (at end for recency bias) ──
    "REMEMBER: (1) The garment in the product photo is the EXACT garment — copy it precisely, no substitutions. "
    "(2) Ignore ALL clothes on the person in image 1 — they are irrelevant. "
    "(3) Preserve the person's face structure, body shape and apparent age exactly — never age, "
    "slim, or reshape them. "
)

# The model sometimes answers 200 with the source photo itself instead of an
# edit. Keep this last so it has the strongest recency bias.
EDIT_REMINDER = (
    "Return a new edited photograph: the person must be wearing the target garment; "
    "never return the source photo unchanged."
)


class ImageGenerationError(Exception):
    """Provider or transport failure, with the fields the debug panel needs."""

    def __init__(self, message: str, *, category: str = "unknown", code: str = "",
                 status: int | None = None, body: str = ""):
        super().__init__(message)
        self.category = category
        self.code = code
        self.status = status
        self.body = body


# Content-policy refusals are provider-specific; the raw code is kept for the
# panel, this list only decides the one label the shop sees.
_POLICY_MARKERS = (
    "moderation_blocked", "moderation", "sensitivecontentdetected",
    "data_inspection_failed", "datainspectionfailed", "ipinfringement",
    "customroleblocked", "content_filter", "prohibited_content",
    "image_safety", "safety_violation", "request moderated",
    "content moderated", "policy_violation", "policy violation",
)


def _error_parts(text: str) -> tuple[str, str]:
    """(provider code, human message) from an OpenAI-ish error envelope."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return "", text or ""
    if not isinstance(data, dict):
        return "", text or ""
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or error.get("type") or ""), str(error.get("message") or "")
    if isinstance(error, str):
        return "", error
    return str(data.get("code") or data.get("error_code") or ""), str(data.get("message") or "")


def classify_response(status: int, text: str) -> tuple[str, str]:
    """Normalize a provider failure into (category, provider code)."""
    code, message = _error_parts(text)
    blob = f"{code} {message} {text}".lower()
    if status in (401, 403):
        return "auth", code
    # The model is in the catalog but the key's group has no upstream channel
    # for it. That is a routing refusal, not a server fault: say so, because
    # "خطای سرور" sends the owner looking in the wrong place.
    if "model_not_found" in blob or "no available channel" in blob:
        return "model_unavailable", code
    if any(marker in blob for marker in _POLICY_MARKERS):
        return "content_policy", code
    if status == 429 or "rate_limit" in blob or "rate limit" in blob:
        return "rate_limit", code
    if status >= 500:
        return "server", code
    return "bad_request", code


def _describe(provider: str, category: str, code: str, status: int, message: str) -> str:
    code_txt = f" (کد: {code})" if code else ""
    detail = (message or "").strip()[:220]
    if category == "content_policy":
        return (f"ارائه‌دهنده این تصویر را به دلیل خط‌مشی محتوا رد کرد{code_txt} "
                f"— HTTP {status}. {detail}")
    if category == "auth":
        return f"کلید {provider} معتبر نیست یا دسترسی ندارد (HTTP {status}){code_txt}."
    if category == "model_unavailable":
        return (f"مدل انتخاب‌شده روی {provider} کانال فعال ندارد{code_txt} "
                f"— گروه این کلید به این مدل دسترسی ندارد. در تنظیمات مدل دیگری انتخاب کنید. {detail}")
    if category == "rate_limit":
        return f"محدودیت نرخ {provider} (HTTP 429){code_txt}. کمی بعد دوباره امتحان کنید."
    if category == "server":
        return f"{provider} خطای سرور داد (HTTP {status}){code_txt}: {detail}"
    return f"درخواست {provider} رد شد (HTTP {status}){code_txt}: {detail}"


def _data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _refusal_text(body: dict) -> str:
    """Text a provider returned instead of an image (Gemini does this)."""
    texts: list[str] = []
    for choice in body.get("choices") or []:
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part.get("text") for part in content
                         if isinstance(part, dict) and part.get("text"))
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                texts.append(part["text"])
    for key in ("message", "detail", "error_description"):
        if isinstance(body.get(key), str):
            texts.append(body[key])
    return next((text.strip() for text in texts if text and text.strip()), "")


def _safety_reason(body: dict) -> str:
    """Gemini/Qwen sometimes answer 200 with an empty candidate and a finish
    reason instead of an error envelope."""
    reasons: list[str] = []
    for candidate in body.get("candidates") or []:
        reasons.append(str(candidate.get("finishReason") or candidate.get("finish_reason") or ""))
    for choice in body.get("choices") or []:
        reasons.append(str(choice.get("finish_reason") or choice.get("finishReason") or ""))
    markers = ("safety", "prohibited", "content_filter", "image_safety")
    return next((reason for reason in reasons
                 if reason and any(marker in reason.lower() for marker in markers)), "")


def _policy_code(body: dict) -> str:
    """Some gateways wrap a policy refusal in a 200 body instead of HTTP 400."""
    code, message = _error_parts(json.dumps(body, ensure_ascii=False))
    blob = f"{code} {message}".lower()
    return code if any(marker in blob for marker in _POLICY_MARKERS) else ""


def _first_image(body: dict) -> str:
    """The first b64/url image anywhere in the known response shapes."""
    for key in ("data", "images"):
        for item in body.get(key) or []:
            if isinstance(item, dict):
                raw = item.get("b64_json") or item.get("url")
                if raw:
                    return raw
    for choice in body.get("choices") or []:
        message = choice.get("message") or {}
        for item in message.get("images") or []:
            url = (item.get("image_url") or {}).get("url") if isinstance(item, dict) else None
            if url:
                return url
        content = message.get("content")
        if isinstance(content, str) and content.startswith("data:"):
            return content
        if isinstance(content, list):
            for part in content:
                inline = (part.get("inline_data") or part.get("inlineData") or {}) \
                    if isinstance(part, dict) else {}
                if inline.get("data"):
                    return "data:image/jpeg;base64," + inline["data"]
    return ""


def get_provider(api_key: str = "", provider: str = DEFAULT_PROVIDER,
                 model: str = DEFAULT_MODEL) -> "ImageGenService":
    return ImageGenService(api_key=api_key, provider=provider, model=model)


class ImageGenService:
    """Send one try-on attempt to the selected provider and model."""

    def __init__(self, api_key: str | None = None, provider: str = DEFAULT_PROVIDER,
                 model: str = DEFAULT_MODEL):
        # Callers pass values resolved from Settings, so a rotated key takes
        # effect without a restart. A missing value stays missing and is
        # rejected below rather than falling back to a default.
        self.provider, self.model = resolve_selection(provider, model)
        self.model_id = self.model["id"]
        self.api_base = api_base(self.provider)
        self.api_url = self.api_base + "/images/edits"
        self.api_key = (api_key or "").strip()
        # 300s read timeout. A queued generation can take 2–4 minutes; cutting
        # the socket earlier charges the owner and loses the image, so keep it
        # open past the slowest realistic queue time.
        self.client = httpx.Client(timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0))

    # ── Public entry point ──

    def generate(
        self,
        prompt: str,
        reference_image_paths: Optional[list[str]] = None,
        prompt_mid: str = "",
        prompt_suffix: str = "",
        job_id: int | None = None,
    ) -> bytes:
        """Return raw result-image bytes. reference_image_paths: person first,
        then garments. `job_id` only tags the debug entry.

        Assembly order: `prompt` + MAIN_PROMPT + `prompt_mid` (background/pose/
        details, which override MAIN_PROMPT's generic defaults) + `prompt_suffix`
        (the outfit rule, last for recency bias)."""
        if not self.api_key:
            raise ImageGenerationError(f"کلید {self.provider} ذخیره نشده است.",
                                       category="auth", code="missing_key")
        paths = [p for p in (reference_image_paths or []) if p and Path(p).exists()]
        if len(paths) < 2:
            raise ImageGenerationError("Need at least a person photo and one garment photo.")

        started = time.perf_counter()
        timings: dict[str, int] = {"prep": 0, "request": 0, "download": 0}

        prep_started = time.perf_counter()
        kid = self._prep(paths[0])
        prepped: dict[str, bytes] = {}
        # Garment prep is independent PIL work — parallel, memoized by
        # (path, mtime, size), so a 3-garment run pays ~1× instead of 3×.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._prep_memo, p): p for p in paths[1:]}
            for future in concurrent.futures.as_completed(futures):
                prepped[futures[future]] = future.result()
        images = [("person.jpg", kid)] + [
            (f"garment{i}.jpg", prepped[path]) for i, path in enumerate(paths[1:], 1)
        ]
        timings["prep"] = int((time.perf_counter() - prep_started) * 1000)

        prompt_text = self._build_prompt(prompt, prompt_mid, prompt_suffix)
        kwargs, fields = self._build_body(prompt_text, images)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info("%s/%s try-on: kid + %d garment(s), body=%s",
                    self.provider, self.model_id, len(paths) - 1, self.model["body"])

        request_started = time.perf_counter()
        try:
            # No retry: the model may already be billed on a timeout or 5xx.
            resp = self.client.post(self.api_url, headers=headers, **kwargs)
        except httpx.ReadTimeout as e:
            timings["request"] = int((time.perf_counter() - request_started) * 1000)
            message = (f"مهلت پاسخ {self.provider} تمام شد — ممکن است هزینه گرفته شده باشد. "
                       f"({e.__class__.__name__})")
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=None, category="network", code="read_timeout",
                                message=message) from e
        except httpx.HTTPError as e:
            timings["request"] = int((time.perf_counter() - request_started) * 1000)
            message = f"دسترسی به {self.provider} برقرار نشد: {e.__class__.__name__}"
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=None, category="network", code=e.__class__.__name__,
                                message=message) from e
        timings["request"] = int((time.perf_counter() - request_started) * 1000)

        if resp.status_code != 200:
            category, code = classify_response(resp.status_code, resp.text)
            message = _describe(self.provider, category, code, resp.status_code, resp.text)
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=resp.status_code, category=category, code=code,
                                message=message, body=resp.text[:500])

        try:
            body = resp.json()
        except ValueError as e:
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=200, category="bad_response", code="not_json",
                                message=f"پاسخ {self.provider} JSON نبود.",
                                body=resp.text[:500]) from e
        if not isinstance(body, dict):
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=200, category="bad_response", code="not_object",
                                message=f"پاسخ {self.provider} ساختار مورد انتظار را نداشت.",
                                body=json.dumps(body, ensure_ascii=False)[:500])

        usage = body.get("usage")
        if usage:
            logger.info("%s/%s usage: %s", self.provider, self.model_id, usage)

        download_started = time.perf_counter()
        try:
            image_bytes = self._extract_image(body)
        except ImageGenerationError as error:
            raise self._failure(job_id=job_id, started=started, timings=timings, fields=fields,
                                status=200, category=error.category, code=error.code,
                                message=str(error), body=error.body) from error
        timings["download"] = int((time.perf_counter() - download_started) * 1000)

        # A 200 that echoes the prepped person photo is a billed non-edit.
        # Fail it so the shop never saves it as a successful try-on.
        # shortcut: exact bytes only; a re-encoded echo would slip through.
        if image_bytes == kid:
            raise self._failure(
                job_id=job_id, started=started, timings=timings, fields=fields,
                status=200, category="no_op", code="source_echo",
                message="ارائه‌دهنده همان عکس شخص را برگرداند و لباس را تغییر نداد "
                        "— دوباره تلاش کنید یا عکس/ژست دیگری انتخاب کنید.",
                body='{"reason":"output_equals_person_input"}')

        self._record(job_id=job_id, started=started, timings=timings, fields=fields,
                     ok=True, status=200, category="", code="", error="",
                     result_bytes=len(image_bytes))
        return image_bytes

    # ── Request bodies ──

    def _build_body(self, prompt: str, images: list[tuple[str, bytes]]) -> tuple[dict, str]:
        options = self.model.get("options") or {}
        body_type = self.model["body"]
        if body_type == SEEDREAM_JSON:
            # Seedream edits are JSON only. 2K is the smallest tier it accepts
            # on /v1/images/edits; "1K" is rejected as an invalid size.
            payload = {
                "model": self.model_id,
                "prompt": prompt,
                "image": [_data_uri(blob) for _name, blob in images],
                "size": "2K",
                "response_format": "b64_json",
                "sequential_image_generation": "disabled",
                "watermark": False,
            }
            fields = self._fields_label(
                {k: v for k, v in payload.items() if k not in ("model", "prompt", "image")},
                len(images))
            return {"json": payload}, fields

        data = {"model": self.model_id, "prompt": prompt, **options}
        files = [("image[]", (name, blob, "image/jpeg")) for name, blob in images]
        return {"data": data, "files": files}, self._fields_label(options, len(images))

    @staticmethod
    def _fields_label(options: dict, image_count: int) -> str:
        bits = [f"{key}={value}" for key, value in options.items()]
        bits.append(f"images={image_count}")
        return ", ".join(bits)

    # ── Debug log ──

    def _record(self, *, job_id, started, timings, fields, ok, status,
                category, code, error, body="", result_bytes=0) -> None:
        from services import tryon_debug
        total_ms = int((time.perf_counter() - started) * 1000)
        tryon_debug.record(
            job_id=job_id, provider=self.provider, model=self.model_id, url=self.api_url, ok=ok,
            status=status, category=category, code=code, error=(error or "")[:400],
            raw=(body or "")[:500], fields=fields, result_bytes=result_bytes,
            prep_ms=timings.get("prep", 0), request_ms=timings.get("request", 0),
            download_ms=timings.get("download", 0), total_ms=total_ms,
        )
        logger.info(
            "try-on %s/%s job=%s ok=%s http=%s total=%dms prep=%dms request=%dms download=%dms%s",
            self.provider, self.model_id, job_id, ok, status, total_ms,
            timings.get("prep", 0), timings.get("request", 0), timings.get("download", 0),
            f" error={error[:200]}" if error else "",
        )

    def _failure(self, *, job_id, started, timings, fields, status, category, code,
                 message, body="") -> ImageGenerationError:
        self._record(job_id=job_id, started=started, timings=timings, fields=fields,
                     ok=False, status=status, category=category, code=code,
                     error=message, body=body)
        logger.warning("try-on %s/%s failed: %s", self.provider, self.model_id, message)
        return ImageGenerationError(message, category=category, code=code,
                                    status=status, body=body)

    # ── Prompt ──

    def _build_prompt(self, base: str, mid: str = "", suffix: str = "") -> str:
        """Assemble the final prompt in recency-weighted order:
           `base` (product intro) + MAIN_PROMPT + `mid` (background/pose/details)
           + `suffix` (outfit-mode rule) + EDIT_REMINDER (never echo the source)."""
        base = (base or "").strip().rstrip(".")
        out = f"{base}. {MAIN_PROMPT}" if base else MAIN_PROMPT
        mid = (mid or "").strip()
        if mid:
            out += f" {mid}"
        suffix = (suffix or "").strip()
        if suffix:
            out += f" {suffix}"
        return f"{out} {EDIT_REMINDER}"

    def test_connection(self, timeout: float = 15.0) -> dict:
        """Cheap non-billable probe: the gateway models endpoint, short leash.
        A listed model wins; an absent list still counts as a reachable key."""
        if not self.api_key:
            return {"ok": False, "error": f"کلید {self.provider} ذخیره نشده است."}
        try:
            resp = self.client.get(
                self.api_base + "/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0, pool=10.0),
            )
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"دسترسی به {self.provider} ناموفق بود: {e.__class__.__name__}"}
        if resp.status_code == 200:
            try:
                listed = [str(item.get("id") or "") for item in resp.json().get("data") or []]
            except (ValueError, AttributeError):
                listed = []
            if listed and self.model_id not in listed:
                return {"ok": False,
                        "error": f"کلید معتبر است، ولی مدل «{self.model_id}» در فهرست {self.provider} نیست."}
            return {"ok": True}
        if resp.status_code in (401, 403):
            return {"ok": False, "error": f"کلید {self.provider} معتبر نیست (401/403)."}
        return {"ok": False, "error": f"{self.provider} پاسخ {resp.status_code} داد."}

    # ── Image preprocessing ──

    _prep_cache: dict = {}

    def _prep_memo(self, path: str, max_bytes: int = MAX_BYTES) -> bytes:
        """Memoized _prep keyed by (path, mtime, size) — catalog garment
        photos repeat across generations, so repeat runs skip the work."""
        try:
            stat = Path(path).stat()
            memo_key = (path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return self._prep(path, max_bytes)
        hit = self._prep_cache.get(memo_key)
        if hit is not None:
            return hit
        data = self._prep(path, max_bytes)
        if len(self._prep_cache) > 64:
            self._prep_cache.clear()
        self._prep_cache[memo_key] = data
        return data

    def _prep(self, path: str, max_bytes: int = MAX_BYTES) -> bytes:
        """Resize longest edge to MAX_DIM, EXIF-fix, flatten alpha, JPEG-encode to <=max_bytes.
        Smaller input = faster upload + faster model. Resize preserves all garment/face detail
        since 1024px is well above what the model actually consumes.
        Fast path: an already-small JPEG ships as-is (EXIF transpose only),
        skipping the decode/re-encode loop entirely."""
        raw_size = 0
        try:
            raw_size = Path(path).stat().st_size
        except OSError:
            pass
        with Image.open(path) as probe:
            if (probe.format == "JPEG" and max(probe.size) <= MAX_DIM
                    and 0 < raw_size <= max_bytes):
                try:
                    orientation = probe.getexif().get(0x0112, 1)
                except Exception:
                    orientation = 1
                if orientation == 1 and probe.mode == "RGB":
                    with open(path, "rb") as handle:
                        return handle.read()
        img = ImageOps.exif_transpose(Image.open(path))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img).convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
        buf = io.BytesIO()
        for q in (90, 85, 80, 75):
            buf.seek(0)
            buf.truncate()
            img.save(buf, "JPEG", quality=q, optimize=True, subsampling=0)
            if buf.tell() <= max_bytes:
                break
        return buf.getvalue()

    def _extract_image(self, body: dict) -> bytes:
        """Pull the first result image (b64 or url) out of the provider JSON.
        A text-only 200 is a refusal, not a missing image, and says so."""
        raw = _first_image(body)
        if not raw:
            policy = _policy_code(body)
            if policy:
                raise ImageGenerationError(
                    f"ارائه‌دهنده این تصویر را به دلیل خط‌مشی محتوا رد کرد (کد: {policy}).",
                    category="content_policy", code=policy,
                    body=json.dumps(body, ensure_ascii=False)[:500])
            reason = _safety_reason(body)
            if reason:
                raise ImageGenerationError(
                    f"ارائه‌دهنده این تصویر را به دلیل خط‌مشی محتوا رد کرد (کد: {reason}).",
                    category="content_policy", code=reason,
                    body=json.dumps(body, ensure_ascii=False)[:500])
            refusal = _refusal_text(body)
            if refusal:
                raise ImageGenerationError(
                    f"ارائه‌دهنده تصویری برنگرداند و متنی فرستاد: {refusal[:300]}",
                    category="refusal", code="text_response",
                    body=json.dumps(body, ensure_ascii=False)[:500])
            raise ImageGenerationError(
                "ارائه‌دهنده هیچ تصویری برنگرداند.",
                category="no_image", code="no_image",
                body=json.dumps(body, ensure_ascii=False)[:500])
        if raw.startswith("http"):
            return self._fetch_url(raw)
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            return base64.b64decode(raw)
        except Exception as error:
            raise ImageGenerationError("تصویر بازگشتی base64 معتبر نبود.",
                                       category="bad_response", code="bad_base64") from error

    def _fetch_url(self, url: str) -> bytes:
        """Download the image at `url` when the provider stores the result on
        its CDN instead of inlining base64. 60s ceiling — these are small CDN
        objects, anything longer means the host is dead."""
        try:
            resp = self.client.get(url, timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0))
        except httpx.HTTPError as e:
            raise ImageGenerationError(f"Failed to fetch result URL: {url} ({e})",
                                       category="network", code="download_failed") from e
        if resp.status_code != 200:
            raise ImageGenerationError(f"Result URL returned HTTP {resp.status_code}: {url}",
                                       category="network", code="download_failed")
        return resp.content

    def cleanup(self):
        self.client.close()
