"""Virtual try-on image generation via the gapgpt /v1/images/edits gateway.

Kid photo + garment photo(s) are sent as separate multipart files to
gpt-image-2. Prompt building and input-image compression are kept here;
anything provider-specific was removed when we dropped the multi-provider chain.
"""
import base64
import io
import logging
from pathlib import Path
from typing import Optional


import httpx
from PIL import Image, ImageOps

from config import TRYON_API_KEY, TRYON_API_URL

logger = logging.getLogger(__name__)

MAX_DIM = 1024
# S24 Ultra native JPEGs are routinely 8–25 MB; raise the ceiling so the
# preprocessor never has to resize (which the user explicitly wants to avoid).
MAX_BYTES = 25_000_000

# Path to the brand logo used for in-app compositing on generated try-on images.
# Resolved at import-time; falls back to None if missing so callers can no-op.
LOGO_PATH = Path("static/logo.png")
_LOGO_CACHE: Optional[Image.Image] = None


def _load_logo() -> Optional[Image.Image]:
    """Lazy-load the brand logo once. Returns None if not present or unreadable."""
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    if not LOGO_PATH.exists():
        return None
    try:
        _LOGO_CACHE = Image.open(LOGO_PATH)
        _LOGO_CACHE.load()
        return _LOGO_CACHE
    except Exception as e:
        logger.warning("Could not load brand logo at %s: %s", LOGO_PATH, e)
        return None


def composite_logo(image_bytes: bytes, logo_width: int = 230) -> bytes:
    """Return PNG bytes of `image_bytes` with the brand logo pinned to the
    top-left corner, scaled to `logo_width` pixels wide (aspect preserved).
    Logo is added via PIL only — no AI involved. Falls back to a plain PNG
    re-encode if the logo is missing."""
    base = Image.open(io.BytesIO(image_bytes))
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    logo = _load_logo()
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


class ImageGenerationError(Exception):
    pass


class ImageGenService:
    """Generate images of kids wearing clothes via the gapgpt gateway."""

    # Don't retry on billable failures. gpt-image-2 charges at generation time
    # regardless of whether the response reaches us, so retrying on a read timeout
    # or 5xx means paying 2–3× for the same result. Only retry on connect/write
    # errors where the model definitely never received the request.
    MAX_ATTEMPTS = 1

    def __init__(self):
        # 300s read timeout. gpt-image-2 normally finishes in 30–90s, but when
        # gapgpt's queue is busy a generation can take 2–4 minutes. Cutting the
        # socket earlier (we tried 120s) caused the worst outcome: the provider
        # still finishes, bills, and produces the image — but the response has
        # nowhere to go, so the user pays and gets nothing. The image/generation
        # is billed the moment it starts, so we must keep the connection open
        # long enough for the result to actually arrive; only give up past the
        # slowest realistic queue time.
        self.client = httpx.Client(timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0))

    # ── Public entry point ──

    def generate(
        self,
        prompt: str,
        reference_image_paths: Optional[list[str]] = None,
        width: int = 1024,
        height: int = 1024,
        prompt_mid: str = "",
        prompt_suffix: str = "",
    ) -> bytes:
        """Return raw result-image bytes. reference_image_paths: person photo first, then garments.
        Only retries on connect/write errors where the model definitely never received
        the request (avoiding double-billing on a paid generation API).
        Assembly order: `prompt` + MAIN_PROMPT + `prompt_mid` (background/pose/details,
        which must override MAIN_PROMPT's generic defaults) + `prompt_suffix` (the
        outfit rule, which must land at the very end for recency bias)."""
        paths = [p for p in (reference_image_paths or []) if p and Path(p).exists()]
        if len(paths) < 2:
            raise ImageGenerationError("Need at least a person photo and one garment photo.")

        kid = self._prep(paths[0])
        files = [("image[]", ("person.jpg", kid, "image/jpeg"))]
        for i, garment in enumerate(paths[1:], 1):
            files.append(("image[]", (f"garment{i}.jpg", self._prep(garment), "image/jpeg")))

        # Matches the working virtual-tryon.html example request.
        data = {
            "model": "gpt-image-2",
            "quality": "low",
            "output_format": "jpeg",
            "prompt": self._build_prompt(prompt, prompt_mid, prompt_suffix),
            "size": f"{width}x{height}",
        }
        headers = {"Authorization": f"Bearer {TRYON_API_KEY}"}

        logger.info("gapgpt try-on: kid + %d garment(s)", len(paths) - 1)

        last_err: Optional[Exception] = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = self.client.post(TRYON_API_URL, headers=headers, data=data, files=files)
            except httpx.ConnectTimeout as e:
                # TCP connect timed out — gapgpt never received the request.
                # Safe to retry once.
                last_err = e
                logger.warning("gapgpt attempt %d/%d connect timeout: %s", attempt, self.MAX_ATTEMPTS, e)
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                continue
            except httpx.ConnectError as e:
                # DNS failure, connection refused — request never reached gapgpt.
                # Safe to retry once.
                last_err = e
                logger.warning("gapgpt attempt %d/%d connect error: %s", attempt, self.MAX_ATTEMPTS, e)
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                continue
            except httpx.WriteTimeout as e:
                # Request body upload timed out — gapgpt may have received partial
                # data but the model didn't start. Retry once.
                last_err = e
                logger.warning("gapgpt attempt %d/%d write timeout: %s", attempt, self.MAX_ATTEMPTS, e)
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep_backoff(attempt)
                continue
            except httpx.ReadTimeout as e:
                # Response didn't arrive in time, but the model may have already
                # generated and billed the image. Do NOT retry — just fail.
                raise ImageGenerationError(
                    f"gapgpt read timeout — generation may have been billed: {e}"
                ) from e
            except httpx.HTTPError as e:
                # Unexpected transport error — assume gapgpt received the request.
                # Do NOT retry to avoid double-billing.
                raise ImageGenerationError(f"gapgpt transport error: {e}") from e

            if resp.status_code == 200:
                body = resp.json()
                usage = body.get("usage")
                if usage:
                    logger.info("gapgpt usage: %s", usage)
                else:
                    logger.info("gapgpt response keys: %s", list(body.keys()))
                return self._extract_image(body)

            # 4xx means we sent something invalid — fail fast.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise ImageGenerationError(f"gapgpt HTTP {resp.status_code}: {resp.text[:300]}")

            # 5xx and 429 — gapgpt received the request but couldn't serve it.
            # The model may have already generated and billed. Do NOT retry.
            raise ImageGenerationError(
                f"gapgpt HTTP {resp.status_code} — request received, not retrying (avoids double-billing): {resp.text[:300]}"
            )

        raise ImageGenerationError(
            f"gapgpt failed after {self.MAX_ATTEMPTS} attempts: {last_err}"
        )

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        """Exponential backoff: 1s, 2s, 4s, 8s … capped at 10s."""
        import time
        delay = min(2 ** (attempt - 1), 10)
        time.sleep(delay)

    # ── Prompt ──

    def _build_prompt(self, base: str, mid: str = "", suffix: str = "") -> str:
        """Assemble the final prompt in recency-weighted order:
           `base` (product intro) + MAIN_PROMPT + `mid` (background/pose/details)
           + `suffix` (outfit-mode rule, kept last so it overrides everything via
           recency bias)."""
        base = (base or "").strip().rstrip(".")
        out = f"{base}. {MAIN_PROMPT}" if base else MAIN_PROMPT
        mid = (mid or "").strip()
        if mid:
            out += f" {mid}"
        suffix = (suffix or "").strip()
        if suffix:
            out += f" {suffix}"
        return out

    # ── Image preprocessing ──

    def _prep(self, path: str, max_bytes: int = MAX_BYTES) -> bytes:
        """Resize longest edge to MAX_DIM, EXIF-fix, flatten alpha, JPEG-encode to <=max_bytes.
        Smaller input = faster upload + faster model. Resize preserves all garment/face detail
        since 1024px is well above what the model actually consumes."""
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

    def _extract_image(self, data: dict) -> bytes:
        """Pull the first result image (b64 or url) out of the gateway JSON response.
        Fetches the URL variant via the same httpx client so we don't lose an image
        that gapgpt returned as a CDN URL instead of inline base64."""
        for key in ("data", "images"):
            for item in data.get(key) or []:
                raw = item.get("b64_json") or item.get("url")
                if not raw:
                    continue
                if raw.startswith("http"):
                    return self._fetch_url(raw)
                if raw.startswith("data:"):
                    raw = raw.split(",", 1)[1]
                return base64.b64decode(raw)
        raise ImageGenerationError("No image in provider response")

    def _fetch_url(self, url: str) -> bytes:
        """Download the image at `url` (returned by gapgpt when it stores the
        result on its CDN instead of inlining base64). 60s ceiling — these are
        small CDN objects, anything longer means the host is dead."""
        try:
            resp = self.client.get(url, timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0))
        except httpx.HTTPError as e:
            raise ImageGenerationError(f"Failed to fetch result URL: {url} ({e})") from e
        if resp.status_code != 200:
            raise ImageGenerationError(f"Result URL returned HTTP {resp.status_code}: {url}")
        return resp.content

    def cleanup(self):
        self.client.close()