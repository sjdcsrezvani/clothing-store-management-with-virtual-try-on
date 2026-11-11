"""Fixed photo slots shared by the product and variant forms.

Every garment is photographed the same way: main, back. Hats, socks and
gloves use the same two slots, and shoes use top, left side, right side.
The family comes from the same free-text classifier Instagram Studio uses, so
the form and the studio can never disagree about what a product is.
"""
from __future__ import annotations

from services.instagram_product_types import FEET, HANDS, HEADWEAR, SOCKS, product_family

MODE_GARMENT = "garment"
MODE_ACCESSORY = "accessory"
MODE_SHOES = "shoes"

SLOTS = {
    MODE_GARMENT: (
        {"slot": 0, "view": "main", "label": "تصویر اصلی",
         "hint": "عکس روبه‌روی لباس.", "prompt": "primary product photo"},
        {"slot": 1, "view": "back", "label": "تصویر پشت",
         "hint": "پشت همان لباس.", "prompt": "back view"},
    ),
    MODE_ACCESSORY: (
        {"slot": 0, "view": "main", "label": "تصویر اصلی",
         "hint": "عکس روبه‌روی محصول.", "prompt": "primary product photo"},
        {"slot": 1, "view": "back", "label": "تصویر پشت",
         "hint": "پشت همان محصول.", "prompt": "back view"},
    ),
    MODE_SHOES: (
        {"slot": 0, "view": "main", "label": "تصویر اصلی (از بالا)",
         "hint": "نمای از بالای کفش.", "prompt": "primary product photo, top view"},
        {"slot": 1, "view": "left", "label": "نمای چپ",
         "hint": "نمای کنار چپ کفش.", "prompt": "left side view"},
        {"slot": 2, "view": "right", "label": "نمای راست",
         "hint": "نمای کنار راست کفش.", "prompt": "right side view"},
    ),
}


def image_mode_for_family(family: str | None) -> str:
    if family == FEET:
        return MODE_SHOES
    if family in (HEADWEAR, HANDS, SOCKS):
        return MODE_ACCESSORY
    return MODE_GARMENT


def image_mode_for(product) -> str:
    return image_mode_for_family(product_family(product) if product is not None else None)


def image_slots_for(product) -> list[dict]:
    return [dict(slot) for slot in SLOTS[image_mode_for(product)]]


def reference_views_for(family: str | None) -> list[str]:
    """English names of this family's photo slots, in slot order."""
    return [slot["prompt"] for slot in SLOTS[image_mode_for_family(family)]]


def slot_for_view(family: str | None, view: str) -> int | None:
    """The fixed slot for a semantic view, when this family has one."""
    for slot in SLOTS[image_mode_for_family(family)]:
        if slot["view"] == view:
            return slot["slot"]
    return None
