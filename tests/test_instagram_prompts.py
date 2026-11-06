"""Prompt builder contract for Instagram Studio Phase 1."""
from services.instagram_prompts import (
    ASPECTS,
    BACKGROUNDS,
    COMPOSITIONS,
    FRAMINGS,
    IG_PROMPT_VERSION,
    LIGHTINGS,
    POSES,
    SCENES,
    aspect_label,
    build_garment_prompt,
    build_model_prompt,
    build_product_brief_prompt,
    build_text_prompt,
    scene_label,
)


def _prompt(scene="packshot", aspect="4:5", refs=1, note="", details=""):
    return build_garment_prompt(
        product_name="تیشرت رای‌کیدز",
        variant_color="آبی",
        scene_key=scene,
        aspect_ratio=aspect,
        reference_count=refs,
        user_note=note,
        garment_details=details,
    )


def _brief_prompt(scene="", brief="soft morning light, minimal beige studio", details="", aspect="4:5"):
    return build_product_brief_prompt(
        product_name="تیشرت رای‌کیدز",
        variant_color="آبی",
        scene_key=scene,
        aspect_ratio=aspect,
        reference_count=1,
        user_brief=brief,
        garment_details=details,
    )


def _model_prompt(**overrides):
    values = dict(
        product_name="تیشرت رای‌کیدز",
        variant_color="آبی",
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


def test_scene_catalog_has_the_ten_approved_modes():
    assert len(SCENES) == 10
    assert set(SCENES) == {
        "hanger", "flat_lay", "ghost_mannequin", "detail_closeup",
        "fabric_texture", "folded_stack", "wooden_hanger_wall",
        "packshot", "back_view", "soft_shadow_studio",
    }
    assert scene_label("packshot") == "پکشات یکدست"


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
    assert "Image 2 is an additional view" in two
    assert "Image 3" not in two

    three = _prompt(refs=3)
    assert "Image 3 is a detail view" in three
    assert "back view" not in three.lower()


def test_aspect_ratios_have_exact_sizes_and_composition_clauses():
    assert ASPECTS["4:5"]["seedream_size"] == "1440x1800"
    assert ASPECTS["1:1"]["seedream_size"] == "1440x1440"
    assert ASPECTS["9:16"]["seedream_size"] == "1440x2560"
    assert aspect_label("4:5").startswith("۴:۵")

    for ratio, entry in ASPECTS.items():
        prompt = _prompt(aspect=ratio)
        assert entry["composition"] in prompt, ratio
        assert entry["seedream_size"] in prompt


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
    assert "Image 3 is an additional view" in two
    assert "Image 4" not in two

    three = _model_prompt(garment_reference_count=3)
    assert "Image 4 is a detail view" in three


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
    assert "The garment must remain the clear visual subject" in prompt
    assert "Do not add text, logos, watermarks, or extra people" in prompt
    assert "correctly fitted and visually dominant" in prompt
    assert "Avoid:" in prompt


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
