"""Per-shop room presets for Instagram Studio.

Backgrounds are text-only and lazily seeded, so a new install gets four
editable studio sets without a migration. The key is stored on
`InstagramGeneration.background_key`, and old generations keep their rendered
prompt even if a background is later edited or deactivated.
"""
from __future__ import annotations

import re
import uuid

from services.instagram_prompts import BACKGROUNDS, ROOM_BACKGROUND_SCENES

MIN_PROMPT_CHARS = 80
MAX_LABEL_CHARS = 120
MAX_DESCRIPTION_CHARS = 240
_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")

PRODUCT_SCENARIOS = ("garment", "product_brief")
MODEL_SCENARIO = "model_scene"

STARTER_BACKGROUNDS = (
    {
        "key": "starter_ivory",
        "label_fa": "استودیو کرم",
        "description_fa": "دیوار کرم، کف چوب روشن، نور نرم پنجره",
        "prompt_en": (
            "Walls: warm ivory limewash plaster with a subtle hand-troweled texture.\n"
            "Floor: pale oak with a matte finish.\n"
            "Baseboard: slim matte-black baseboard.\n"
            "Set piece: a low pale oak display table at the back of the set.\n"
            "Lighting: soft north-facing window daylight from camera left; gentle "
            "natural shadows; no direct sun, no colored gels, no flash.\n"
            "Lens and color: 50mm lens at f/4, natural perspective, no wide-angle "
            "distortion; warm neutral color grade, low contrast, true-to-life fabric "
            "colors, subtle fine grain.\n"
            "Set rules: clean negative space; no other garments, furniture, props, "
            "text, logos, or watermarks."
        ),
    },
    {
        "key": "starter_white",
        "label_fa": "استودیو سفید",
        "description_fa": "دیوار و کف سفید یکدست، نور نرم استودیویی",
        "prompt_en": (
            "Walls: seamless pure-white studio wall with a smooth matte finish.\n"
            "Floor: seamless white studio floor, clean and matte.\n"
            "Baseboard: seamless white baseboard.\n"
            "Set piece: a low white plinth at the back of the set.\n"
            "Lighting: large softbox from camera left plus gentle fill from camera "
            "right; soft contact shadow; even high-key commercial lighting; no "
            "colored gels.\n"
            "Lens and color: 50mm lens at f/4, natural perspective; neutral white "
            "balance, clean contrast, true-to-life fabric colors, subtle fine grain.\n"
            "Set rules: no visible seams; no other garments, furniture, props, text, "
            "logos, or watermarks."
        ),
    },
    {
        "key": "starter_oak",
        "label_fa": "اتاق چوبی گرم",
        "description_fa": "دیوار گچی و پنل چوب گردو، نور گرم عصر",
        "prompt_en": (
            "Walls: warm off-white plaster with a wide natural-oak panel section.\n"
            "Floor: warm oak planks with a matte finish.\n"
            "Baseboard: natural oak baseboard.\n"
            "Set piece: a low natural-oak display table at the back of the set.\n"
            "Lighting: soft late-afternoon window light from camera right; warm "
            "gentle shadows; no direct sun, no flash.\n"
            "Lens and color: 50mm lens at f/4, natural perspective; warm "
            "amber-neutral color grade, low contrast, true-to-life fabric colors, "
            "subtle fine grain.\n"
            "Set rules: clean negative space; no other garments, furniture, props, "
            "text, logos, or watermarks."
        ),
    },
    {
        "key": "starter_gray",
        "label_fa": "استودیو خاکستری",
        "description_fa": "دیوار خاکستری روشن، کف بتنی، نور پخش",
        "prompt_en": (
            "Walls: light warm-gray seamless studio wall with a subtle matte texture.\n"
            "Floor: pale gray concrete with a soft matte finish.\n"
            "Baseboard: slim gray baseboard.\n"
            "Set piece: a low gray plinth at the back of the set.\n"
            "Lighting: diffused overhead daylight with gentle side falloff; soft "
            "natural shadows; no colored gels, no flash.\n"
            "Lens and color: 50mm lens at f/4, natural perspective; cool-neutral "
            "color grade, low contrast, true-to-life fabric colors, subtle fine grain.\n"
            "Set rules: clean negative space; no other garments, furniture, props, "
            "text, logos, or watermarks."
        ),
    },
)

DEFAULT_STARTER_KEY = STARTER_BACKGROUNDS[0]["key"]


def _model():
    from models import InstagramBackground
    return InstagramBackground


def _ordered(db):
    InstagramBackground = _model()
    return (db.query(InstagramBackground)
            .order_by(InstagramBackground.is_default.desc(),
                      InstagramBackground.sort_order,
                      InstagramBackground.id)
            .all())


def _ensure_default(db) -> bool:
    """Leave exactly one active default; return True when rows changed."""
    rows = _ordered(db)
    active = [row for row in rows if row.is_active]
    defaults = [row for row in active if row.is_default]
    if len(defaults) == 1:
        return False
    for row in rows:
        row.is_default = False
    if active:
        active[0].is_default = True
    return True


def ensure_instagram_backgrounds(db) -> list:
    """Seed the starter pack once and keep one active default."""
    InstagramBackground = _model()
    if db.query(InstagramBackground.id).first() is None:
        for index, item in enumerate(STARTER_BACKGROUNDS):
            db.add(InstagramBackground(
                key=item["key"],
                label_fa=item["label_fa"],
                description_fa=item["description_fa"],
                prompt_en=item["prompt_en"],
                is_active=True,
                is_default=(item["key"] == DEFAULT_STARTER_KEY),
                is_starter=True,
                sort_order=index,
            ))
        db.commit()
    elif _ensure_default(db):
        db.commit()
    return _ordered(db)


def list_backgrounds(db, active_only: bool = True) -> list:
    ensure_instagram_backgrounds(db)
    rows = _ordered(db)
    return [row for row in rows if row.is_active] if active_only else rows


def get_background(db, key: str):
    ensure_instagram_backgrounds(db)
    return (db.query(_model())
            .filter(_model().key == (key or "").strip())
            .first())


def default_background(db):
    ensure_instagram_backgrounds(db)
    return (db.query(_model())
            .filter(_model().is_active.is_(True),
                    _model().is_default.is_(True))
            .first())


def _clean_fields(label_fa, description_fa, prompt_en):
    label = (label_fa or "").strip()
    description = (description_fa or "").strip()
    prompt = (prompt_en or "").strip()
    if not label:
        raise ValueError("عنوان پس‌زمینه الزامی است.")
    if len(label) > MAX_LABEL_CHARS:
        raise ValueError("عنوان پس‌زمینه بیش از حد بلند است.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError("توضیح کوتاه پس‌زمینه بیش از حد بلند است.")
    if len(prompt) < MIN_PROMPT_CHARS:
        raise ValueError("متن انگلیسی پس‌زمینه باید کامل‌تر باشد.")
    return label, description, prompt


def _next_sort_order(db) -> int:
    row = (db.query(_model().sort_order)
           .order_by(_model().sort_order.desc()).first())
    return (row[0] if row else -1) + 1


def save_background(
    db,
    *,
    label_fa: str,
    prompt_en: str,
    description_fa: str = "",
    key: str | None = None,
    is_active: bool = True,
    is_default: bool | None = None,
    sort_order: int | None = None,
    staff_id: int | None = None,
):
    """Create a row or update the one with `key`; validates every field."""
    InstagramBackground = _model()
    label, description, prompt = _clean_fields(label_fa, description_fa, prompt_en)
    if is_default and not is_active:
        raise ValueError("پس‌زمینه پیش‌فرض باید فعال باشد.")
    clean_key = (key or "").strip()
    if clean_key and not _KEY_RE.match(clean_key):
        raise ValueError("کلید پس‌زمینه فقط حروف کوچک انگلیسی، عدد و _ است.")

    row = (db.query(InstagramBackground)
           .filter(InstagramBackground.key == clean_key).first()
           if clean_key else None)

    if row is None:
        if is_active is False and is_default:
            raise ValueError("پس‌زمینه پیش‌فرض باید فعال باشد.")
        row = InstagramBackground(
            key=clean_key or f"bg_{uuid.uuid4().hex[:12]}",
            sort_order=_next_sort_order(db) if sort_order is None else sort_order,
        )
        db.add(row)
    else:
        active_others = (db.query(InstagramBackground)
                         .filter(InstagramBackground.is_active.is_(True),
                                 InstagramBackground.id != row.id)
                         .count())
        if not is_active and not active_others:
            raise ValueError("حداقل یک پس‌زمینه فعال باید بماند.")

    row.label_fa = label
    row.description_fa = description or None
    row.prompt_en = prompt
    row.is_active = bool(is_active)
    row.updated_by_staff_id = staff_id
    if sort_order is not None:
        row.sort_order = sort_order

    if is_default:
        if not row.is_active:
            raise ValueError("پس‌زمینه پیش‌فرض باید فعال باشد.")
        db.flush()
        for other in db.query(InstagramBackground).all():
            other.is_default = other.id == row.id
    elif row.is_default and not row.is_active:
        row.is_default = False

    db.flush()
    _ensure_default(db)
    db.commit()
    return row


def set_default(db, key: str, staff_id: int | None = None):
    row = get_background(db, key)
    if row is None:
        raise ValueError("پس‌زمینه پیدا نشد.")
    if not row.is_active:
        raise ValueError("پس‌زمینه غیرفعال را نمی‌توان پیش‌فرض کرد.")
    for other in db.query(_model()).all():
        other.is_default = other.id == row.id
    row.updated_by_staff_id = staff_id
    db.commit()
    return row


def toggle_active(db, key: str, staff_id: int | None = None):
    row = get_background(db, key)
    if row is None:
        raise ValueError("پس‌زمینه پیدا نشد.")
    if row.is_active:
        active_others = (db.query(_model())
                         .filter(_model().is_active.is_(True),
                                 _model().id != row.id)
                         .count())
        if not active_others:
            raise ValueError("حداقل یک پس‌زمینه فعال باید بماند.")
        row.is_active = False
        row.is_default = False
    else:
        row.is_active = True
    row.updated_by_staff_id = staff_id
    db.flush()
    _ensure_default(db)
    db.commit()
    return row


def duplicate_background(db, key: str, staff_id: int | None = None):
    source = get_background(db, key)
    if source is None:
        raise ValueError("پس‌زمینه پیدا نشد.")

    InstagramBackground = _model()
    base = f"{source.key}_copy"[:40]
    new_key = base
    suffix = 2
    while (db.query(InstagramBackground)
           .filter(InstagramBackground.key == new_key).first()):
        tail = f"_{suffix}"
        new_key = f"{base[:40 - len(tail)]}{tail}"
        suffix += 1

    label = f"{source.label_fa} (کپی)"[:MAX_LABEL_CHARS]
    row = InstagramBackground(
        key=new_key,
        label_fa=label,
        description_fa=source.description_fa,
        prompt_en=source.prompt_en,
        is_active=True,
        is_default=False,
        is_starter=False,
        sort_order=_next_sort_order(db),
        updated_by_staff_id=staff_id,
    )
    db.add(row)
    db.commit()
    return row


def _brand_choice(row) -> dict:
    return {
        "key": row.key,
        "label": row.label_fa,
        "description": row.description_fa or "",
        "prompt_en": row.prompt_en,
        "source": "brand",
        "is_default": bool(row.is_default),
    }


def background_choices_for_scenario(db, scenario: str) -> list[dict]:
    """Brand rows for product tabs; model scenes also keep the generic set."""
    scenario = (scenario or "").strip()
    if scenario in PRODUCT_SCENARIOS:
        return [_brand_choice(row) for row in list_backgrounds(db)]
    if scenario != MODEL_SCENARIO:
        return []
    choices = [_brand_choice(row) for row in list_backgrounds(db)]
    choices.extend({
        "key": key,
        "label": entry["label"],
        "description": "",
        "clause": entry["clause"],
        "source": "generic",
        "is_default": False,
    } for key, entry in BACKGROUNDS.items())
    return choices


def _resolved_brand(row) -> dict | None:
    if row is None:
        return None
    return {
        "key": row.key,
        "label": row.label_fa,
        "description": row.description_fa or "",
        "clause": row.prompt_en,
        "prompt_en": row.prompt_en,
        "source": "brand",
    }


def _fallback_brand(db) -> dict | None:
    return _resolved_brand(default_background(db))


def resolve_generation_background(
    db,
    *,
    scenario: str,
    scene_key: str,
    background_key: str,
) -> dict | None:
    """The one background a generation may use, or None when not applicable."""
    scenario = (scenario or "").strip()
    scene_key = (scene_key or "").strip()
    background_key = (background_key or "").strip()

    if scenario in PRODUCT_SCENARIOS:
        if scenario == "garment" and scene_key not in ROOM_BACKGROUND_SCENES:
            return None
        if scenario == "product_brief" and scene_key and scene_key not in ROOM_BACKGROUND_SCENES:
            return None
        row = get_background(db, background_key) if background_key else None
        if row is None or not row.is_active:
            return _fallback_brand(db)
        return _resolved_brand(row)

    if scenario == MODEL_SCENARIO:
        row = get_background(db, background_key) if background_key else None
        if row is not None and row.is_active:
            return _resolved_brand(row)
        generic = BACKGROUNDS.get(background_key)
        if generic:
            return {
                "key": background_key,
                "label": generic["label"],
                "description": "",
                "clause": generic["clause"],
                "prompt_en": "",
                "source": "generic",
            }
        return _fallback_brand(db)

    return None


def model_lighting_is_from_background(background: dict | None) -> bool:
    return bool(background and background.get("source") == "brand")
