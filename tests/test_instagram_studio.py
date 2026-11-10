"""Instagram Studio validation and job-creation contract."""
import uuid
from pathlib import Path

import pytest
from PIL import Image

from models import BackgroundJob, InstagramGeneration, Product, ProductVariant, ShopModel, VariantImage
from services.instagram_prompts import POSES, SCENES
from services.instagram_studio import (
    InstagramValidationError,
    collect_reference_paths,
    create_generation,
    select_reference_paths,
    variation_payload,
    variant_reference_images,
)
from tests.test_roles import _staff


def _make_product_with_images(db_session, count=2, name="تیشرت تست", details=None):
    product = Product(name=name)
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100_000,
                             barcode=f"IG{uuid.uuid4().hex[:6]}",
                             tryon_details=details)
    db_session.add(variant)
    db_session.flush()

    root = Path("static/uploads/ig_test") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        path = root / f"ref{i}.jpg"
        Image.new("RGB", (80, 80), (30 * i, 80, 120)).save(path, "JPEG")
        url = "/" + str(path)
        db_session.add(VariantImage(variant_id=variant.id, image_path=url, sort_order=i))
        paths.append(url)
    if paths:
        variant.image_path = paths[0]
    db_session.commit()
    db_session.refresh(variant)
    return product, variant, paths, root


def _cleanup(root: Path):
    import shutil
    shutil.rmtree(root, ignore_errors=True)


def test_reference_images_are_variant_gallery_only(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=2)
    try:
        images = variant_reference_images(variant)
        assert [image["path"] for image in images] == paths
        assert images[0]["label"] == "تصویر اصلی"
    finally:
        _cleanup(root)


def test_collect_reference_paths_accepts_subset_and_rejects_foreign_path(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        selected = collect_reference_paths(variant, [paths[2], paths[0]])
        assert selected == [paths[2].lstrip("/"), paths[0].lstrip("/")]

        with pytest.raises(InstagramValidationError):
            collect_reference_paths(variant, ["/static/uploads/other.jpg"])

        with pytest.raises(InstagramValidationError):
            collect_reference_paths(variant, paths + [paths[0]])
    finally:
        _cleanup(root)


def test_prompt_names_the_real_slots_and_skips_the_absent_back_photo(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=2)
    user, _password = _staff(db_session, "ig-slot-views", "manager")
    try:
        # Keep the main photo in slot 0 and move the second photo to the
        # detail slot, leaving the back slot empty.
        frame = db_session.query(VariantImage).filter(
            VariantImage.image_path == paths[1]).one()
        frame.sort_order = 2
        db_session.commit()

        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0], paths[1]], scene_key="detail_closeup",
            aspect_ratio="4:5")
        assert "Image 2 is the detail close-up" in generation.rendered_prompt
        assert "back view" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_select_reference_paths_uses_the_scene_slots(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        main, back, detail = [path.lstrip("/") for path in paths]
        for scene in SCENES:
            expected = [main]
            if scene == "back_view":
                expected = [main, back]
            elif scene in ("detail_closeup", "fabric_texture"):
                expected = [main, detail]
            assert select_reference_paths(
                variant, paths, scenario="garment", scene_key=scene) == expected, scene

        assert select_reference_paths(
            variant, paths, scenario="product_brief", scene_key="") == [main]
    finally:
        _cleanup(root)


def test_select_reference_paths_puts_main_first_and_auto_adds_it(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        main, back, detail = [path.lstrip("/") for path in paths]
        assert select_reference_paths(
            variant, [paths[2], paths[0]], scenario="garment",
            scene_key="detail_closeup") == [main, detail]
        assert select_reference_paths(
            variant, [paths[1]], scenario="garment",
            scene_key="back_view") == [main, back]
    finally:
        _cleanup(root)


def test_select_reference_paths_skips_a_missing_optional_view(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=1)
    try:
        main = paths[0].lstrip("/")
        assert select_reference_paths(
            variant, paths, scenario="garment", scene_key="back_view") == [main]
        assert select_reference_paths(
            variant, paths, scenario="garment", scene_key="detail_closeup") == [main]
    finally:
        _cleanup(root)


def test_select_reference_paths_rejects_a_variant_without_main(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        db_session.query(VariantImage).filter(
            VariantImage.image_path == paths[0]).delete()
        db_session.commit()
        with pytest.raises(InstagramValidationError) as error:
            select_reference_paths(
                variant, [paths[1]], scenario="garment", scene_key="flat_lay")
        assert "تصویر اصلی" in str(error.value)
    finally:
        _cleanup(root)


def test_model_reference_selection_never_sends_detail(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        main, back, _detail = [path.lstrip("/") for path in paths]
        for pose in POSES:
            expected = [main, back] if pose == "back_view" else [main]
            assert select_reference_paths(
                variant, paths, scenario="model_scene", scene_key="",
                pose_key=pose) == expected, pose
    finally:
        _cleanup(root)


def test_family_without_a_view_does_not_substitute_another_slot(db_session):
    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    try:
        main, back, _detail = [path.lstrip("/") for path in paths]

        product.category = "کفش"
        product.name = "کتانی تست"
        db_session.commit()
        assert select_reference_paths(
            variant, paths, scenario="garment", scene_key="detail_closeup") == [main]
        assert select_reference_paths(
            variant, paths, scenario="model_scene", pose_key="back_view") == [main]

        product.category = "کلاه"
        db_session.commit()
        assert select_reference_paths(
            variant, paths, scenario="garment", scene_key="detail_closeup") == [main]
        assert select_reference_paths(
            variant, paths, scenario="model_scene", pose_key="back_view") == [main, back]
    finally:
        _cleanup(root)


@pytest.mark.parametrize(("scene_key", "kept"), [
    ("flat_lay", (0,)),
    ("back_view", (0, 1)),
    ("detail_closeup", (0, 2)),
    ("fabric_texture", (0, 2)),
])
def test_create_generation_sends_only_the_scene_references(
        db_session, scene_key, kept):
    import json

    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    user, _password = _staff(db_session, f"ig-refs-{scene_key}", "manager")
    try:
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=paths, scene_key=scene_key, aspect_ratio="4:5")
        payload = json.loads(job.payload)
        assert payload["reference_paths"] == [paths[i].lstrip("/") for i in kept]
        if len(kept) == 1:
            assert "Image 2" not in generation.rendered_prompt
        elif scene_key == "back_view":
            assert "Image 2 is the back view" in generation.rendered_prompt
            assert "detail close-up" not in generation.rendered_prompt
        else:
            assert "Image 2 is the detail close-up" in generation.rendered_prompt
            assert "back view" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_product_brief_free_campaign_sends_main_only(db_session):
    import json

    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    user, _password = _staff(db_session, "ig-brief-refs", "manager")
    try:
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=paths, scene_key="", aspect_ratio="4:5",
            scenario="product_brief", user_text="soft morning light")
        payload = json.loads(job.payload)
        assert payload["reference_paths"] == [paths[0].lstrip("/")]
        assert "Image 2" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_sends_only_the_pose_references(db_session, tmp_path):
    import json

    product, variant, paths, root = _make_product_with_images(db_session, count=3)
    model = _model_slot(db_session, tmp_path)
    user, _password = _staff(db_session, "ig-model-refs", "manager")
    try:
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=paths, scene_key="", aspect_ratio="4:5",
            **_model_options(pose_key="back_view"))
        payload = json.loads(job.payload)
        assert payload["reference_paths"][0] == model.image_path
        assert payload["reference_paths"][1:] == [
            paths[0].lstrip("/"), paths[1].lstrip("/")]
        assert "Image 2 is the primary product photo" in generation.rendered_prompt
        assert "Image 3 is the back view" in generation.rendered_prompt
        assert "detail close-up" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_variant_without_images_is_rejected(db_session):
    product = Product(name="بدون عکس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100_000, barcode="IGNOIMG")
    db_session.add(variant)
    db_session.commit()

    with pytest.raises(InstagramValidationError) as error:
        collect_reference_paths(variant, [])
    assert "عکس محصول ثبت نشده" in str(error.value)


def test_create_generation_builds_prompt_and_enqueues_one_job(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-maker", "manager")
    try:
        generation, job = create_generation(
            db_session,
            staff=user,
            product=product,
            variant=variant,
            selected_paths=[paths[0], paths[1]],
            scene_key="back_view",
            aspect_ratio="4:5",
            user_text="soft morning light",
            logo=False,
            logo_path="",
        )
        assert generation.id
        assert generation.prompt_version
        assert "back silhouette" in generation.rendered_prompt
        assert "soft morning light" in generation.rendered_prompt
        assert generation.job_id == job.id
        assert job.job_type == "instagram_image"
        payload = __import__("json").loads(job.payload)
        assert "/static/uploads/instagram/" in payload["result_url"]
        assert payload["reference_paths"] == [paths[0].lstrip("/"), paths[1].lstrip("/")]
        assert payload["aspect_ratio"] == "4:5"
        assert "prompt" in payload and payload["prompt"] == generation.rendered_prompt
        assert db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == "instagram_image").count() == 1
    finally:
        _cleanup(root)


def test_create_product_brief_reads_variant_details_and_allows_no_scene(db_session):
    product, variant, paths, root = _make_product_with_images(
        db_session, details="دو تکه؛ آستین کوتاه")
    user, _password = _staff(db_session, "ig-brief-maker", "manager")
    try:
        generation, job = create_generation(
            db_session,
            staff=user,
            product=product,
            variant=variant,
            selected_paths=[paths[0]],
            scene_key="",
            aspect_ratio="4:5",
            user_text="soft morning light, minimal beige studio",
            scenario="product_brief",
        )
        assert generation.scenario == "product_brief"
        assert generation.garment_mode is None
        assert "دو تکه؛ آستین کوتاه" in generation.rendered_prompt
        assert "soft morning light, minimal beige studio" in generation.rendered_prompt
        assert job.job_type == "instagram_image"
    finally:
        _cleanup(root)


def test_product_brief_rejects_a_brief_shorter_than_ten_characters(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-brief-short", "manager")
    try:
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session,
                staff=user,
                product=product,
                variant=variant,
                selected_paths=[paths[0]],
                scene_key="",
                aspect_ratio="4:5",
                user_text="کوتاه",
                scenario="product_brief",
            )
        assert db_session.query(InstagramGeneration).count() == 0
        assert db_session.query(BackgroundJob).count() == 0
    finally:
        _cleanup(root)


def test_garment_generation_injects_variant_details(db_session):
    product, variant, paths, root = _make_product_with_images(
        db_session, details="یقه گرد؛ دکمه پشت")
    user, _password = _staff(db_session, "ig-details", "manager")
    try:
        generation, _job = create_generation(
            db_session,
            staff=user,
            product=product,
            variant=variant,
            selected_paths=[paths[0]],
            scene_key="packshot",
            aspect_ratio="4:5",
        )
        assert "یقه گرد؛ دکمه پشت" in generation.rendered_prompt
    finally:
        _cleanup(root)


def _model_options(**overrides):
    values = dict(
        scenario="model_scene",
        model_slot="girl_7",
        pose_key="standing_front",
        background_key="studio_white",
        framing_key="full_body",
        lighting_key="soft_daylight",
        composition_key="centered_catalog",
    )
    values.update(overrides)
    return values


def _model_slot(db_session, tmp_path, slot_key="girl_7", image=True):
    path = str(tmp_path / f"{slot_key}.jpg")
    if image:
        Image.new("RGB", (120, 120), (120, 150, 180)).save(path, "JPEG")
    model = ShopModel(slot_key=slot_key, label_fa="مدل تست", image_path=path if image else None)
    db_session.add(model)
    db_session.commit()
    return model


def test_model_scene_requires_an_active_model_with_an_image(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-model-missing", "manager")
    try:
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session, staff=user, product=product, variant=variant,
                selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
                **_model_options())
        assert db_session.query(InstagramGeneration).count() == 0

        _model_slot(db_session, tmp_path, image=False)
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session, staff=user, product=product, variant=variant,
                selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
                **_model_options())
    finally:
        _cleanup(root)


def test_model_scene_rejects_a_disappeared_model_file(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-model-gone", "manager")
    try:
        _model_slot(db_session, tmp_path)
        model = db_session.query(ShopModel).one()
        model.image_path = str(tmp_path / "gone.jpg")
        db_session.commit()
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session, staff=user, product=product, variant=variant,
                selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
                **_model_options())
    finally:
        _cleanup(root)


def test_model_scene_prepends_the_model_reference_and_stores_choices(db_session, tmp_path):
    import json

    product, variant, paths, root = _make_product_with_images(
        db_session, details="دو تکه؛ آستین کوتاه")
    user, _password = _staff(db_session, "ig-model-maker", "manager")
    try:
        _model_slot(db_session, tmp_path, slot_key="boy_13")
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            user_text="soft morning light",
            **_model_options(
                model_slot="boy_13", pose_key="three_quarter",
                background_key="park", framing_key="three_quarter_body",
                lighting_key="golden_hour",
                composition_key="editorial_negative_space"))
        payload = json.loads(job.payload)
        assert payload["reference_paths"][0] == str(tmp_path / "boy_13.jpg")
        assert payload["reference_paths"][1:] == [paths[0].lstrip("/")]
        assert generation.scenario == "model_scene"
        assert generation.model_slot == "boy_13"
        assert generation.pose_key == "three_quarter"
        assert generation.background_key == "park"
        assert generation.garment_mode is None
        assert "teenager" in generation.rendered_prompt
        assert "دو تکه؛ آستین کوتاه" in generation.rendered_prompt
        assert "soft morning light" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_stores_the_effective_outfit_rule(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-model-outfit", "manager")
    try:
        _model_slot(db_session, tmp_path)
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            **_model_options())
        assert generation.outfit_category == "upper"
        assert "UPPER-BODY piece" in generation.rendered_prompt
        assert "no missing bottom" in generation.rendered_prompt
        payload = __import__("json").loads(job.payload)
        variation = variation_payload(generation, payload)
        assert variation["outfit_category"] == "upper"
    finally:
        _cleanup(root)


def test_model_scene_outfit_override_changes_the_rule(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    product.name = "پیراهن تست"
    db_session.commit()
    user, _password = _staff(db_session, "ig-model-override", "manager")
    try:
        _model_slot(db_session, tmp_path)
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            outfit_category="one_piece", **_model_options())
        assert generation.outfit_category == "one_piece"
        assert "ONE-PIECE garment" in generation.rendered_prompt
        assert "NEVER invent pants beneath it" in generation.rendered_prompt
        assert "UPPER-BODY piece" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_override_can_treat_a_one_piece_as_an_upper(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    product.name = "سرهمی تست"
    db_session.commit()
    user, _password = _staff(db_session, "ig-model-onesie-override", "manager")
    try:
        _model_slot(db_session, tmp_path)
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            outfit_category="upper", **_model_options())
        assert generation.outfit_category == "upper"
        assert "UPPER-BODY piece" in generation.rendered_prompt
        assert "no missing bottom" in generation.rendered_prompt
        assert "NEVER invent pants beneath it" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_ignores_a_clothing_override_for_accessories(db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    product.category = "کفش"
    product.name = "کتانی تست"
    db_session.commit()
    user, _password = _staff(db_session, "ig-model-shoes-override", "manager")
    try:
        _model_slot(db_session, tmp_path)
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            outfit_category="one_piece", **_model_options())
        assert generation.outfit_category == "feet"
        assert "pair of shoes" in generation.rendered_prompt
        assert "ONE-PIECE garment" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_rejects_unknown_pose_and_falls_back_for_unknown_background(
        db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-model-invalid", "manager")
    try:
        _model_slot(db_session, tmp_path)
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session, staff=user, product=product, variant=variant,
                selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
                **_model_options(pose_key="not-a-pose"))
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            **_model_options(background_key="not-a-place"))
        assert generation.background_key == "starter_ivory"
        assert "Use the shop's fixed studio set" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_product_room_scene_uses_the_shop_default_background(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-bg-room", "manager")
    try:
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="hanger", aspect_ratio="4:5")
        assert generation.background_key == "starter_ivory"
        assert "warm ivory limewash plaster" in generation.rendered_prompt
        assert "Use the shop's fixed studio set" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_product_free_campaign_uses_the_shop_default_background(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-bg-brief", "manager")
    try:
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            scenario="product_brief", user_text="soft morning light")
        assert generation.background_key == "starter_ivory"
        assert "warm ivory limewash plaster" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_specialized_product_scene_stores_no_background(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-bg-packshot", "manager")
    try:
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="packshot", aspect_ratio="4:5",
            background_key="starter_oak")
        assert generation.background_key is None
        assert "پس‌زمینه:" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_accessory_product_rejects_a_hanger_scene(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    product.category = "کفش"
    product.name = "کتانی تست"
    db_session.commit()
    user, _password = _staff(db_session, "ig-shoes-hanger", "manager")
    try:
        with pytest.raises(InstagramValidationError):
            create_generation(
                db_session, staff=user, product=product, variant=variant,
                selected_paths=[paths[0]], scene_key="hanger",
                aspect_ratio="4:5")
        assert db_session.query(InstagramGeneration).count() == 0
    finally:
        _cleanup(root)


def test_set_generation_uses_the_two_hanger_clause(db_session):
    product, variant, paths, root = _make_product_with_images(db_session)
    product.category = "ست"
    product.name = "ست لس"
    db_session.commit()
    user, _password = _staff(db_session, "ig-set-hanger", "manager")
    try:
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="hanger", aspect_ratio="4:5")
        assert "clip hanger" in generation.rendered_prompt
        assert "upper-body piece" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_brand_background_stores_the_key_and_drops_the_lighting_section(
        db_session, tmp_path):
    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-bg-model-brand", "manager")
    try:
        _model_slot(db_session, tmp_path)
        generation, _job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            **_model_options(background_key="starter_oak",
                             lighting_key="golden_hour"))
        assert generation.background_key == "starter_oak"
        assert "warm off-white plaster" in generation.rendered_prompt
        assert "Warm golden-hour light" not in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_text_scene_needs_no_product_or_references(db_session):
    import json

    user, _password = _staff(db_session, "ig-text-maker", "manager")
    generation, job = create_generation(
        db_session, staff=user, product=None, variant=None, selected_paths=[],
        scene_key="", aspect_ratio="4:5", scenario="text",
        user_text="cozy autumn campaign with warm knit layers")
    payload = json.loads(job.payload)
    assert payload["reference_paths"] == []
    assert generation.scenario == "text"
    assert generation.product_id is None
    assert generation.variant_id is None
    assert generation.garment_mode is None
    assert "cozy autumn campaign" in generation.rendered_prompt
    assert "garment" not in generation.rendered_prompt.lower()


def test_text_scene_requires_a_ten_character_brief(db_session):
    user, _password = _staff(db_session, "ig-text-short", "manager")
    with pytest.raises(InstagramValidationError):
        create_generation(
            db_session, staff=user, product=None, variant=None, selected_paths=[],
            scene_key="", aspect_ratio="4:5", scenario="text", user_text="too short")
    assert db_session.query(InstagramGeneration).count() == 0


def test_variation_payload_reuses_the_garment_inputs(db_session):
    import json

    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-variation-garment", "manager")
    try:
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0], paths[1]], scene_key="back_view",
            aspect_ratio="1:1", user_text="soft morning light", logo=True,
            logo_path="static/uploads/logo.png")
        variation = variation_payload(generation, json.loads(job.payload))
        assert variation["scenario"] == "garment"
        assert variation["product_id"] == product.id
        assert variation["variant_id"] == variant.id
        assert variation["image_paths"] == paths[:2]
        assert variation["garment_mode"] == "back_view"
        assert variation["aspect_ratio"] == "1:1"
        assert variation["user_text"] == "soft morning light"
        assert variation["logo"] is True
        assert variation["model_slot"] == ""
    finally:
        _cleanup(root)


def test_variation_payload_skips_the_model_reference_and_keeps_the_controls(
        db_session, tmp_path):
    import json

    product, variant, paths, root = _make_product_with_images(db_session)
    user, _password = _staff(db_session, "ig-variation-model", "manager")
    try:
        _model_slot(db_session, tmp_path, slot_key="boy_7")
        generation, job = create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            scenario="model_scene", model_slot="boy_7", pose_key="seated_bench",
            background_key="park", framing_key="waist_up",
            lighting_key="golden_hour", composition_key="candid_lifestyle",
            show_full_garment=False)
        payload = json.loads(job.payload)
        assert payload["framing_key"] == "waist_up"
        assert payload["lighting_key"] == "golden_hour"
        assert payload["composition_key"] == "candid_lifestyle"
        assert payload["show_full_garment"] is False
        variation = variation_payload(generation, payload)
        assert variation["image_paths"] == paths[:1]
        assert variation["model_slot"] == "boy_7"
        assert variation["pose_key"] == "seated_bench"
        assert variation["framing_key"] == "waist_up"
        assert variation["show_full_garment"] is False
    finally:
        _cleanup(root)


def test_variation_payload_for_text_has_no_references(db_session):
    import json

    user, _password = _staff(db_session, "ig-variation-text", "manager")
    generation, job = create_generation(
        db_session, staff=user, product=None, variant=None, selected_paths=[],
        scene_key="", aspect_ratio="9:16", scenario="text",
        user_text="cozy autumn campaign with warm knit layers")
    variation = variation_payload(generation, json.loads(job.payload))
    assert variation["scenario"] == "text"
    assert variation["image_paths"] == []
    assert variation["aspect_ratio"] == "9:16"
    assert variation["user_text"] == "cozy autumn campaign with warm knit layers"


def test_text_image_scene_uses_the_uploaded_photo(db_session, tmp_path):
    import json

    source = tmp_path / "campaign.jpg"
    Image.new("RGB", (120, 120), (150, 120, 90)).save(source, "JPEG")
    user, _password = _staff(db_session, "ig-text-image-maker", "manager")
    generation, job = create_generation(
        db_session, staff=user, product=None, variant=None, selected_paths=[],
        scene_key="", aspect_ratio="4:5", scenario="text_image",
        user_text="warm autumn window light", source_image_path=str(source))
    payload = json.loads(job.payload)
    assert generation.scenario == "text_image"
    assert generation.product_id is None
    assert generation.variant_id is None
    assert payload["reference_paths"] == [str(source)]
    assert payload["temporary_paths"] == [str(source)]
    assert "Edit the supplied reference image" in generation.rendered_prompt
    assert "warm autumn window light" in generation.rendered_prompt
    # The worker removes it after the paid request; creation must not.
    assert source.exists()


def test_text_image_scene_needs_a_photo_and_a_ten_character_brief(db_session, tmp_path):
    source = tmp_path / "campaign.jpg"
    Image.new("RGB", (120, 120), (150, 120, 90)).save(source, "JPEG")
    user, _password = _staff(db_session, "ig-text-image-short", "manager")
    with pytest.raises(InstagramValidationError):
        create_generation(
            db_session, staff=user, product=None, variant=None, selected_paths=[],
            scene_key="", aspect_ratio="4:5", scenario="text_image",
            user_text="warm autumn window light", source_image_path="")
    with pytest.raises(InstagramValidationError):
        create_generation(
            db_session, staff=user, product=None, variant=None, selected_paths=[],
            scene_key="", aspect_ratio="4:5", scenario="text_image",
            user_text="short", source_image_path=str(source))
    assert db_session.query(InstagramGeneration).count() == 0
