"""Barcode number and image helpers."""
from __future__ import annotations

import hashlib
import random
import string
from pathlib import Path
from typing import Any

# The auto-generated barcode code length is configurable from the main
# settings page so shops can match their existing numbering scheme.
BARCODE_CODE_LENGTH_SETTING = "barcode_code_length"
BARCODE_CODE_LENGTH_DEFAULT = 5
BARCODE_CODE_LENGTH_MIN = 4
BARCODE_CODE_LENGTH_MAX = 12
BARCODE_DENSITIES = {
    "compact": {"module_width": 0.2, "quiet_zone": 2.5, "module_height": 5},
    "standard": {"module_width": 0.3, "quiet_zone": 3, "module_height": 5},
}
BARCODE_DENSITY_DEFAULT = "compact"


def _clamp_code_length(value: Any) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return BARCODE_CODE_LENGTH_DEFAULT
    if parsed < BARCODE_CODE_LENGTH_MIN:
        return BARCODE_CODE_LENGTH_MIN
    if parsed > BARCODE_CODE_LENGTH_MAX:
        return BARCODE_CODE_LENGTH_MAX
    return parsed


def get_barcode_code_length(db=None) -> int:
    """Return the configured auto-generated code length (4-12 digits)."""
    if db is not None:
        try:
            from models import Settings

            row = (
                db.query(Settings)
                .filter(Settings.key == BARCODE_CODE_LENGTH_SETTING)
                .first()
            )
            if row and row.value:
                return _clamp_code_length(row.value)
        except Exception:
            return BARCODE_CODE_LENGTH_DEFAULT
    return BARCODE_CODE_LENGTH_DEFAULT


def generate_barcode_number(db=None) -> str:
    """Generate the numeric code printed on product tags."""
    length = get_barcode_code_length(db)
    return "".join(random.choices(string.digits, k=length))


_LEGACY_PURGED = False


def _purge_legacy_barcode_cache(directory: Path) -> None:
    """Remove barcode images generated before the bars-only format."""
    global _LEGACY_PURGED
    if _LEGACY_PURGED:
        return
    try:
        for path in directory.glob("barcode_*.png"):
            if not path.name.startswith("barcode_v8_"):
                path.unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        _LEGACY_PURGED = True


def get_barcode_profile(density: str = BARCODE_DENSITY_DEFAULT) -> dict[str, float]:
    """Return a scanner-safe rendering profile for the selected density."""
    return dict(BARCODE_DENSITIES.get(density, BARCODE_DENSITIES[BARCODE_DENSITY_DEFAULT]))


def generate_barcode_image(
    barcode_number: str,
    product_name: str = "",
    density: str = BARCODE_DENSITY_DEFAULT,
) -> str | None:
    """Generate a cached Code 128 bar-strip PNG and return its static URL.

    The image contains only the vertical bars (no human-readable text); the
    tag renderer prints the code digits itself, scaled to the tag design.
    """
    value = str(barcode_number or "").strip()
    if not value:
        return None
    try:
        import barcode
        from barcode.writer import ImageWriter
    except ImportError:
        return None

    directory = Path("static/uploads/barcodes")
    directory.mkdir(parents=True, exist_ok=True)
    _purge_legacy_barcode_cache(directory)
    cache_key = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    if density not in BARCODE_DENSITIES:
        density = BARCODE_DENSITY_DEFAULT
    profile = get_barcode_profile(density)
    profile_key = f"{density}:{profile['module_width']}:{profile['quiet_zone']}:{profile['module_height']}"
    cache_key = hashlib.sha256(f"{value}|{profile_key}".encode("utf-8")).hexdigest()[:24]
    # v8: density is part of the cache key and the writer's text layer is
    # disabled; digits are rendered by the independent barcode_text field.
    target = directory / f"barcode_v8_{cache_key}.png"
    if target.exists() and target.stat().st_size > 0:
        return f"/static/uploads/barcodes/{target.name}"

    try:
        generator = barcode.get("code128", value, writer=ImageWriter())
        saved_path = generator.save(
            str(directory / f"barcode_v8_{cache_key}"),
            options={
                "module_width": profile["module_width"],
                "module_height": profile["module_height"],
                "font_size": 0,
                "text_distance": 0,
                "quiet_zone": profile["quiet_zone"],
            },
        )
        saved = Path(saved_path)
        try:
            from PIL import Image

            image = Image.open(saved)
            # Keep the writer output as-is: with font_size=0 it contains only
            # the Code 128 bars and its quiet/margin zones.
            if image.width < 1 or image.height < 1:
                return None
        except Exception:
            pass
        if saved.exists() and saved.stat().st_size > 0:
            return f"/static/uploads/barcodes/{saved.name}"
    except Exception:
        return None
    return None


def get_or_create_barcode(existing_barcode: str | None = None) -> tuple[str, str | None]:
    """Return an existing code or a newly generated code."""
    if existing_barcode:
        return existing_barcode, None
    return generate_barcode_number(), None
