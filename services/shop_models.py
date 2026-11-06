"""The eight fixed AI model profiles for Instagram Studio Phase 3.

References are AI-generated shop assets, not real people. The seed is lazy so a
new install gets the slots without a migration, and uploads stay in one small
service so the settings route and the studio read the same paths.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image

from services.image_storage import compress_for_storage

MODEL_DIR = Path("static/uploads/shop_models")
MODEL_URL = "/static/uploads/shop_models"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MIN_LONG_EDGE = 512
ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

SHOP_MODEL_SLOTS = (
    ("girl_2", "دختر ۲ ساله"),
    ("girl_7", "دختر ۷ ساله"),
    ("girl_13", "دختر ۱۳ ساله"),
    ("girl_18", "دختر ۱۸ ساله"),
    ("boy_2", "پسر ۲ ساله"),
    ("boy_7", "پسر ۷ ساله"),
    ("boy_13", "پسر ۱۳ ساله"),
    ("boy_18", "پسر ۱۸ ساله"),
)
SLOT_LABELS = dict(SHOP_MODEL_SLOTS)
_SLOT_ORDER = {key: index for index, (key, _label) in enumerate(SHOP_MODEL_SLOTS)}


def ensure_shop_models(db) -> list:
    """Create any missing slots and return all eight in fixed order."""
    from models import ShopModel

    existing = {row.slot_key for row in db.query(ShopModel.slot_key).all()}
    created = False
    for slot_key, label in SHOP_MODEL_SLOTS:
        if slot_key not in existing:
            db.add(ShopModel(slot_key=slot_key, label_fa=label, is_active=True))
            created = True
    if created:
        db.commit()
    rows = db.query(ShopModel).all()
    return sorted(rows, key=lambda row: _SLOT_ORDER.get(row.slot_key, 99))


def model_age(slot_key: str) -> int | None:
    """The slot key carries the age: girl_7 -> 7."""
    try:
        return int((slot_key or "").rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def model_gender(slot_key: str) -> str:
    return "boy" if (slot_key or "").startswith("boy_") else "girl"


def save_model_image(raw: bytes, filename: str = "",
                     content_type: str | None = None) -> str:
    """Validate one uploaded model reference and return its public URL.

    The extension is proved by decoding the bytes, not by trusting the filename.
    Raises ValueError with a Persian message the settings page can show.
    """
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("عکس مدل باید کمتر از ۱۰ مگابایت باشد.")
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("فقط عکس PNG، JPG یا WebP مجاز است.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            fmt = (image.format or "").upper()
            width, height = image.size
    except Exception as error:
        raise ValueError("فایل عکس مدل معتبر نیست.") from error
    if fmt not in ALLOWED_FORMATS:
        raise ValueError("فقط عکس PNG، JPG یا WebP مجاز است.")
    if max(width, height) < MIN_LONG_EDGE:
        raise ValueError("عکس مدل باید حداقل ۵۱۲ پیکسل در ضلع بزرگ‌تر باشد.")

    suffix = ALLOWED_FORMATS[fmt]
    data, suffix = compress_for_storage(raw, suffix)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    name = f"sm_{uuid.uuid4().hex[:12]}{suffix}"
    (MODEL_DIR / name).write_bytes(data)
    return f"{MODEL_URL}/{name}"


def delete_model_image(url: str) -> None:
    """Delete an old upload, but only a direct child of the shop_models root."""
    if not url:
        return
    prefix = MODEL_URL.rstrip("/") + "/"
    if not url.startswith(prefix):
        return
    name = url[len(prefix):]
    if not name or "/" in name or "\\" in name:
        return
    root = MODEL_DIR.resolve()
    path = (MODEL_DIR / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return
    if path == root:
        return
    path.unlink(missing_ok=True)
