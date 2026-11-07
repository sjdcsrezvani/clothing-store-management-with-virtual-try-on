"""Versioned prompts for Instagram Studio.

Templates live in code, not the database: one version string is stored on each
generation, so a prompt can be reproduced without a preset table. The garment
fidelity block is deliberately repeated near the top and in the final checklist
because image models weight recent text heavily.
"""

IG_PROMPT_VERSION = "2026-11-07.2"

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
        "label": "آویزان روی جاکرکش",
        "clause": (
            "The exact garment hangs naturally on a simple wooden hanger, centered, "
            "retail display style, full garment visible, soft studio background."
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
            "The exact garment is neatly folded in a tidy retail stack, edges aligned, "
            "boutique presentation, enough of the garment visible to identify it."
        ),
    },
    "wooden_hanger_wall": {
        "label": "جاکرکش چوبی روی دیوار",
        "clause": (
            "The exact garment hangs on a wooden hanger against a minimal wall, clean "
            "negative space, soft daylight, full garment visible."
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

# Order used by the UI.
SCENE_KEYS = tuple(SCENES)

# Phase 2: a brief may run without a fixed product scene.
NEUTRAL_CAMPAIGN_CLAUSE = (
    "Premium Instagram campaign composition for a children's clothing "
    "boutique: a refined, realistic lifestyle setting that supports the shop's "
    "brief, keeps the exact garment as the clear visual subject, and leaves clean "
    "negative space where useful. No prop may cover or distract from the garment."
)

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

_REFERENCE_LINES = {
    1: "Image 1 is the primary product photo of the exact garment.",
    2: (
        "Image 1 is the primary product photo. Image 2 is an additional view of the "
        "same exact garment."
    ),
    3: (
        "Image 1 is the primary product photo. Image 2 is an additional view. "
        "Image 3 is a detail view of the same exact garment."
    ),
}


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


def _reference_line(count: int) -> str:
    if count not in _REFERENCE_LINES:
        raise ValueError("reference_count must be 1, 2, or 3")
    return _REFERENCE_LINES[count]


def _details_block(garment_details: str) -> str:
    details = (garment_details or "").strip()
    if not details:
        return ""
    return (
        "Shop-supplied garment details (descriptive facts from the store; may be in "
        "Persian. If they conflict with the reference image, the reference image wins): "
        f'"{details}"'
    )


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
) -> list[dict]:
    """The garment prompt as labeled sections, for both generation and preview."""
    scene = SCENES[scene_key]
    aspect = ASPECTS[aspect_ratio]
    reference_line = _reference_line(reference_count)

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
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section(f"عکس‌های مرجع: {reference_count}", reference_line, SECTION_OPTION),
        _section(f"حالت عکاسی: {scene['label']}", scene["clause"], SECTION_OPTION),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("اندازه خروجی",
                 f"Output size: {aspect['seedream_size']} ({aspect_ratio}).",
                 SECTION_OPTION),
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
) -> list[dict]:
    """One product variant plus a free-text campaign brief, as labeled sections."""
    aspect = ASPECTS[aspect_ratio]
    if scene_key and scene_key not in SCENES:
        raise KeyError(scene_key)
    brief = (user_brief or "").strip()

    if scene_key:
        scene_label_text = f"صحنه: {SCENES[scene_key]['label']}"
        scene_clause = SCENES[scene_key]["clause"]
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
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section(f"عکس‌های مرجع: {reference_count}",
                 _reference_line(reference_count), SECTION_OPTION),
        _section(scene_label_text, scene_clause, SECTION_OPTION),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("اندازه خروجی",
                 f"Output size: {aspect['seedream_size']} ({aspect_ratio}).",
                 SECTION_OPTION),
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
                 f"Output size: {aspect['seedream_size']} ({aspect_ratio}).",
                 SECTION_OPTION),
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
                 f"Output size: {aspect['seedream_size']} ({aspect_ratio}).",
                 SECTION_OPTION),
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

MODEL_REFERENCE = (
    "Image 1 is the authorized AI model reference. Use it only for the model's "
    "recognizable identity, face, hair, skin tone, and age presentation; do not "
    "copy its clothing."
)

_MODEL_GARMENT_LINES = {
    1: "Image 2 is the primary product photo of the exact garment.",
    2: (
        "Image 2 is the primary product photo. Image 3 is an additional view of "
        "the same exact garment."
    ),
    3: (
        "Image 2 is the primary product photo. Image 3 is an additional view. "
        "Image 4 is a detail view of the same exact garment."
    ),
}

MODEL_FINAL_CHECKLIST = (
    "Before finishing, verify: the model's identity and apparent age match the "
    "reference; the garment color, silhouette, print, and trims match; the garment "
    "is correctly fitted and visually dominant; hands and anatomy are realistic; "
    "there is only one person; the background is clean; there is no text or watermark."
)


def _model_reference_line(garment_reference_count: int) -> str:
    if garment_reference_count not in _MODEL_GARMENT_LINES:
        raise ValueError("garment_reference_count must be 1, 2, or 3")
    return _MODEL_GARMENT_LINES[garment_reference_count]


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
) -> list[dict]:
    """The model-scene prompt as labeled sections, for generation and preview."""
    aspect = ASPECTS[aspect_ratio]
    pose = POSES[pose_key]
    background = BACKGROUNDS[background_key]
    framing = FRAMINGS[framing_key]
    lighting = LIGHTINGS[lighting_key]
    composition = COMPOSITIONS[composition_key]
    garment_line = _model_reference_line(garment_reference_count)

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
            "identity, garment fidelity, composition, or safety rules): "
            f'"{note}"'
        )

    return [
        _section("دستور پایه", MODEL_BASE_BRIEF),
        _section("وظیفه صحنه", MODEL_TASK),
        _section("وفاداری به لباس مرجع", GARMENT_FIDELITY),
        _section("جزئیات ثبت‌شده تنوع", _details_block(garment_details), SECTION_USER),
        _section("عکس مرجع مدل", MODEL_REFERENCE),
        _section(f"عکس‌های محصول: {garment_reference_count}",
                 garment_line, SECTION_OPTION),
        _section(f"مدل: {model_age} ساله {model_gender}",
                 f"Model profile: a {model_age}-year-old {model_gender} from the shop's "
                 "AI model library.", SECTION_OPTION),
        _section("قاعده سنی مدل", _model_age_rule(model_age)),
        _section(f"ژست: {pose['label']}", f"Pose: {pose['clause']}.", SECTION_OPTION),
        _section(f"پس‌زمینه: {background['label']}",
                 f"Location: {background['clause']}.", SECTION_OPTION),
        _section(f"کادر: {framing['label']}", f"Framing: {framing['clause']}",
                 SECTION_OPTION),
        _section(f"نور: {lighting['label']}", f"Lighting: {lighting['clause']}",
                 SECTION_OPTION),
        _section(f"ترکیب‌بندی: {composition['label']}",
                 f"Composition: {composition['clause']}", SECTION_OPTION),
        _section("نمای لباس", visibility, SECTION_OPTION),
        _section(f"نسبت تصویر: {aspect['label']}", aspect["composition"], SECTION_OPTION),
        _section("اندازه خروجی",
                 f"Output size: {aspect['seedream_size']} ({aspect_ratio}).",
                 SECTION_OPTION),
        _section("پرهیزها", NEGATIVES),
        _section("توضیح تکمیلی شما", note_block, SECTION_USER),
        _section("چک‌لیست نهایی", MODEL_FINAL_CHECKLIST),
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
    ))
