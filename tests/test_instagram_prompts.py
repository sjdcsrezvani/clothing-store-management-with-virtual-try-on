"""Prompt builder contract for Instagram Studio Phase 1."""
from services.instagram_prompts import (
    ASPECTS,
    BACKGROUND_LOCK,
    BACKGROUNDS,
    COMPOSITIONS,
    FRAMINGS,
    IG_PROMPT_VERSION,
    LIGHTINGS,
    MODEL_EDIT_REMINDER,
    MODEL_FACE_RULE,
    MODEL_IDENTITY,
    MODEL_OUTFIT_RULES,
    POSES,
    POSE_REFERENCE_VIEWS,
    ROOM_BACKGROUND_SCENES,
    SCENES,
    SCENE_REFERENCE_VIEWS,
    aspect_label,
    build_garment_prompt,
    build_model_prompt,
    build_product_brief_prompt,
    build_text_image_prompt,
    build_text_prompt,
    garment_prompt_sections,
    model_prompt_sections,
    scene_label,
    text_image_prompt_sections,
)


def _prompt(scene="packshot", aspect="4:5", refs=1, note="", details="",
            product_name="", variant_color="", background=None, family=None,
            reference_views=None):
    return build_garment_prompt(
        product_name=product_name,
        variant_color=variant_color,
        scene_key=scene,
        aspect_ratio=aspect,
        reference_count=refs,
        user_note=note,
        garment_details=details,
        background=background,
        garment_family=family,
        reference_views=reference_views,
    )


def _brief_prompt(scene="", brief="soft morning light, minimal beige studio", details="", aspect="4:5",
                  product_name="", variant_color="", background=None, family=None):
    return build_product_brief_prompt(
        product_name=product_name,
        variant_color=variant_color,
        scene_key=scene,
        aspect_ratio=aspect,
        reference_count=1,
        user_brief=brief,
        garment_details=details,
        background=background,
        garment_family=family,
    )


def _model_prompt(**overrides):
    values = dict(
        product_name="",
        variant_color="",
        model_age=7,
        model_gender="girl",
        pose_key="standing_front",
        background_key="studio_white",
        framing_key="full_body",
        lighting_key="soft_daylight",
        composition_key="centered_catalog",
        aspect_ratio="4:5",
        garment_reference_count=1,
        user_note="",
        garment_details="",
        show_full_garment=True,
    )
    values.update(overrides)
    return build_model_prompt(**values)


def test_scene_catalog_has_the_eight_approved_modes():
    assert len(SCENES) == 8
    assert set(SCENES) == {
        "hanger", "flat_lay", "ghost_mannequin", "folded_stack",
        "wooden_hanger_wall", "packshot", "back_view", "soft_shadow_studio",
    }
    assert scene_label("packshot") == "پکشات یکدست"


def test_reference_views_default_to_main_and_name_the_special_cases():
    assert set(SCENE_REFERENCE_VIEWS) == set(SCENES)
    assert SCENE_REFERENCE_VIEWS["flat_lay"] == ("main",)
    assert SCENE_REFERENCE_VIEWS["back_view"] == ("main", "back")

    assert set(POSE_REFERENCE_VIEWS) == set(POSES)
    assert POSE_REFERENCE_VIEWS["back_view"] == ("main", "back")
    assert all(
        views == ("main",)
        for key, views in POSE_REFERENCE_VIEWS.items()
        if key != "back_view"
    )
    assert all(
        "detail" not in views for views in POSE_REFERENCE_VIEWS.values()
    )


def test_every_scene_carries_its_clause_fidelity_and_negatives():
    for key, entry in SCENES.items():
        prompt = _prompt(scene=key)
        assert entry["clause"] in prompt, key
        assert "only source of truth" in prompt
        assert "Do not add, remove, recolor" in prompt
        assert "Avoid:" in prompt
        assert "Before finishing, verify:" in prompt


def test_reference_mapping_is_generic_and_deterministic():
    one = _prompt(refs=1)
    assert "Image 1 is the primary product photo" in one
    assert "Image 2" not in one

    two = _prompt(refs=2)
    assert "Image 2 is the back view" in two
    assert "Image 3" not in two


def test_aspect_ratios_keep_the_composition_clause_without_a_size_claim():
    assert ASPECTS["4:5"]["seedream_size"] == "1440x1800"
    assert ASPECTS["1:1"]["seedream_size"] == "1440x1440"
    assert ASPECTS["9:16"]["seedream_size"] == "1440x2560"
    assert aspect_label("4:5").startswith("۴:۵")

    for ratio, entry in ASPECTS.items():
        prompt = _prompt(aspect=ratio)
        assert entry["composition"] in prompt, ratio
        assert entry["seedream_size"] not in prompt, ratio
        assert "Output size" not in prompt, ratio


def test_user_note_is_delimited_and_cannot_override_fidelity():
    prompt = _prompt(note="soft morning light, beige studio")
    assert 'Additional creative direction from the shop' in prompt
    assert '"soft morning light, beige studio"' in prompt
    assert prompt.index("Additional creative direction") < prompt.index("Before finishing, verify:")
    # The fidelity block is outside the quoted user section.
    assert prompt.index("only source of truth") < prompt.index("Additional creative direction")


def test_variant_details_are_injected_and_the_image_stays_authoritative():
    prompt = _prompt(details="دو تکه؛ آستین کوتاه")
    assert "دو تکه؛ آستین کوتاه" in prompt
    assert "Shop-supplied garment details" in prompt
    assert "reference image wins" in prompt
    # The visible reference stays the final authority over the text description.
    assert prompt.index("only source of truth") < prompt.index("Shop-supplied garment details")


def test_empty_variant_details_add_no_empty_quote():
    assert "Shop-supplied garment details" not in _prompt()


def test_product_brief_prompt_carries_brief_details_and_neutral_scene():
    prompt = _brief_prompt(brief="soft morning light, beige studio", details="دو تکه")
    assert '"soft morning light, beige studio"' in prompt
    assert "Creative campaign brief from the shop" in prompt
    assert "دو تکه" in prompt
    assert "Premium Instagram campaign composition" in prompt
    assert "Avoid:" in prompt
    assert prompt.index("only source of truth") < prompt.index("Creative campaign brief")


def test_product_brief_uses_the_selected_scene_instead_of_the_neutral_one():
    prompt = _brief_prompt(scene="flat_lay")
    assert SCENES["flat_lay"]["clause"] in prompt
    assert "Premium Instagram campaign composition" not in prompt


def test_product_brief_requires_a_brief():
    import pytest
    with pytest.raises(ValueError):
        _brief_prompt(brief="   ")


def test_model_libraries_cover_the_approved_categories():
    assert {"standing_front", "three_quarter", "side_profile", "back_view",
            "walking_stance", "seated_bench", "seated_block", "gentle_walk",
            "small_turn"} <= set(POSES)
    assert {"studio_white", "studio_beige", "studio_gray", "studio_pastel",
            "studio_color", "studio_editorial"} <= set(BACKGROUNDS)
    assert {"city_quiet", "pedestrian", "park", "garden_walkway", "beach",
            "architecture"} <= set(BACKGROUNDS)
    assert {"cafe_family", "home_bright", "playroom", "bookshop",
            "apartment"} <= set(BACKGROUNDS)
    assert {"spring_garden", "autumn_street", "winter_city", "summer_outdoor",
            "festive"} <= set(BACKGROUNDS)
    assert set(FRAMINGS) == {"full_body", "three_quarter_body", "waist_up", "detail_crop"}
    assert len(LIGHTINGS) >= 4
    assert len(COMPOSITIONS) >= 3


def test_model_prompt_maps_image_one_to_the_model_and_later_images_to_the_garment():
    one = _model_prompt(garment_reference_count=1)
    assert "Image 1 is the authorized AI model reference" in one
    assert "Image 2 is the primary product photo" in one
    assert "Image 3" not in one

    two = _model_prompt(garment_reference_count=2)
    assert "Image 3 is the back view" in two
    assert "Image 4" not in two


def test_model_prompt_carries_every_selected_photography_clause():
    prompt = _model_prompt(
        pose_key="seated_bench", background_key="park",
        framing_key="three_quarter_body", lighting_key="golden_hour",
        composition_key="editorial_negative_space")
    assert POSES["seated_bench"]["clause"] in prompt
    assert BACKGROUNDS["park"]["clause"] in prompt
    assert FRAMINGS["three_quarter_body"]["clause"] in prompt
    assert LIGHTINGS["golden_hour"]["clause"] in prompt
    assert COMPOSITIONS["editorial_negative_space"]["clause"] in prompt


def test_model_prompt_keeps_the_garment_fidelity_and_dominance_rules():
    prompt = _model_prompt()
    assert "only source of truth" in prompt
    assert "The target garment must remain the clear visual subject" in prompt
    assert "Do not add text, logos, watermarks, or extra people" in prompt
    assert "correctly fitted and visually dominant" in prompt
    assert "Avoid:" in prompt
    assert MODEL_IDENTITY in prompt
    assert MODEL_FACE_RULE in prompt


def test_model_prompt_age_rules_separate_children_teens_and_adults():
    child = _model_prompt(model_age=2)
    assert "young child" in child
    assert "No makeup" in child
    assert "no body-focused framing" in child

    teen = _model_prompt(model_age=13)
    assert "teenager" in teen
    assert "No adult fashion posing" in teen

    adult = _model_prompt(model_age=18)
    assert "adult" in adult
    assert "clearly separate from the child and teen profiles" in adult


def test_model_prompt_injects_variant_details_and_the_delimited_note():
    prompt = _model_prompt(garment_details="دو تکه؛ آستین کوتاه",
                           user_note="soft morning light")
    assert "دو تکه؛ آستین کوتاه" in prompt
    assert '"soft morning light"' in prompt
    assert prompt.index("only source of truth") < prompt.index("Additional creative direction")
    assert prompt.index("Additional creative direction") < prompt.index("Before finishing, verify:")


def test_model_detail_crop_does_not_claim_the_full_garment_is_visible():
    prompt = _model_prompt(framing_key="detail_crop", show_full_garment=True)
    assert "designated detail shot" in prompt
    assert "full garment must remain visible" not in prompt


def test_model_prompt_completes_the_outfit_for_each_family():
    upper = _model_prompt(garment_family="upper")
    assert "UPPER-BODY piece" in upper
    assert "must ALWAYS be present" in upper
    assert "no missing bottom" in upper
    assert "no missing shoes" in upper

    lower = _model_prompt(garment_family="lower")
    assert "LOWER-BODY piece" in lower
    assert "Only one bottom on the model" in lower

    set_prompt = _model_prompt(garment_family="set")
    assert "complete two-piece SET" in set_prompt
    assert "ONLY thing you may change is the SHOES" in set_prompt

    layered = _model_prompt(garment_family="layered")
    assert "layered outfit" in layered
    assert "Never drop the inner layer" in layered
    assert "different category, different fabric" in layered

    outer = _model_prompt(garment_family="outer")
    assert "outer layer" in outer
    assert "ONE plain neutral inner top" in outer
    assert "never leave the torso bare" in outer

    one_piece = _model_prompt(garment_family="one_piece")
    assert "ONE-PIECE garment" in one_piece
    assert "NEVER invent pants beneath it" in one_piece

    for family in ("headwear", "hands", "feet", "socks"):
        assert MODEL_OUTFIT_RULES[family] in _model_prompt(garment_family=family)

    unknown = _model_prompt()
    assert MODEL_OUTFIT_RULES["upper"] not in unknown
    assert "Reproduce the exact target product" in unknown


def test_model_prompt_scopes_fidelity_and_keeps_outfit_rule_last():
    prompt = _model_prompt(garment_family="upper")
    assert "Other garments may be added ONLY as the outfit rule" in prompt
    assert "extra pieces unless they are explicitly visible" not in prompt
    assert prompt.index("Pose:") < prompt.index("FACE RULE:")
    assert prompt.index("Before finishing, verify:") < prompt.index("OUTFIT RULE")
    assert prompt.endswith(MODEL_EDIT_REMINDER)

    product_only = _prompt()
    assert "extra pieces unless they are explicitly visible" in product_only
    assert "extra garments" in product_only


def test_model_prompt_outfit_override_replaces_the_detected_rule():
    prompt = _model_prompt(garment_family="upper", outfit_family="one_piece")
    assert "ONE-PIECE garment" in prompt
    assert "UPPER-BODY piece" not in prompt
    labels = [section["label"] for section in model_prompt_sections(
        model_age=7, model_gender="girl", pose_key="standing_front",
        background_key="studio_white", framing_key="full_body",
        lighting_key="soft_daylight", composition_key="centered_catalog",
        aspect_ratio="4:5", garment_reference_count=1,
        garment_family="upper", outfit_family="one_piece")]
    assert "نوع محصول: یکسره" in labels


def test_one_piece_hanger_and_family_clause_never_split_the_garment():
    prompt = _prompt(scene="hanger", family="one_piece")
    assert "one-piece garment hangs" in prompt
    assert "do not split it into a top and bottom" in prompt
    assert "two-piece set" not in prompt


def test_model_prompt_rejects_unknown_keys_and_missing_garment_references():
    import pytest
    with pytest.raises(KeyError):
        _model_prompt(pose_key="not-a-pose")
    with pytest.raises(KeyError):
        _model_prompt(background_key="not-a-background")
    with pytest.raises(KeyError):
        _model_prompt(framing_key="not-a-frame")
    with pytest.raises(ValueError):
        _model_prompt(garment_reference_count=0)


def test_text_prompt_is_a_reference_free_campaign_brief():
    brief = "cozy autumn campaign with warm knit layers and soft city light"
    prompt = build_text_prompt(user_text=brief, aspect_ratio="4:5")
    assert "Create an original commercial Instagram fashion campaign image" in prompt
    assert f'"{brief}"' in prompt
    assert "Leave clean negative space" in prompt
    assert "Do not include brand logos, watermarks" in prompt
    assert "garment" not in prompt.lower()
    assert "source of truth" not in prompt


def test_text_prompt_requires_a_brief_and_a_known_aspect():
    import pytest
    with pytest.raises(ValueError):
        build_text_prompt(user_text="   ", aspect_ratio="4:5")
    with pytest.raises(KeyError):
        build_text_prompt(user_text="a valid campaign brief", aspect_ratio="16:9")


def test_prompt_version_is_pinned():
    assert IG_PROMPT_VERSION


def test_unknown_scene_or_aspect_is_rejected():
    import pytest
    with pytest.raises(KeyError):
        _prompt(scene="not-a-scene")
    with pytest.raises(KeyError):
        _prompt(aspect="16:9")


def test_prompt_sections_name_the_options_that_produced_them():
    sections = garment_prompt_sections(
        scene_key="packshot",
        aspect_ratio="1:1",
        reference_count=2,
        user_note="soft morning light",
        garment_details="دو تکه",
    )
    by_label = {section["label"]: section for section in sections}
    assert by_label["حالت عکاسی: پکشات یکدست"]["kind"] == "option"
    assert by_label["نسبت تصویر: ۱:۱ (مربع)"]["kind"] == "option"
    assert by_label["عکس‌های مرجع: 2"]["kind"] == "option"
    assert by_label["دستور پایه"]["kind"] == "fixed"
    assert by_label["توضیح تکمیلی شما"]["kind"] == "user"
    assert by_label["جزئیات ثبت‌شده تنوع"]["kind"] == "user"
    assert by_label["حالت عکاسی: پکشات یکدست"]["text"] in _prompt(
        scene="packshot", aspect="1:1", refs=2, note="soft morning light",
        details="دو تکه")


def test_prompts_never_include_the_product_name_or_colour():
    product = "نام محصول یکتا"
    colour = "رنگ یکتا"
    prompts = [
        _prompt(product_name=product, variant_color=colour),
        _brief_prompt(product_name=product, variant_color=colour),
        _model_prompt(product_name=product, variant_color=colour),
    ]
    for prompt in prompts:
        assert product not in prompt
        assert colour not in prompt
        assert "محصول و رنگ" not in prompt


def test_text_image_prompt_edits_the_uploaded_photo_from_the_brief():
    prompt = build_text_image_prompt(
        user_text="warm autumn window light, clean negative space",
        aspect_ratio="1:1")
    assert "Edit the supplied reference image" in prompt
    assert "reference image as the visual source" in prompt
    assert '"warm autumn window light, clean negative space"' in prompt
    assert "1:1" in prompt

    labels = {section["label"] for section in text_image_prompt_sections(
        user_text="brief", aspect_ratio="4:5")}
    assert "عکس مرجع شما" in labels
    assert "متن کمپین شما" in labels


def test_text_image_prompt_requires_a_brief_and_a_known_aspect():
    import pytest
    with pytest.raises(ValueError):
        build_text_image_prompt(user_text="   ", aspect_ratio="4:5")
    with pytest.raises(KeyError):
        build_text_image_prompt(user_text="a valid campaign brief", aspect_ratio="16:9")


BRAND_BACKGROUND = {
    "key": "starter_ivory",
    "label": "استودیو کرم",
    "prompt_en": "Walls: warm ivory limewash plaster. Floor: pale oak.",
    "source": "brand",
}


def test_hanger_scenes_use_the_same_signature_hanger_and_rail():
    assert SCENES["hanger"]["label"] == "آویزان روی رگال"
    assert SCENES["wooden_hanger_wall"]["label"] == "رگال چوبی روی دیوار"
    for scene in ("hanger", "wooden_hanger_wall"):
        prompt = _prompt(scene=scene)
        assert "natural oak hanger" in prompt, scene
        assert "brushed-brass hook" in prompt, scene
        assert "matte-black rail" in prompt, scene
        assert "identical in every image" in prompt, scene


def test_room_scenes_carry_the_brand_background_and_its_lock():
    assert ROOM_BACKGROUND_SCENES == ("hanger", "wooden_hanger_wall", "folded_stack")
    for scene in ROOM_BACKGROUND_SCENES:
        prompt = _prompt(scene=scene, background=BRAND_BACKGROUND)
        assert BACKGROUND_LOCK in prompt, scene
        assert BRAND_BACKGROUND["prompt_en"] in prompt, scene

    sections = garment_prompt_sections(
        scene_key="hanger", aspect_ratio="4:5", reference_count=1,
        background=BRAND_BACKGROUND)
    labels = {section["label"]: section for section in sections}
    assert labels["پس‌زمینه: استودیو کرم"]["kind"] == "option"
    assert BRAND_BACKGROUND["prompt_en"] in labels["پس‌زمینه: استودیو کرم"]["text"]


def test_specialized_scenes_ignore_the_brand_background():
    for scene in ("packshot", "ghost_mannequin", "soft_shadow_studio",
                  "flat_lay", "back_view"):
        prompt = _prompt(scene=scene, background=BRAND_BACKGROUND)
        assert BRAND_BACKGROUND["prompt_en"] not in prompt, scene


def test_product_brief_uses_the_background_for_free_and_room_scenes():
    for scene in ("", "hanger", "wooden_hanger_wall", "folded_stack"):
        prompt = _brief_prompt(scene=scene, background=BRAND_BACKGROUND)
        assert BACKGROUND_LOCK in prompt, scene
        assert BRAND_BACKGROUND["prompt_en"] in prompt, scene
    assert BRAND_BACKGROUND["prompt_en"] not in _brief_prompt(
        scene="packshot", background=BRAND_BACKGROUND)


def test_model_brand_background_replaces_the_location_and_lighting():
    sections = model_prompt_sections(
        model_age=7, model_gender="girl", pose_key="standing_front",
        background_key="studio_white", framing_key="full_body",
        lighting_key="soft_daylight", composition_key="centered_catalog",
        aspect_ratio="4:5", garment_reference_count=1,
        background=BRAND_BACKGROUND)
    labels = {section["label"] for section in sections}
    assert "پس‌زمینه: استودیو کرم" in labels
    assert not any(label.startswith("نور:") for label in labels)

    prompt = _model_prompt(background=BRAND_BACKGROUND)
    assert BACKGROUND_LOCK in prompt
    assert BRAND_BACKGROUND["prompt_en"] in prompt
    assert LIGHTINGS["soft_daylight"]["clause"] not in prompt
    assert "Location:" not in prompt


def test_model_generic_location_keeps_the_lighting_section():
    prompt = _model_prompt(background_key="park", lighting_key="golden_hour")
    assert BACKGROUNDS["park"]["clause"] in prompt
    assert LIGHTINGS["golden_hour"]["clause"] in prompt


def test_no_prompt_claims_a_provider_size():
    prompts = [
        _prompt(),
        _brief_prompt(),
        _model_prompt(),
        build_text_prompt(user_text="a valid campaign brief", aspect_ratio="4:5"),
        build_text_image_prompt(user_text="a valid campaign brief", aspect_ratio="4:5"),
    ]
    for prompt in prompts:
        assert "Output size" not in prompt
        for entry in ASPECTS.values():
            assert entry["seedream_size"] not in prompt


def test_set_hanger_stages_upper_on_shoulder_and_lower_on_clip_hanger():
    prompt = _prompt(scene="hanger", family="set")
    assert "two-piece set" in prompt
    assert "upper-body piece" in prompt
    assert "clip hanger" in prompt
    assert "lower-body piece" in prompt
    assert "identical in every image" in prompt

    closer = _prompt(scene="wooden_hanger_wall", family="set")
    assert "clip hanger" in closer
    assert "viewed closer against the wall" in closer


def test_lower_body_hanger_uses_a_clip_hanger():
    prompt = _prompt(scene="hanger", family="lower")
    assert "clip hanger" in prompt
    assert "waistband" in prompt
    assert "two clips" in prompt


def test_layered_hanger_keeps_both_layers():
    prompt = _prompt(scene="hanger", family="layered")
    assert "under-layer top" in prompt
    assert "outer layer" in prompt
    assert "Both layers remain visible" in prompt


def test_product_brief_set_hanger_uses_the_clip_hanger():
    prompt = _brief_prompt(scene="hanger", family="set")
    assert "clip hanger" in prompt


def test_each_family_gets_its_own_type_clause():
    upper = _prompt(refs=1, family="upper")
    assert "Garment type (بالاتنه)" in upper
    assert "upper-body garment" in upper
    assert "neckline" in upper

    shoes = _prompt(refs=1, family="feet")
    assert "Garment type (کفش)" in shoes
    assert "pair of shoes" in shoes
    assert "left/right" in shoes

    hat = _prompt(refs=1, family="headwear")
    assert "Garment type (کلاه)" in hat
    assert "headwear" in hat
    assert "crown" in hat

    unknown = _prompt(refs=1)
    assert "Garment type" not in unknown


def test_reference_lines_name_the_family_s_slots_and_skip_missing_ones():
    shoes = _prompt(refs=3, family="feet")
    assert "Image 1 is the primary product photo, top view" in shoes
    assert "Image 2 is the left side view" in shoes
    assert "Image 3 is the right side view" in shoes

    hat = _prompt(refs=2, family="headwear")
    assert "Image 2 is the back view" in hat
    assert "Image 3" not in hat

    only_main = _prompt(refs=1, family="headwear")
    assert "Image 2" not in only_main
    assert "back view" not in only_main.lower()


def test_model_prompt_uses_the_family_s_slot_names():
    shoes = _model_prompt(garment_reference_count=3, garment_family="feet")
    assert "Garment type (کفش)" in shoes
    assert "Image 2 is the primary product photo, top view" in shoes
    assert "Image 3 is the left side view" in shoes
    assert "Image 4 is the right side view" in shoes


def test_reference_lines_follow_the_real_selected_slots():
    prompt = _prompt(refs=2, reference_views=[
        "primary product photo", "back view"])
    assert "Image 2 is the back view" in prompt

    only_back = _prompt(refs=1, reference_views=["back view"])
    assert "Image 1 is the back view" in only_back
    assert "Image 2" not in only_back


def test_model_reference_lines_follow_the_real_selected_slots():
    prompt = _model_prompt(garment_reference_count=2, reference_views=[
        "primary product photo", "back view"])
    assert "Image 2 is the primary product photo" in prompt
    assert "Image 3 is the back view" in prompt
