"""Product-family staging rules for Instagram Studio."""
from models import Product
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
    RECOMMENDATION_NOTES_FA,
    SET,
    SOCKS,
    UPPER,
    is_scene_allowed,
    product_family,
    recommended_scene,
)


def _product(name="", category="", garment_type="", size_type=None):
    return Product(name=name, category=category or None,
                   garment_type=garment_type or None, size_type=size_type)


def test_common_product_words_map_to_staging_families():
    assert product_family(_product("تیشرت آبی", "بالاتنه")) == UPPER
    assert product_family(_product("ست لس", "ست")) == SET
    assert product_family(_product("شلوار جین")) == LOWER
    assert product_family(_product("کاپشن زمستانی")) == OUTER
    assert product_family(_product("کاپشن و تیشرت")) == LAYERED
    assert product_family(_product("کتانی تست", "کفش")) == FEET
    assert product_family(_product("کلاه بافت")) == HEADWEAR
    assert product_family(_product("دستکش")) == HANDS
    assert product_family(_product("جوراب")) == SOCKS
    assert product_family(_product("محصول بدون نوع")) is None


def test_generic_clothing_and_the_size_ladder_fill_the_gap():
    assert product_family(_product("لباس دخترانه", "لباس کودک")) == UPPER
    assert product_family(_product("لباس کودک", "پوشاک بچگانه")) == UPPER
    # A generic "لباس" must not turn a jacket or pants into a layered outfit.
    assert product_family(_product("لباس کاپشن پسرانه")) == OUTER
    assert product_family(_product("لباس شلوار جین")) == LOWER
    assert product_family(_product("محصول بدون نوع", size_type="lower")) == LOWER
    assert product_family(_product("محصول بدون نوع", size_type="set")) == SET
    assert product_family(_product("محصول بدون نوع", size_type="shoes")) == FEET


def test_rompers_and_jumpsuits_are_one_piece_not_two_piece_sets():
    assert product_family(_product("سرهمی نوزاد")) == ONE_PIECE
    assert product_family(_product("رامپر دخترانه")) == ONE_PIECE
    assert product_family(_product("اورال پسرانه")) == ONE_PIECE
    assert product_family(_product("jumpsuit")) == ONE_PIECE
    assert product_family(_product("onesie")) == ONE_PIECE
    assert product_family(_product("ونسی")) == ONE_PIECE
    assert product_family(_product("مایو")) == ONE_PIECE
    # A bodysuit is an upper piece; it still needs a bottom.
    assert product_family(_product("بادی آستین بلند")) == UPPER
    assert product_family(_product("ست لس")) == SET


def test_every_family_has_a_prompt_clause_scene_and_suggestion():
    for family in FAMILY_LABELS_FA:
        assert FAMILY_PROMPT_CLAUSES.get(family), family
        assert recommended_scene(family), family
        assert RECOMMENDATION_NOTES_FA.get(family), family


def test_accessories_cannot_use_hanger_or_folded_scenes():
    for family in (HEADWEAR, HANDS, FEET, SOCKS):
        assert not is_scene_allowed(family, "hanger")
        assert not is_scene_allowed(family, "folded_stack")
        assert not is_scene_allowed(family, "ghost_mannequin")
        assert is_scene_allowed(family, "flat_lay")
        assert recommended_scene(family) == "flat_lay"
    assert is_scene_allowed(UPPER, "hanger")
    assert is_scene_allowed(SET, "hanger")
    assert is_scene_allowed(LOWER, "hanger")
    assert is_scene_allowed(None, "hanger")
