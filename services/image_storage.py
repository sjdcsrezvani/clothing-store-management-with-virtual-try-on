"""High-quality storage compression for person and garment photos.

Generation keeps using the 1024px pipeline in ``services.image_gen``; this
module only shrinks the copy kept on disk. 1600px q90 4:4:4 is visually
lossless and still above the model's 1024px input.
"""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

MAX_STORAGE_DIM = 1600
JPEG_QUALITY = 90
FAST_PATH_BYTES = 1_000_000

_FORMAT_BY_SUFFIX = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


def compress_for_storage(raw: bytes, suffix: str) -> tuple[bytes, str]:
    """Return ``(bytes, suffix)`` for the stored copy.

    Small images are returned untouched. Anything unreadable is returned
    untouched too: losing an upload is worse than keeping it large.
    """
    suffix = (suffix or ".jpg").lower()
    fmt = _FORMAT_BY_SUFFIX.get(suffix)
    if not raw or fmt is None:
        return raw, suffix
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            width, height = probe.size
            icc = probe.info.get("icc_profile")
            if max(width, height) <= MAX_STORAGE_DIM and len(raw) <= FAST_PATH_BYTES:
                return raw, suffix
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if fmt == "JPEG":
                image = image.convert("RGB")
            elif fmt == "PNG" and image.mode == "P" and "transparency" in image.info:
                image = image.convert("RGBA")
            elif fmt == "WEBP" and image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            image.thumbnail((MAX_STORAGE_DIM, MAX_STORAGE_DIM), Image.LANCZOS)
            buffer = io.BytesIO()
            options = {"optimize": True}
            if icc:
                options["icc_profile"] = icc
            if fmt == "JPEG":
                image.save(buffer, "JPEG", quality=JPEG_QUALITY, subsampling=0, **options)
            elif fmt == "PNG":
                image.save(buffer, "PNG", **options)
            else:
                image.save(buffer, "WEBP", quality=JPEG_QUALITY, method=6, **options)
            data = buffer.getvalue()
        if len(data) >= len(raw):
            return raw, suffix
        return data, suffix
    except Exception:
        return raw, suffix


def save_upload_image(raw: bytes, original_filename: str, upload_dir: Path,
                      url_prefix: str) -> str:
    """Compress an uploaded product image and return its public URL."""
    suffix = Path(original_filename or "").suffix.lower() or ".jpg"
    data, suffix = compress_for_storage(raw, suffix)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{suffix}"
    (upload_dir / filename).write_bytes(data)
    return f"{url_prefix.rstrip('/')}/{filename}"
