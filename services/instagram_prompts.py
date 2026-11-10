"""Versioned prompts for Instagram Studio.

Templates live in code, not the database: one version string is stored on each
generation, so a prompt can be reproduced without a preset table. The garment
fidelity block is deliberately repeated near the top and in the final checklist
because image models weight recent text heavily.
"""
from services.image_slots import (
    MODE_GARMENT,
    image_mode_for_family,
    reference_views_for,
)
from services.instagram_product_types import (
    FAMILY_LABELS_FA,
    FAMILY_PROMPT_CLAUSES,
    FEET,
    HANDS,
    HEADWEAR,
    LAYERED,
    LOWER,
    ONE_PIECE,
    OUTER,
    SET,
    SOCKS,
    UPPER,
)

IG_PROMPT_VERSION = "2026-11-08.1"

SCENARIO_GARMENT = "garment"
SCENARIO_PRODUCT_BRIEF = "product_brief"
SCENARIO_MODEL_SCENE = "model_scene"
SCENARIO_TEXT = "text"
SCENARIO_TEXT_IMAGE = "text_image"
SCENARIOS = {
    SCENARIO_GARMENT: "عکاسی محصول",
    SCENARIO_PRODUCT_BRIEF: "کمپین با توضیح",
    SCENARIO_MODEL_SCENE: "مدل و ژست",
    SCENARIO_TEXT: "کمپین متنی",
    SCENARIO_TEXT_IMAGE: "کمپین متنی با عکس",
}
SCENARIO_KEYS = tuple(SCENARIOS)

BASE_BRIEF = (
    "Professional commercial fashion product photograph for a children's clothing "
    "boutique's Instagram. Photorealistic, high-end catalog quality, accurate fabric "
    "behavior, clean composition, soft flattering light, sharp focus on the product. "
    "No watermark, no text, no extra logos. The presentation must be family-friendly "
    "and age-appropriate."
)

GARMENT_FIDELITY = (
    "Treat the supplied product reference image(s) as the only source of truth for "
    "the garment. Reproduce the exact same garment, not a redesigned interpretation. "
    "Preserve its exact visible color, silhouette, proportions, cut, neckline, sleeves, "
    "hem, seams, closures, pockets, print, embroidery, trims, and placement of details. "
    "Do not add, remove, recolor, simplify, embellish, or invent garment features. "
    "Do not substitute a similar garment. Do not add logos, labels, text, accessories, "
    "or extra pieces unless they are explicitly visible in the references. If a detail "
    "is not visible, do not invent it. The garment must remain the clear visual subject."
)

NEGATIVES = (
    "Avoid: altered garment design or color; invented seams, buttons, pockets, prints, "
    "labels, or logos; missing garment details; extra garments; duplicated limbs; "
    "distorted anatomy; floating garment; impossible fabric physics; cluttered "
    "background; props covering the product; cropped garment edges in full-product "
    "shots; text; watermark; signature; fake brand marks; plastic skin; cartoon or CGI look."
)

FINAL_CHECKLIST = (
    "Before finishing, verify: garment color matches the reference; silhouette and "
    "proportions match; print and embroidery placement match; no invented details; the "
    "full garment is visible; the background is clean; there is no text or watermark; "
    "fabric and anatomy are realistic."
)

SECTION_FIXED = "fixed"
SECTION_OPTION = "option"
SECTION_USER = "user"


def _section(label: str, text: str, kind: str = SECTION_FIXED) -> dict:
    return {"label": label, "text": text, "kind": kind}


def _join_sections(sections: list[dict], separator: str = " ") -> str:
    return separator.join(section["text"] for section in sections if section["text"])

# key -> (Persian label, English staging clause)
SCENES = {
    "hanger": {
        "label": "آویزان روی رگال",
        "clause": (
            "The exact garment hangs on the shop's fixed signature hanger: a natural "
            "oak hanger with a centered brushed-brass hook, on a slim matte-black rail "
            "fixed to the wall. The hanger, rail, and camera angle must be identical in "
            "every image; do not change the hanger style, rail position, or angle. "
            "Centered, full garment visible, straight-on."
        ),
    },
    "flat_lay": {
        "label": "چیدمان تخت",
        "clause": (
            "The exact garment is arranged neatly flat on a clean neutral surface, "
            "photographed from directly above (top-down), balanced composition, "
            "full garment visible."
        ),
    },
    "ghost_mannequin": {
        "label": "مانکن نامرئی",
        "clause": (
            "The exact garment is presented as a ghost mannequin / invisible mannequin: "
            "a hollow, naturally shaped 3D garment with no visible person, body, skin, "
            "head, or mannequin, studio e-commerce style, full garment visible."
        ),
    },
    "detail_closeup": {
        "label": "نمای نزدیک جزئیات",
        "clause": (
            "Macro close-up of the garment's construction details: seams, stitching, "
            "buttons, zipper, cuffs, hem, or trims. Sharp focus, shallow depth of field, "
            "do not invent details that are not visible."
        ),
    },
    "fabric_texture": {
        "label": "نمای نزدیک بافت پارچه",
        "clause": (
            "Extreme close-up of the visible fabric texture and weave/knit, soft raking "
            "side light, sharp focus, do not invent fiber composition."
        ),
    },
    "folded_stack": {
        "label": "تاشده فروشگاهی",
        "clause": (
            "The exact garment is neatly folded on the set's clean display surface, "
            "edges aligned, boutique presentation, enough of the garment visible to "
            "identify it. Do not add props."
        ),
    },
    "wooden_hanger_wall": {
        "label": "رگال چوبی روی دیوار",
        "clause": (
            "The exact garment hangs on the same natural oak hanger with a centered "
            "brushed-brass hook, on the same slim matte-black rail, viewed closer "
            "against the wall with clean negative space. The hanger, rail, and camera "
            "angle must be identical in every image; do not change them."
        ),
    },
    "packshot": {
        "label": "پکشات یکدست",
        "clause": (
            "Centered e-commerce packshot on a seamless pure white or soft gray "
            "background, soft contact shadow, full garment visible, no props."
        ),
    },
    "back_view": {
        "label": "نمای پشت/پهلو",
        "clause": (
            "The back of the exact garment is emphasized, showing the back silhouette, "
            "seams, and any visible back details. Use the supplied additional/back "
            "reference if present. Full garment visible."
        ),
    },
    "soft_shadow_studio": {
        "label": "نور و سایه نرم استودیویی",
        "clause": (
            "Professional studio product shot with soft directional light and a gentle "
            "natural contact shadow, clean seamless background, full garment visible, "
            "premium commercial look."
        ),
    },
}

# Fixed photo slots each scene actually needs. Main is always included by the
# studio, so it is listed explicitly to keep the rule readable.
SCENE_REFERENCE_VIEWS = {key: ("main",) for key in SCENES}
SCENE_REFERENCE_VIEWS.update({
    "detail_closeup": ("main", "detail"),
    "fabric_texture": ("main", "detail"),
    "back_view": ("main", "back"),
})

# Order used by the UI.
SCENE_KEYS = tuple(SCENES)

# These scenes take their room/wall/floor from the selected brand background.
ROOM_BACKGROUND_SCENES = ("hanger", "wooden_hanger_wall", "folded_stack")

_HANGER_LOCK = (
    "The hanger, rail, and camera angle must be identical in every image; do not "
    "change the hanger style, rail position, or angle."
)


def _hanger_clause(scene_key: str, family: str | None) -> str:
    """The hanger scene adapted to the product's physical staging family."""
    if scene_key not in ("hanger", "wooden_hanger_wall"):
        return SCENES[scene_key]["clause"]
    if family == LOWER:
        base = (
            "The exact lower-body garment hangs on the shop's fixed slim matte-black "
            "clip hanger: the waistband is held evenly by two clips and the legs hang "
            "straight and fully visible, on the same slim matte-black rail fixed to "
            "the wall."
        )
    elif family == SET:
        base = (
            "The exact two-piece set hangs on the shop's fixed rail: the upper-body "
            "piece on the natural oak shoulder hanger with a centered brushed-brass "
            "hook, and the matching lower-body piece on a slim matte-black clip hanger "
            "directly below on the same rail, waistband clipped evenly, legs hanging "
            "straight. Both pieces are fully visible as one coordinated set."
        )
    elif family == LAYERED:
        base = (
            "The exact layered outfit hangs on the shop's fixed signature hanger: the "
            "under-layer top and the outer layer worn together exactly as in the "
            "reference, on a natural oak hanger with a centered brushed-brass hook, on "
            "a slim matte-black rail fixed to the wall. Both layers remain visible; do "
            "not remove the under-layer or change the layering."
        )
    elif family == ONE_PIECE:
        base = (
            "The exact one-piece garment hangs on the shop's fixed signature shoulder "
            "hanger: a natural oak hanger with a centered brushed-brass hook, on a slim "
            "matte-black rail fixed to the wall. The piece stays one piece; do not "
            "split it into a top and bottom or add a separate bottom."
        )
    else:
        return SCENES[scene_key]["clause"]
    camera = ("viewed closer against the wall with clean negative space"
              if scene_key == "wooden_hanger_wall"
              else "centered, straight-on")
    return f"{base} {camera}. {_HANGER_LOCK}"

# Repeated verbatim for every brand background so the set does not drift.
BACKGROUND_LOCK = (
    "Use the shop's fixed studio set. Keep the wall, floor, lighting direction, "
    "lens, and color grade identical in every image; do not redesign, recolor, "
    "or rearrange the set."
)

# Phase 2: a brief may run without a fixed product scene.
NEUTRAL_CAMPAIGN_CLAUSE = (
    "Premium Instagram campaign composition for a children's clothing "
    "boutique: a refined, realistic lifestyle setting that supports the shop's "
    "brief, keeps the exact garment as the clear visual subject, and leaves clean "
    "negative space where useful. No prop may cover or distract from the garment."
)

# `seedream_size` and `gpt_size` are the paid API request sizes; `target` is the
# exact local crop. GPT models never receive the larger Seedream size.
ASPECTS = {
    "4:5": {
        "label": "۴:۵ (فید اینستاگرام)",
        "seedream_size": "1440x1800",
        "target": (1440, 1800),
        "gpt_size": "1024x1536",
        "composition": (
            "Vertical 4:5 Instagram feed composition. Keep the full garment inside the "
            "frame with generous margins on all sides. Do not crop off important garment edges."
        ),
    },
    "1:1": {
        "label": "۱:۱ (مربع)",
        "seedream_size": "1440x1440",
        "target": (1440, 1440),
        "gpt_size": "1024x1024",
        "composition": (
            "Square 1:1 Instagram composition. Keep the full garment inside the frame "
            "with generous margins. Do not crop off important garment edges."
        ),
    },
    "9:16": {
        "label": "۹:۱۶ (استوری/ریلز)",
        "seedream_size": "1440x2560",
        "target": (1440, 2560),
        "gpt_size": "1024x1536",
        "composition": (
            "Vertical 9:16 story/reel cover composition. Keep the full garment inside "
            "the safe area with generous margins. Do not crop off important garment edges."
        ),
    },
}

ASPECT_KEYS = tuple(ASPECTS)

# ── Phase 3: model photoshoot libraries ─────────────────────────────────────
# Vetted, family-friendly poses and locations. Only these keys reach the model;
# free text never selects a location, so a child profile cannot be sent to an
# adult setting.

POSES = {
    "standing_front": {
        "label": "ایستاده روبه‌دوربین",
        "clause": "standing front-facing in a relaxed natural stance, shoulders "
                  "relaxed, hands at the sides, full body visible",
    },
    "three_quarter": {
        "label": "سه‌چهارم",
        "clause": "standing in a gentle three-quarter turn toward the camera, "
                  "relaxed shoulders, full body visible",
    },
    "side_profile": {
        "label": "نیم‌رخ",
        "clause": "standing in a natural side profile so the garment silhouette "
                  "reads clearly, full body visible",
    },
    "back_view": {
        "label": "نمای پشت",
        "clause": "standing with the back to the camera to show the garment's "
                  "back, full body visible",
    },
    "walking_stance": {
        "label": "قدم رو به جلو",
        "clause": "standing with one foot slightly forward in a natural walking "
                  "stance, relaxed arms, full body visible",
    },
    "hands_relaxed": {
        "label": "دست‌های آزاد",
        "clause": "standing relaxed with hands naturally at the sides and the "
                  "front of the garment unobstructed, full body visible",
    },
    "seated_bench": {
        "label": "نشسته روی نیمکت",
        "clause": "sitting naturally on a simple bench, posture relaxed, full "
                  "garment visible",
    },
    "seated_block": {
        "label": "نشسته روی سکو",
        "clause": "sitting naturally on a clean studio block, posture relaxed, "
                  "full garment visible",
    },
    "seated_casual": {
        "label": "نشسته راحت",
        "clause": "sitting casually with the garment fully visible and unobstructed",
    },
    "gentle_walk": {
        "label": "قدم زدن آرام",
        "clause": "walking gently toward the camera in a natural, age-appropriate "
                  "stride, garment visible",
    },
    "small_turn": {
        "label": "چرخش کوچک",
        "clause": "making a small natural turn, caught mid-motion, garment visible",
    },
    "playful": {
        "label": "حرکت کودکانه",
        "clause": "light, playful, age-appropriate movement, natural and modest, "
                  "garment visible",
    },
    "hands_away": {
        "label": "دست‌ها کنار لباس",
        "clause": "hands gently away from the garment to reveal its shape, arms relaxed",
    },
    "touch_sleeve": {
        "label": "لمس آستین یا جیب",
        "clause": "one hand lightly touching a sleeve or pocket, the other arm "
                  "relaxed, garment unobstructed",
    },
}

# Model scenes never send the detail slot; only a back-facing pose needs back.
POSE_REFERENCE_VIEWS = {key: ("main",) for key in POSES}
POSE_REFERENCE_VIEWS["back_view"] = ("main", "back")

BACKGROUNDS = {
    "studio_white": {
        "label": "استودیو سفید",
        "clause": "a clean pure-white seamless studio backdrop with a soft contact shadow",
    },
    "studio_beige": {
        "label": "استودیو بژ",
        "clause": "a warm off-white beige seamless studio backdrop with soft natural light",
    },
    "studio_gray": {
        "label": "استودیو خاکستری",
        "clause": "a light-gray seamless studio backdrop with a subtle gradient",
    },
    "studio_pastel": {
        "label": "استودیو پاستلی",
        "clause": "a soft pastel seamless studio backdrop, calm and family-friendly",
    },
    "studio_color": {
        "label": "استودیو هم‌رنگ لباس",
        "clause": "a seamless studio backdrop in a muted tone that complements the "
                  "garment color without matching it exactly",
    },
    "studio_editorial": {
        "label": "استودیو ادیتوریال",
        "clause": "a minimal editorial studio with subtle shadows and clean negative space",
    },
    "city_quiet": {
        "label": "خیابان آرام",
        "clause": "a quiet city street with a softly blurred background, clean "
                  "pavement, family-friendly",
    },
    "pedestrian": {
        "label": "پیاده‌رو تمیز",
        "clause": "a clean pedestrian street with soft daylight and an uncluttered background",
    },
    "park": {
        "label": "پارک",
        "clause": "a green park with soft natural light and a gently blurred background",
    },
    "garden_walkway": {
        "label": "باغ و گل",
        "clause": "a garden walkway with flowers and soft daylight, uncluttered and "
                  "family-friendly",
    },
    "beach": {
        "label": "ساحل",
        "clause": "a clean beach promenade with soft daylight and a calm background, "
                  "seasonally appropriate",
    },
    "architecture": {
        "label": "معماری مدرن",
        "clause": "a modern architectural wall or courtyard with clean lines and soft daylight",
    },
    "cafe_family": {
        "label": "کافه خانوادگی",
        "clause": "a bright family-friendly café interior with soft daylight and an "
                  "uncluttered background",
    },
    "home_bright": {
        "label": "خانه روشن",
        "clause": "a bright, tidy home interior with soft natural light and simple furnishings",
    },
    "playroom": {
        "label": "اتاق بازی",
        "clause": "a cheerful, tidy children's playroom with soft daylight and no "
                  "distracting clutter",
    },
    "bookshop": {
        "label": "کتاب‌فروشی",
        "clause": "a cozy bookshop interior with warm light and softly blurred shelves",
    },
    "apartment": {
        "label": "آپارتمان مدرن",
        "clause": "a minimal modern apartment interior with clean lines and soft natural light",
    },
    "spring_garden": {
        "label": "باغ بهاری",
        "clause": "a spring garden with fresh greenery and soft natural light",
    },
    "autumn_street": {
        "label": "خیابان پاییزی",
        "clause": "an autumn street with warm foliage and soft daylight",
    },
    "winter_city": {
        "label": "شهر زمستانی",
        "clause": "a clean winter city scene with cool soft light and a calm background",
    },
    "summer_outdoor": {
        "label": "فضای باز تابستان",
        "clause": "a bright summer outdoor setting with soft daylight and an "
                  "uncluttered background",
    },
    "festive": {
        "label": "فصل جشن",
        "clause": "a festive but uncluttered seasonal set with soft warm light",
    },
}

FRAMINGS = {
    "full_body": {
        "label": "تمام‌قد",
        "clause": "Full-body framing from head to toe, with the entire garment "
                  "visible and generous margins.",
    },
    "three_quarter_body": {
        "label": "سه‌چهارم قد",
        "clause": "Three-quarter-body framing from mid-thigh up, with the garment "
                  "as the clear subject.",
    },
    "waist_up": {
        "label": "نیم‌تنه",
        "clause": "Waist-up framing, with the upper garment fully visible and the "
                  "lower body naturally cropped.",
    },
    "detail_crop": {
        "label": "نمای نزدیک جزئیات",
        "clause": "Tight detail crop on the garment's fabric, print, or "
                  "construction, with sharp focus and no invented details.",
    },
}

LIGHTINGS = {
    "soft_daylight": {
        "label": "نور نرم روز",
        "clause": "Soft natural daylight with gentle shadows and balanced exposure.",
    },
    "bright_studio": {
        "label": "استودیو روشن",
        "clause": "Bright, even studio lighting with a clean high-key look.",
    },
    "softbox_editorial": {
        "label": "سافت‌باکس ادیتوریال",
        "clause": "Large softbox editorial lighting with soft directional shadows "
                  "and a premium commercial look.",
    },
    "golden_hour": {
        "label": "نور طلایی غروب",
        "clause": "Warm golden-hour light with a soft glow and natural shadows.",
    },
}

COMPOSITIONS = {
    "centered_catalog": {
        "label": "مرکزی کاتالوگ",
        "clause": "Centered catalog composition with the model as the clear subject "
                  "and balanced negative space.",
    },
    "editorial_negative_space": {
        "label": "ادیتوریال با فضای منفی",
        "clause": "Editorial off-center composition with intentional negative "
                  "space, keeping the model and garment as the focus.",
    },
    "candid_lifestyle": {
        "label": "لایف‌استایل طبیعی",
        "clause": "Candid lifestyle composition that feels natural and unposed "
                  "while keeping the garment clearly visible.",
    },
}

POSE_KEYS = tuple(POSES)
BACKGROUND_KEYS = tuple(BACKGROUNDS)
FRAMING_KEYS = tuple(FRAMINGS)
LIGHTING_KEYS = tuple(LIGHTINGS)
COMPOSITION_KEYS = tuple(COMPOSITIONS)

def _reference_subject(family: str | None) -> str:
    return "garment" if image_mode_for_family(family) == MODE_GARMENT else "product"


def _chosen_views(count: int, family: str | None,
                  views: list[str] | None) -> list[str]:
    """The real selected slots, or the first `count` slots for count-only callers."""
    allowed = reference_views_for(family)
    chosen = list(views) if views is not None else allowed[:count]
    if not chosen or len(chosen) > len(allowed) or any(
            view not in allowed for view in chosen):
        raise ValueError("reference views must be 1..{0}".format(len(allowed)))
    return chosen


def _reference_line(count: int, family: str | None = None,
                    views: list[str] | None = None) -> str:
    """Name only the photos that were actually selected.

    The caller passes the real slots, so a variant with main + detail but no
    back photo never gets a back-view line. Count-only callers keep the old
    "first N slots" behavior.
    """
    chosen = _chosen_views(count, family, views)
    subject = _reference_subject(family)
    lines = [f"Image 1 is the {chosen[0]} of the exact {subject}."]
    for index, view in enumerate(chosen[1:], start=2):
        lines.append(f"Image {index} is the {view} of the same exact {subject}.")
    return " ".join(lines)


def scene_label(key: str) -> str:
    return SCENES[key]["label"]


def aspect_label(key: str) -> str:
    return ASPECTS[key]["label"]


def pose_label(key: str) -> str:
    return POSES[key]["label"]


def background_label(key: str) -> str:
    return BACKGROUNDS[key]["label"]


def framing_label(key: str) -> str:
    return FRAMINGS[key]["label"]


def lighting_label(key: str) -> str:
    return LIGHTINGS[key]["label"]


def composition_label(key: str) -> str:
    return COMPOSITIONS[key]["label"]


def _details_block(garment_details: str) -> str:
    details = (garment_details or "").strip()
    if not details:
        return ""
    return (
        "Shop-supplied garment details (descriptive facts from the store; may be in "
        "Persian. If they conflict with the reference image, the reference image wins): "
        f'"{details}"'
    )


def _family_section(garment_family: str | None) -> dict | None:
    """One labeled section naming the garment type and what must stay intact."""
    clause = FAMILY_PROMPT_CLAUSES.get(garment_family)
    if not clause:
        return None
    label = FAMILY_LABELS_FA.get(garment_family, garment_family)
    return _section(f"نوع محصول: {label}", f"Garment type ({label}): {clause}", SECTION_OPTION)


def _is_brand_background(background: dict | None) -> bool:
    return bool(background and (background.get("source") == "brand"
                                or background.get("prompt_en")))


def _background_section(background: dict | None, *,
                        generic_prefix: bool = False) -> dict | None:
    """One labeled section for a brand background or a generic model location.

    For brand rows the raw owner prompt lives in `clause`/`prompt_en`; the lock
    sentence is added here so every prompt path gets it exactly once.
    """
    if not background:
        return None
    label = (background.get("label") or background.get("label_fa") or "").strip()
    clause = (background.get("clause") or background.get("prompt_en") or "").strip()
    if not clause:
        return None
    if _is_brand_background(background):
        text = f"{BACKGROUND_LOCK} {clause}"
    elif generic_prefix:
        text = f"Location: {clause}."
    else:
        text = clause
    return _section(f"پس‌زمینه: {label}", text, SECTION_OPTION)


# `product_name` and `variant_color` stay accepted for callers but are never
# written into a prompt: the reference image shows the colour, and the shop's
# product name adds no visual information.
def garment_prompt_sections(
    *,
    product_name: str = "",
    variant_color: str = "",
    scene_key: str,
    aspect_ratio: str,
    reference_count: int,
    user_note: str = "",
    garment_details: str = "",
    background: dict | None = None,
    garment_family: str | None = None,
    reference_views: list[str] | None = None,
) -> list[dict]:
    """The garment prompt as labeled sections, for both generation and preview."""
    scene = SCENES[scene_key]
    scene_clause = _hanger_clause(scene_key, garment_family)
    aspect = ASPECTS[aspect_ratio]
    if reference_views is not None:
        reference_count = len(reference_views)
    reference_line = _reference_line(reference_count, garment_family, reference_views)
    family_section = _family_section(garment_family)
    background_section = (_background_section(background)
                          if scene_key in ROOM_BACKGROUND_SCENES else None)

    note = (user_note or "").strip()
    note_block = ""
    if note:
        note_block = (
            "Additional creative direction from the shop (must not override garment "
            f'fidelity, composition, or safety rules): "{note}"'
        )

    return [
        _section("دستور پایه", BASE_BRIEF),
        _section("وفاداری به لباس مرجع", GARMENT_FIDELITY),
        *([family_section] if family_section else []),
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section(f"عکس‌های مرجع: {reference_count}", reference_line, SECTION_OPTION),
        _section(f"حالت عکاسی: {scene['label']}", scene_clause, SECTION_OPTION),
        *([background_section] if background_section else []),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("پرهیزها", NEGATIVES),
        _section("توضیح تکمیلی شما", note_block, SECTION_USER),
        _section("چک‌لیست نهایی", FINAL_CHECKLIST),
    ]


def build_garment_prompt(
    *,
    product_name: str = "",
    variant_color: str = "",
    scene_key: str,
    aspect_ratio: str,
    reference_count: int,
    user_note: str = "",
    garment_details: str = "",
    background: dict | None = None,
    garment_family: str | None = None,
    reference_views: list[str] | None = None,
) -> str:
    """Assemble the final English prompt for one garment generation.

    Raises KeyError for an unknown scene/aspect and ValueError for a reference
    count outside 1..3. The route validates first; these are the last guard.
    """
    return _join_sections(garment_prompt_sections(
        scene_key=scene_key,
        aspect_ratio=aspect_ratio,
        reference_count=reference_count,
        user_note=user_note,
        garment_details=garment_details,
        background=background,
        garment_family=garment_family,
        reference_views=reference_views,
    ))


def product_brief_prompt_sections(
    *,
    product_name: str = "",
    variant_color: str = "",
    scene_key: str,
    aspect_ratio: str,
    reference_count: int,
    user_brief: str,
    garment_details: str = "",
    background: dict | None = None,
    garment_family: str | None = None,
    reference_views: list[str] | None = None,
) -> list[dict]:
    """One product variant plus a free-text campaign brief, as labeled sections."""
    aspect = ASPECTS[aspect_ratio]
    if reference_views is not None:
        reference_count = len(reference_views)
    if scene_key and scene_key not in SCENES:
        raise KeyError(scene_key)
    brief = (user_brief or "").strip()
    use_background = not scene_key or scene_key in ROOM_BACKGROUND_SCENES
    background_section = _background_section(background) if use_background else None
    family_section = _family_section(garment_family)

    if scene_key:
        scene_label_text = f"صحنه: {SCENES[scene_key]['label']}"
        scene_clause = _hanger_clause(scene_key, garment_family)
    else:
        scene_label_text = "صحنه: بدون صحنه — کمپین آزاد"
        scene_clause = NEUTRAL_CAMPAIGN_CLAUSE
    brief_block = (
        "Creative campaign brief from the shop (must not override garment fidelity, "
        f'composition, or safety rules): "{brief}"'
    )

    return [
        _section("دستور پایه", BASE_BRIEF),
        _section("وفاداری به لباس مرجع", GARMENT_FIDELITY),
        *([family_section] if family_section else []),
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section(f"عکس‌های مرجع: {reference_count}",
                 _reference_line(reference_count, garment_family, reference_views),
                 SECTION_OPTION),
        _section(scene_label_text, scene_clause, SECTION_OPTION),
        *([background_section] if background_section else []),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("توضیح کمپین شما", brief_block, SECTION_USER),
        _section("پرهیزها", NEGATIVES),
        _section("چک‌لیست نهایی", FINAL_CHECKLIST),
    ]


def build_product_brief_prompt(
    *,
    product_name: str = "",
    variant_color: str = "",
    scene_key: str,
    aspect_ratio: str,
    reference_count: int,
    user_brief: str,
    garment_details: str = "",
    background: dict | None = None,
    garment_family: str | None = None,
    reference_views: list[str] | None = None,
) -> str:
    """Phase 2 prompt: one product variant plus a free-text campaign brief.

    `scene_key` is optional; an empty value uses the neutral campaign clause.
    Raises KeyError for an unknown scene/aspect and ValueError for an empty
    brief or a reference count outside 1..3.
    """
    brief = (user_brief or "").strip()
    if not brief:
        raise ValueError("user_brief is required")
    return _join_sections(product_brief_prompt_sections(
        scene_key=scene_key,
        aspect_ratio=aspect_ratio,
        reference_count=reference_count,
        user_brief=brief,
        garment_details=garment_details,
        background=background,
        garment_family=garment_family,
        reference_views=reference_views,
    ))


TEXT_PROMPT_HEAD = (
    "Create an original commercial Instagram fashion campaign image based on this "
    "creative brief:"
)

TEXT_PROMPT_RULES = (
    "Produce a refined, realistic fashion photograph with intentional composition, "
    "natural materials, professional lighting, and a clear visual focal point. Leave "
    "clean negative space if the composition is intended to receive text later. Do not "
    "include brand logos, watermarks, fake product labels, or garbled text. Do not add "
    "unsupported product claims or unrelated objects."
)

TEXT_PROMPT_TEMPLATE = (
    TEXT_PROMPT_HEAD + '\n"{brief}"\n\n' + TEXT_PROMPT_RULES
)


def text_prompt_sections(*, user_text: str, aspect_ratio: str) -> list[dict]:
    """Reference-free campaign prompt, as labeled sections."""
    aspect = ASPECTS[aspect_ratio]
    brief = (user_text or "").strip()
    return [
        _section("دستور پایه", TEXT_PROMPT_HEAD),
        _section("متن کمپین شما", f'"{brief}"' if brief else "", SECTION_USER),
        _section(f"نسبت تصویر: {aspect['label']}",
                 f"Final delivery ratio: {aspect_ratio}.", SECTION_OPTION),
        _section("قواعد تصویر", TEXT_PROMPT_RULES),
    ]


def build_text_prompt(*, user_text: str, aspect_ratio: str) -> str:
    """Phase 4 prompt: a campaign image from text and no references.

    Raises KeyError for an unknown aspect and ValueError for an empty brief.
    """
    ASPECTS[aspect_ratio]
    brief = (user_text or "").strip()
    if not brief:
        raise ValueError("user_text is required")
    return _join_sections(text_prompt_sections(user_text=brief,
                                               aspect_ratio=aspect_ratio))


TEXT_IMAGE_PROMPT_HEAD = (
    "Edit the supplied reference image into an original commercial Instagram fashion "
    "campaign image based on this creative brief:"
)

TEXT_IMAGE_REFERENCE = (
    "Use the supplied reference image as the visual source. Preserve the recognizable "
    "subject, product, or composition unless the brief explicitly asks for a change. "
    "Do not invent a different subject or replace the reference with a similar one."
)

TEXT_IMAGE_PROMPT_TEMPLATE = (
    TEXT_IMAGE_PROMPT_HEAD + '\n"{brief}"\n\n' + TEXT_IMAGE_REFERENCE + " "
    + TEXT_PROMPT_RULES
)


def text_image_prompt_sections(*, user_text: str, aspect_ratio: str) -> list[dict]:
    """Campaign prompt built from one uploaded image and free text."""
    aspect = ASPECTS[aspect_ratio]
    brief = (user_text or "").strip()
    return [
        _section("دستور پایه", TEXT_IMAGE_PROMPT_HEAD),
        _section("عکس مرجع شما", TEXT_IMAGE_REFERENCE, SECTION_OPTION),
        _section("متن کمپین شما", f'"{brief}"' if brief else "", SECTION_USER),
        _section(f"نسبت تصویر: {aspect['label']}",
                 f"Final delivery ratio: {aspect_ratio}.", SECTION_OPTION),
        _section("قواعد تصویر", TEXT_PROMPT_RULES),
    ]


def build_text_image_prompt(*, user_text: str, aspect_ratio: str) -> str:
    """Text plus one uploaded reference image; uses the provider's edit endpoint."""
    ASPECTS[aspect_ratio]
    brief = (user_text or "").strip()
    if not brief:
        raise ValueError("user_text is required")
    return _join_sections(text_image_prompt_sections(user_text=brief,
                                                     aspect_ratio=aspect_ratio))


MODEL_BASE_BRIEF = (
    "Professional commercial fashion photoshoot for a children's clothing "
    "boutique's Instagram. Photorealistic, high-end catalog quality, accurate "
    "fabric behavior, natural anatomy, clean composition, soft flattering light, "
    "sharp focus on the garment. No watermark, no text, no extra logos. The "
    "presentation must be family-friendly and age-appropriate."
)

MODEL_TASK = (
    "Create a professional commercial fashion photoshoot featuring the supplied "
    "model reference and the exact supplied garment reference. Preserve the "
    "model's recognizable visual identity from the authorized reference image, "
    "while keeping the depiction natural and age-appropriate. Dress the model in "
    "the exact garment shown in the product reference. Do not add text, logos, "
    "watermarks, or extra people."
)

MODEL_IDENTITY = (
    "Image 1 is the authorized AI model reference. Use it ONLY for the model's "
    "identity: face structure, facial features, skin tone and undertones, hair "
    "color/texture/hairline, body shape/build, and apparent age. Ignore ALL clothing, "
    "shoes, accessories, pose, and background in Image 1; they are not part of the "
    "output and must never be copied. Preserve the model's face structure exactly: "
    "same face shape, eye shape/spacing/size, nose, lip shape/fullness, brow shape, "
    "jawline, chin, ear shape, hairline, skin tone and undertones. Preserve body "
    "shape, build, and apparent age unchanged."
)

MODEL_FACE_RULE = (
    "FACE RULE: preserve the model's face EXACTLY as in the reference photo — same "
    "facial structure, same expression, same skin texture, same look. NO beautification, "
    "no retouching, no skin-smoothing, no makeup, no expression change, no aging. The "
    "model must still look like the same person."
)

MODEL_GARMENT_FIDELITY = (
    "For the target garment, the supplied product reference image(s) are the only "
    "source of truth. Reproduce the exact same garment, not a redesigned interpretation. "
    "Preserve its exact visible color, silhouette, proportions, cut, neckline, sleeves, "
    "hem, seams, closures, pockets, print, embroidery, trims, and placement of details. "
    "A sweatshirt stays a sweatshirt (not a hoodie); a long-sleeve tee stays long-sleeve; "
    "a one-piece stays one-piece; a crew-neck stays crew-neck. Do not add, remove, "
    "recolor, simplify, embellish, or invent features of the target garment. Do not "
    "substitute a similar garment. Do not add logos, labels, or text to the target "
    "garment. The target garment must remain the clear visual subject. Other garments "
    "may be added ONLY as the outfit rule below requires; they must be plain, coordinated, "
    "clearly different from the target, and must never compete with or copy it."
)

MODEL_NEGATIVES = (
    "Avoid: altered target-garment design or color; invented target-garment seams, "
    "buttons, pockets, prints, labels, or logos; missing target-garment details; a second "
    "copy of the target garment; matching clone outfits made from the target's fabric or "
    "print; bare legs, underwear, or a missing lower body when the target is an "
    "upper-body garment; missing shoes; an underdressed model; extra unrelated garments "
    "that distract from the target; duplicated limbs; distorted anatomy; floating "
    "garment; impossible fabric physics; cluttered background; props covering the "
    "product; text; watermark; signature; fake brand marks; plastic skin; cartoon or CGI look."
)

MODEL_EDIT_REMINDER = (
    "Return a new edited photograph: the model must be wearing the exact target garment "
    "and a complete coordinated outfit; never return the model reference unchanged."
)

_OUTFIT_SCOPE = (
    "This rule controls clothing only; keep the selected pose, framing, background, "
    "and lighting unchanged."
)

MODEL_OUTFIT_UPPER = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target garment is a single "
    "UPPER-BODY piece (top / t-shirt / shirt / sweatshirt / hoodie / sweater). Reproduce "
    "it exactly. Build a full coordinated outfit around it. The bottom (pants / shorts / "
    "skirt) must ALWAYS be present and must NEVER match the target's fabric, color, or "
    "print; it is always a different category, different fabric, and different color. "
    "T-shirt → jeans, chino shorts, or linen pants. Sweatshirt → jeans, chinos, or cargo "
    "pants (never sweatpants). Hoodie → jeans or joggers (never matching hoodie-fabric "
    "pants). Add shoes that complement the bottom — clean sneakers with jeans, sandals or "
    "canvas shoes with shorts. No recolored clones, no matching-sweatsuit looks. Never "
    "leave the lower body bare: no underwear-only, no missing bottom, no missing shoes. "
    "Choose age-appropriate, modest clothing for the model's age. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_LOWER = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target garment is a single "
    "LOWER-BODY piece (jeans / pants / shorts / skirt). Reproduce its category, cut, "
    "length, fit, color, fabric, wash, and print exactly. Build a full coordinated outfit "
    "around it. The top must ALWAYS be present and must NEVER match the bottom's fabric or "
    "print, and must not echo its color head-to-toe (no denim-on-denim, no matching "
    "tracksuit). The top is always a different category, different fabric, and different "
    "color. Jeans → white or neutral t-shirt, henley, or plain shirt. Chinos/cargo → plain "
    "t-shirt or pullover sweater. Shorts → t-shirt or polo. Skirt → soft blouse or plain "
    "t-shirt. Add shoes that complement the look — clean sneakers with jeans, sandals or "
    "canvas shoes with shorts, ballet flats with skirts. Only one bottom on the model — "
    "never two bottoms. Choose age-appropriate, modest clothing. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_SET = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a complete two-piece "
    "SET — top + bottom together. Reproduce the ENTIRE set exactly as shown — same top, "
    "same bottom, same fabric, same color, same print, same proportions. Do NOT add another "
    "top or another bottom, do NOT swap the set's bottom for jeans or anything else, and "
    "do NOT add a piece made from the set's fabric. The ONLY thing you may change is the "
    "SHOES: pick clean sneakers, canvas shoes, or sandals that complement the set. Choose "
    "age-appropriate footwear. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_LAYERED = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a layered outfit — an "
    "OUTER upper piece (jacket / hoodie / cardigan / coat) over a visible INNER piece "
    "(t-shirt / shirt). Reproduce BOTH upper pieces exactly — the outer's cut, fabric, "
    "print and color, and the inner layer exactly as visible at the neckline, cuffs, and "
    "hem. Build a full coordinated outfit around them. The bottom must NEVER match either "
    "upper layer's fabric or color; it is always a different category, different fabric, "
    "and different color. Hoodie + tee → jeans or chinos (never matching-print sweatpants). "
    "Jacket + tee → slim jeans, chinos, or cargo. Add shoes that fit the look — clean "
    "sneakers or boots. Never drop the inner layer, never fuse the two layers into one "
    "garment, never add a third upper layer, never add a matching tracksuit. Choose "
    "age-appropriate, modest clothing. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_OUTER = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is an outer layer (jacket "
    "/ coat / cardigan). Reproduce it exactly — same front opening, collar, cuffs, pockets, "
    "hem, fabric, print, and color. If the product photos show an inner layer, keep it "
    "exactly as visible. If no inner layer is visible, add ONE plain neutral inner top "
    "(t-shirt or shirt) that is clearly different from the outer's fabric, color, and "
    "print; never leave the torso bare under the jacket. Add a coordinated bottom and shoes "
    "that never match the outer's fabric or print — jeans or chinos with a casual jacket, "
    "tailored trousers with a coat. Do not add a matching set, do not add a second outer "
    "layer, and do not cover the target with another garment. Choose age-appropriate, "
    "modest clothing. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_ONE_PIECE = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a ONE-PIECE garment "
    "(dress / romper / jumpsuit / overall / pinafore), and may include the visible "
    "under-layer shown in the product photos. Reproduce the whole piece exactly — shape, "
    "cut, straps, sleeves, length, print, and color — and keep any visible under-layer "
    "exactly as shown. It must stay ONE piece on the model: NEVER split it into a separate "
    "top and bottom, NEVER invent pants beneath it, NEVER add a t-shirt or shorts that the "
    "product photos do not show. Add shoes that suit the style — ballet flats, clean "
    "sneakers, or sandals. If the legs need covering, use simple neutral tights or "
    "leggings — never the garment's own fabric or print. Choose age-appropriate, modest "
    "styling. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_HEADWEAR = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is headwear. Place the "
    "exact hat on the model's head — same crown, brim, silhouette, angle, fabric, print, "
    "and color. Build a complete, modest, age-appropriate outfit around it: a simple top, "
    "a coordinated bottom, and shoes. The outfit must never match the hat's fabric or "
    "print and must not hide or compete with the hat. Do not add a second hat. "
    + _OUTFIT_SCOPE
)

MODEL_OUTFIT_HANDS = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a pair of gloves. Both "
    "gloves must be worn on the correct hands, matching left/right, with the exact cuffs, "
    "seams, and prints visible. Build a complete, modest, age-appropriate outfit around "
    "them: top, bottom, and shoes. Sleeves must end before the glove cuffs or be pushed up "
    "so the gloves stay clearly visible; do not hide the gloves in pockets or behind the "
    "body. The outfit must never match the gloves' fabric or print. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_FEET = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a pair of shoes. Both "
    "shoes must be worn on the correct feet, matching left/right, with the exact toe shape, "
    "sole, laces, straps, and side profile visible. Build a complete, modest, "
    "age-appropriate outfit around them: top, bottom, and socks if appropriate. The "
    "bottom's hem must not cover or hide the shoes; choose shorts, a skirt, cropped pants, "
    "or a length that keeps both shoes clearly visible. The outfit must never match the "
    "shoes' fabric or print. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_SOCKS = (
    "OUTFIT RULE (this is critical — do NOT ignore): The target is a pair of socks. Both "
    "socks must be worn, matching left/right, with the exact cuff, heel, toe, and pattern "
    "visible. Build a complete, modest, age-appropriate outfit around them. Choose shorts, "
    "a skirt, cropped pants, or a hem length that reveals the socks; shoes may be worn but "
    "must not hide the sock cuff, heel, or pattern. The outfit must never match the socks' "
    "fabric or print. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_GENERIC = (
    "OUTFIT RULE (this is critical — do NOT ignore): Reproduce the exact target product "
    "from the product photos and place it naturally on the model (worn, held, or carried "
    "as appropriate). Build a simple, complete, modest, age-appropriate outfit around it: "
    "top, bottom, and shoes. The added clothing must be plain and coordinated, must never "
    "match or copy the target's fabric, print, or color, and must never hide or compete "
    "with the target. Never leave the lower body bare and never leave the model "
    "underdressed. " + _OUTFIT_SCOPE
)

MODEL_OUTFIT_RULES = {
    UPPER: MODEL_OUTFIT_UPPER,
    LOWER: MODEL_OUTFIT_LOWER,
    SET: MODEL_OUTFIT_SET,
    LAYERED: MODEL_OUTFIT_LAYERED,
    OUTER: MODEL_OUTFIT_OUTER,
    ONE_PIECE: MODEL_OUTFIT_ONE_PIECE,
    HEADWEAR: MODEL_OUTFIT_HEADWEAR,
    HANDS: MODEL_OUTFIT_HANDS,
    FEET: MODEL_OUTFIT_FEET,
    SOCKS: MODEL_OUTFIT_SOCKS,
}

MODEL_OUTFIT_LABELS_FA = {
    UPPER: "بالاتنه — پایین‌تنه و کفش اضافه شود",
    LOWER: "پایین‌تنه — بالاتنه و کفش اضافه شود",
    SET: "ست دو تکه — فقط کفش هماهنگ",
    LAYERED: "ست لایه‌دار — پایین‌تنه و کفش",
    OUTER: "کاپشن/ژاکت — تی‌شرت زیر، پایین‌تنه و کفش",
    ONE_PIECE: "یکسره — بدون شلوار، کفش/جوراب‌شلواری",
}


def outfit_rule(family: str | None) -> str:
    return MODEL_OUTFIT_RULES.get(family, MODEL_OUTFIT_GENERIC)


def _model_reference_line(garment_reference_count: int,
                          garment_family: str | None = None,
                          views: list[str] | None = None) -> str:
    """The product images start at Image 2; absent slots get no line."""
    chosen = _chosen_views(garment_reference_count, garment_family, views)
    subject = _reference_subject(garment_family)
    lines = []
    for index, view in enumerate(chosen):
        lines.append(f"Image {index + 2} is the {view} of the exact {subject}.")
    return " ".join(lines)

MODEL_FINAL_CHECKLIST = (
    "Before finishing, verify: the model's identity, face structure, expression, and "
    "apparent age match the reference; the face is not beautified, retouched, made up, or "
    "aged; the target garment color, silhouette, print, and trims match; the target garment "
    "is correctly fitted and visually dominant; the outfit is complete and modest — for a "
    "top-only product a coordinated bottom and shoes are present, with no bare legs or "
    "underwear; added pieces coordinate with the target and never match its fabric or "
    "print; there is only one person; hands and anatomy are realistic; the background is "
    "clean; there is no text or watermark."
)


def _model_age_rule(age: int) -> str:
    if age <= 7:
        return (
            f"The model is a young child (about {age} years old). Use natural child "
            "proportions and a calm, playful, family-friendly commercial tone. "
            "No makeup, no adult styling, no suggestive posing, and no body-focused framing."
        )
    if age <= 13:
        return (
            f"The model is a teenager (about {age} years old). Use age-appropriate "
            "styling and a natural, modest, family-friendly pose. No adult fashion "
            "posing, no suggestive framing."
        )
    return (
        f"The model is an adult (about {age} years old). Keep the styling "
        "age-appropriate and commercial; the depiction must remain family-friendly "
        "and non-suggestive, clearly separate from the child and teen profiles."
    )


def model_prompt_sections(
    *,
    product_name: str = "",
    variant_color: str = "",
    model_age: int,
    model_gender: str,
    pose_key: str,
    background_key: str,
    framing_key: str,
    lighting_key: str,
    composition_key: str,
    aspect_ratio: str,
    garment_reference_count: int,
    user_note: str = "",
    garment_details: str = "",
    show_full_garment: bool = True,
    background: dict | None = None,
    garment_family: str | None = None,
    outfit_family: str | None = None,
    reference_views: list[str] | None = None,
) -> list[dict]:
    """The model-scene prompt as labeled sections, for generation and preview."""
    aspect = ASPECTS[aspect_ratio]
    pose = POSES[pose_key]
    framing = FRAMINGS[framing_key]
    composition = COMPOSITIONS[composition_key]
    if reference_views is not None:
        garment_reference_count = len(reference_views)
    garment_line = _model_reference_line(garment_reference_count, garment_family,
                                         reference_views)
    prompt_family = outfit_family if outfit_family is not None else garment_family
    family_section = _family_section(prompt_family)
    brand_background = _is_brand_background(background)
    if brand_background:
        background_section = _background_section(background)
        lighting_sections = []  # the brand background owns lighting and lens
    else:
        location = BACKGROUNDS[background_key]
        lighting = LIGHTINGS[lighting_key]
        background_section = _section(
            f"پس‌زمینه: {location['label']}",
            f"Location: {location['clause']}.", SECTION_OPTION)
        lighting_sections = [
            _section(f"نور: {lighting['label']}",
                     f"Lighting: {lighting['clause']}", SECTION_OPTION)
        ]

    if framing_key == "detail_crop":
        visibility = (
            "This is a designated detail shot: frame the selected garment detail "
            "tightly while keeping fabric and construction accurate."
        )
    elif show_full_garment and framing_key in ("full_body", "three_quarter_body"):
        visibility = (
            "The full garment must remain visible; do not crop off important garment edges."
        )
    else:
        visibility = (
            "Keep the garment as visible as the chosen framing allows; do not crop "
            "off important details."
        )

    note = (user_note or "").strip()
    note_block = ""
    if note:
        note_block = (
            "Additional creative direction from the shop (must not override model "
            "identity, face fidelity, garment fidelity, outfit completeness, composition, "
            f'or safety rules): "{note}"'
        )

    return [
        _section("دستور پایه", MODEL_BASE_BRIEF),
        _section("وظیفه صحنه", MODEL_TASK),
        _section("وفاداری به لباس مرجع", MODEL_GARMENT_FIDELITY),
        *([family_section] if family_section else []),
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section("هویت مدل از عکس مرجع", MODEL_IDENTITY),
        _section(f"عکس‌های محصول: {garment_reference_count}",
                 garment_line, SECTION_OPTION),
        _section(f"مدل: {model_age} ساله {model_gender}",
                 f"Model profile: a {model_age}-year-old {model_gender} from the shop's "
                 "AI model library.", SECTION_OPTION),
        _section("قاعده سنی مدل", _model_age_rule(model_age)),
        _section(f"ژست: {pose['label']}", f"Pose: {pose['clause']}.", SECTION_OPTION),
        *([background_section] if background_section else []),
        _section(f"کادر: {framing['label']}", f"Framing: {framing['clause']}",
                 SECTION_OPTION),
        *lighting_sections,
        _section(f"ترکیب‌بندی: {composition['label']}",
                 f"Composition: {composition['clause']}", SECTION_OPTION),
        _section("نمای لباس", visibility, SECTION_OPTION),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("قاعده چهره", MODEL_FACE_RULE),
        _section("پرهیزها", MODEL_NEGATIVES),
        _section("توضیح تکمیلی شما", note_block, SECTION_USER),
        _section("چک‌لیست نهایی", MODEL_FINAL_CHECKLIST),
        _section("قاعده ست کامل", outfit_rule(prompt_family), SECTION_OPTION),
        _section("یادآوری ویرایش", MODEL_EDIT_REMINDER),
    ]


def build_model_prompt(
    *,
    product_name: str = "",
    variant_color: str = "",
    model_age: int,
    model_gender: str,
    pose_key: str,
    background_key: str,
    framing_key: str,
    lighting_key: str,
    composition_key: str,
    aspect_ratio: str,
    garment_reference_count: int,
    user_note: str = "",
    garment_details: str = "",
    show_full_garment: bool = True,
    background: dict | None = None,
    garment_family: str | None = None,
    outfit_family: str | None = None,
    reference_views: list[str] | None = None,
) -> str:
    """Phase 3 prompt: the exact garment on one vetted AI model profile.

    Keys come from the code libraries above; an unknown key raises KeyError so
    a bad UI value can never reach the paid provider. At least one garment
    reference is required; the model reference is always Image 1.
    """
    return _join_sections(model_prompt_sections(
        model_age=model_age,
        model_gender=model_gender,
        pose_key=pose_key,
        background_key=background_key,
        framing_key=framing_key,
        lighting_key=lighting_key,
        composition_key=composition_key,
        aspect_ratio=aspect_ratio,
        garment_reference_count=garment_reference_count,
        user_note=user_note,
        garment_details=garment_details,
        show_full_garment=show_full_garment,
        background=background,
        garment_family=garment_family,
        outfit_family=outfit_family,
        reference_views=reference_views,
    ))
