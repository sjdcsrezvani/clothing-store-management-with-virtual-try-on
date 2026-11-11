"""Product-family classification for Instagram Studio staging.

The shop's category and garment-type fields are free text, so this maps the
common Persian/English words to the handful of staging families the prompts
need. Unknown products keep every scene and no recommendation.
"""
from __future__ import annotations

import re

UPPER = "upper"
LOWER = "lower"
SET = "set"
LAYERED = "layered"
OUTER = "outer"
ONE_PIECE = "one_piece"
HEADWEAR = "headwear"
HANDS = "hands"
FEET = "feet"
SOCKS = "socks"

CLOTHING_FAMILIES = (UPPER, LOWER, SET, LAYERED, OUTER, ONE_PIECE)
ACCESSORY_FAMILIES = (HEADWEAR, HANDS, FEET, SOCKS)

FAMILY_LABELS_FA = {
    UPPER: "بالاتنه",
    LOWER: "پایین‌تنه",
    SET: "ست",
    LAYERED: "ست لایه‌ای",
    OUTER: "کاپشن/ژاکت",
    ONE_PIECE: "یکسره",
    HEADWEAR: "کلاه",
    HANDS: "دستکش",
    FEET: "کفش",
    SOCKS: "جوراب",
}

# One English clause per family, added to every garment prompt. It tells the
# model what kind of object it is looking at, so a hat is not treated as a
# shirt and a pair of shoes keeps its left and right.
FAMILY_PROMPT_CLAUSES = {
    UPPER: (
        "The product is an upper-body garment. Preserve the exact neckline, sleeve "
        "shape, shoulder line, front opening, and hem visible in the reference."
    ),
    LOWER: (
        "The product is a lower-body garment. Preserve the exact waistband, rise, "
        "leg shape, and hem visible in the reference."
    ),
    SET: (
        "The product is a coordinated two-piece set. Keep both matching pieces "
        "together exactly as shown; never show only one piece or swap them."
    ),
    LAYERED: (
        "The product is a layered outfit. Keep every layer in the same order, "
        "position, and proportion as the reference; never remove or add a layer."
    ),
    OUTER: (
        "The product is an outer layer (jacket or coat). Preserve the exact front "
        "opening, collar, cuffs, pockets, and hem visible in the reference."
    ),
    ONE_PIECE: (
        "The product is a one-piece garment (dress / romper / jumpsuit / overall / "
        "pinafore). Keep it as one piece exactly as shown; never split it into a "
        "separate top and bottom, and never invent pants beneath it. Preserve its exact "
        "neckline, straps, sleeves, length, and hem visible in the reference."
    ),
    HEADWEAR: (
        "The product is headwear. Preserve its exact crown, brim, silhouette, and "
        "surface details; do not stretch or reshape it into a different hat."
    ),
    HANDS: (
        "The product is a pair of gloves. Show both gloves as the matching "
        "left/right pair, with the exact cuffs, seams, and prints visible in the "
        "reference."
    ),
    FEET: (
        "The product is a pair of shoes. Show both shoes as the matching left/right "
        "pair, keeping the exact toe shape, sole, laces, and side profile visible in "
        "the reference."
    ),
    SOCKS: (
        "The product is a pair of socks. Show both socks as the matching pair, "
        "keeping the exact cuff, heel, toe, and pattern visible in the reference."
    ),
}

_ACCESSORY_WORDS = {
    HEADWEAR: ("کلاه", "hat", "cap", "beanie", "beret"),
    HANDS: ("دستکش", "glove", "mitten"),
    FEET: ("کفش", "کتانی", "بوت", "صندل", "چکمه", "دمپایی", "اسلایدر",
           "کالج", "پاپوش", "shoe", "sneaker", "boot", "sandal", "slipper",
           "slide", "loafer", "heel", "footwear"),
    SOCKS: ("جوراب", "sock", "stocking"),
}
_UPPER_WORDS = (
    "بالاتنه", "تیشرت", "تی شرت", "پیراهن", "شومیز", "بلوز", "تاپ",
    "تونیک", "بولرو", "جلیقه", "بادی", "لباس زیر", "مایو",
    "shirt", "t-shirt", "tshirt", "top", "blouse", "vest", "bodysuit",
    "swimsuit", "underwear", "dress",
)
# A generic clothing word alone is not enough to pair with "jacket" or
# "pants"; it only fills the gap when no specific word matched.
_GENERIC_CLOTHING_WORDS = ("لباس", "پوشاک", "clothing")
_OUTER_WORDS = (
    "کاپشن", "ژاکت", "پالتو", "سویشرت", "هودی", "بارانی", "بلیزر", "مانتو",
    "jacket", "coat", "hoodie", "sweater", "cardigan", "raincoat", "blazer",
)
_LOWER_WORDS = (
    "پایین‌تنه", "شلوار", "شلوارک", "شورت", "ساپورت", "لگ", "دامن", "پالازو",
    "pants", "trousers", "shorts", "leggings", "skirt", "palazzo",
)
_SET_TOKENS = ("ست", "set", "دستک")
_ONE_PIECE_WORDS = ("سرهمی", "سرهم", "اورال", "رامپر", "یکسره", "مایو", "ونسی",
                    "dress", "gown", "pinafore", "romper", "jumpsuit", "onesie",
                    "overall")

# The product form's own garment ladder, used only when the free text is unknown.
_SIZE_TYPE_FAMILIES = {"upper": UPPER, "lower": LOWER, "set": SET, "shoes": FEET}

# Accessories cannot hang on a shoulder hanger, fold into a clothing stack, or
# wear a ghost mannequin; product-only scenes are the safe set.
_ACCESSORY_SCENES = ("flat_lay", "packshot", "soft_shadow_studio")
ALLOWED_SCENES = {
    HEADWEAR: _ACCESSORY_SCENES,
    HANDS: _ACCESSORY_SCENES,
    FEET: _ACCESSORY_SCENES,
    SOCKS: _ACCESSORY_SCENES,
}

RECOMMENDED_SCENE = {
    UPPER: "hanger",
    LOWER: "hanger",
    SET: "hanger",
    LAYERED: "hanger",
    OUTER: "hanger",
    ONE_PIECE: "hanger",
    HEADWEAR: "flat_lay",
    HANDS: "flat_lay",
    FEET: "flat_lay",
    SOCKS: "flat_lay",
}

RECOMMENDATION_NOTES_FA = {
    UPPER: "روی رگال شانه‌ای، یقه و آستین‌ها باز و کامل دیده شوند.",
    OUTER: "روی رگال شانه‌ای، زیپ یا دکمه‌ها بسته و یقه مرتب.",
    SET: "بالاتنه روی رگال شانه‌ای و پایین‌تنه روی رگال گیره‌ای.",
    LOWER: "شلوار روی رگال گیره‌ای، با دو گیره روی کمر.",
    LAYERED: "هر دو لایه روی یک رگال، دقیقاً مثل عکس مرجع.",
    ONE_PIECE: "روی رگال شانه‌ای؛ یکسره بماند و بالاتنه/پایین‌تنه از هم جدا نشود.",
    HEADWEAR: "روی سطح تمیز یا پک‌شات؛ رگال و مانکن مناسب نیست.",
    HANDS: "روی سطح تمیز یا پک‌شات؛ رگال و مانکن مناسب نیست.",
    FEET: "روی سطح تمیز یا پک‌شات؛ رگال و مانکن مناسب نیست.",
    SOCKS: "روی سطح تمیز یا پک‌شات؛ رگال و مانکن مناسب نیست.",
}


def _product_text(product) -> str:
    return " ".join(
        str(value or "") for value in (
            getattr(product, "category", ""),
            getattr(product, "garment_type", ""),
            getattr(product, "name", ""),
        )
    ).lower().strip()


def _has(text: str, words: tuple[str, ...]) -> bool:
    parts = set(re.split(r"[\s،,;:/()\-_]+", text))
    for word in words:
        if word.isascii():
            if word in parts:
                return True
        elif word in text:
            return True
    return False


def _has_token(text: str, tokens: tuple[str, ...]) -> bool:
    parts = re.split(r"[\s،,;:/()\-_]+", text)
    return any(part in tokens for part in parts)


def product_family(product) -> str | None:
    """The staging family for a product, or None when the text is unknown."""
    text = _product_text(product)
    if not text:
        return None
    for family, words in _ACCESSORY_WORDS.items():
        if _has(text, words):
            return family
    if _has(text, _ONE_PIECE_WORDS):
        return ONE_PIECE
    has_upper = _has(text, _UPPER_WORDS)
    has_outer = _has(text, _OUTER_WORDS)
    has_lower = _has(text, _LOWER_WORDS)
    if has_upper and has_lower:
        return SET
    if _has_token(text, _SET_TOKENS):
        if not has_lower and has_outer and has_upper:
            return LAYERED
        return SET
    if has_outer and has_upper:
        return LAYERED
    if has_lower:
        return LOWER
    if has_outer:
        return OUTER
    if has_upper:
        return UPPER
    if _has(text, _GENERIC_CLOTHING_WORDS):
        return UPPER
    return _SIZE_TYPE_FAMILIES.get(getattr(product, "size_type", None))


def recommended_scene(family: str | None) -> str | None:
    return RECOMMENDED_SCENE.get(family)


def scene_keys_for_family(family: str | None) -> tuple[str, ...] | None:
    """Allowed scene keys; None means every scene is allowed."""
    return ALLOWED_SCENES.get(family)


def is_scene_allowed(family: str | None, scene_key: str) -> bool:
    allowed = scene_keys_for_family(family)
    return allowed is None or scene_key in allowed
