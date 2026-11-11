"""Instagram Studio routes: role gate, enqueue, result, download."""
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from models import (BackgroundJob, InstagramGeneration, Product, ProductVariant,
                    Settings, ShopModel, VariantImage)
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def _make_variant(db_session, images=2):
    product = Product(name=f"تیشرت اینستاگرام {uuid.uuid4().hex[:4]}")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100_000,
                             barcode=f"IGR{uuid.uuid4().hex[:6]}", color="آبی")
    db_session.add(variant)
    db_session.flush()
    root = Path("static/uploads/ig_test") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(images):
        path = root / f"ref{i}.jpg"
        Image.new("RGB", (80, 80), (20 * i, 80, 120)).save(path, "JPEG")
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


def _make_shop_model(db_session, tmp_path, slot_key="girl_7"):
    path = tmp_path / f"{slot_key}.jpg"
    Image.new("RGB", (120, 120), (140, 170, 200)).save(path, "JPEG")
    model = ShopModel(slot_key=slot_key, label_fa="دختر ۷ ساله", image_path=str(path))
    db_session.add(model)
    db_session.commit()
    return model


def _login(client, db_session, role="manager", username=None):
    user, password = _staff(db_session, username or f"ig-{role}", role)
    _session_as(client, user, password)
    return user


def _store_logo(db_session) -> Path:
    folder = Path("static/uploads/instagram")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"logo_{uuid.uuid4().hex[:8]}.png"
    Image.new("RGB", (60, 60), (255, 0, 0)).save(path)
    db_session.add(Settings(key="owner_logo_path", value="/" + str(path)))
    db_session.commit()
    return path


def _seed_key(db_session):
    db_session.add_all([
        Settings(key="tryon_provider", value="avalai"),
        Settings(key="tryon_model", value="seedream-5-0-260128"),
        Settings(key="instagram_generation_model", value="qwen-image-3.0-pro"),
        Settings(key="tryon_api_key_avalai", value="sk-test-key"),
    ])
    db_session.commit()


def _post_generate(client, product, variant, paths, **overrides):
    token = csrf_token(client, "/admin/instagram-studio")
    data = {
        "csrf_token": token,
        "product_id": str(product.id),
        "variant_id": str(variant.id),
        "image_paths": paths[:3],
        "garment_mode": "flat_lay",
        "aspect_ratio": "4:5",
        "user_text": "soft morning light",
    }
    data.update(overrides)
    return client.post("/admin/instagram-studio/generate", data=data,
                       follow_redirects=False)


def test_cashier_cannot_open_the_studio(client, db_session):
    _login(client, db_session, "cashier", "ig-cashier")
    assert client.get("/admin/instagram-studio", follow_redirects=False).status_code == 403


def test_manager_opens_the_empty_studio(client, db_session):
    _login(client, db_session, "manager", "ig-manager-open")
    page = client.get("/admin/instagram-studio")
    assert page.status_code == 200
    assert "استودیو اینستاگرام" in page.text
    assert "جستجوی محصول" in page.text


def test_generate_creates_one_generation_and_one_job(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-generate")
    _seed_key(db_session)
    try:
        response = _post_generate(client, product, variant, paths)
        assert response.status_code == 303, response.text[:300]
        assert "job_id=" in response.headers["location"]

        generation = db_session.query(InstagramGeneration).one()
        job = db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == "instagram_image").one()
        assert generation.job_id == job.id
        assert generation.product_id == product.id
        assert generation.variant_id == variant.id
        assert generation.garment_mode == "flat_lay"
        assert generation.aspect_ratio == "4:5"
        assert generation.rendered_prompt
        assert job.status == "pending"
    finally:
        _cleanup(root)


def test_generate_rejects_a_variant_without_images(client, db_session):
    product, variant, paths, root = _make_variant(db_session, images=0)
    _login(client, db_session, "manager", "ig-noimage")
    _seed_key(db_session)
    try:
        response = _post_generate(client, product, variant, [])
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 0
    finally:
        _cleanup(root)


def test_a_second_click_on_a_pending_generation_is_refused(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-duplicate")
    _seed_key(db_session)
    try:
        first = _post_generate(client, product, variant, paths)
        assert first.status_code == 303 and "job_id=" in first.headers["location"]

        second = _post_generate(client, product, variant, paths)
        assert second.status_code == 303
        assert "err=" in second.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 1
        assert db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == "instagram_image").count() == 1
    finally:
        _cleanup(root)


def test_generate_product_brief_creates_a_brief_generation(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-brief")
    _seed_key(db_session)
    try:
        response = _post_generate(
            client, product, variant, paths,
            scenario="product_brief", garment_mode="",
            user_text="soft morning light, minimal beige studio",
        )
        assert response.status_code == 303, response.text[:300]
        assert "job_id=" in response.headers["location"]

        generation = db_session.query(InstagramGeneration).one()
        assert generation.scenario == "product_brief"
        assert generation.garment_mode is None
        assert generation.background_key == "starter_ivory"
        assert "soft morning light, minimal beige studio" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_duplicate_guard_separates_product_backgrounds(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-duplicate-bg")
    _seed_key(db_session)
    try:
        first = _post_generate(
            client, product, variant, paths,
            garment_mode="hanger", background_key="starter_ivory")
        assert first.status_code == 303 and "job_id=" in first.headers["location"]

        second = _post_generate(
            client, product, variant, paths,
            garment_mode="hanger", background_key="starter_oak")
        assert second.status_code == 303
        assert "job_id=" in second.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 2
    finally:
        _cleanup(root)


def test_product_brief_tab_shows_the_brief_fields_and_variant_details(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    variant.tryon_details = "دو تکه؛ آستین کوتاه"
    db_session.commit()
    _login(client, db_session, "manager", "ig-brief-tab")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=product_brief"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert "کمپین با توضیح" in page.text
        assert "بدون صحنه — کمپین آزاد" in page.text
        assert 'minlength="10"' in page.text
        assert "دو تکه؛ آستین کوتاه" in page.text
    finally:
        _cleanup(root)


def test_generate_product_brief_rejects_a_short_brief(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-brief-short")
    _seed_key(db_session)
    try:
        response = _post_generate(
            client, product, variant, paths,
            scenario="product_brief", garment_mode="", user_text="کوتاه")
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 0
    finally:
        _cleanup(root)



def test_variant_details_save_reaches_the_instagram_prompt(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-variant-details")
    _seed_key(db_session)
    try:
        edit_page = client.get(f"/admin/variants/{variant.id}/edit")
        assert 'name="tryon_details"' in edit_page.text
        assert "استودیو اینستاگرام" in edit_page.text

        token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
        saved = client.post(f"/admin/variants/{variant.id}", data={
            "csrf_token": token,
            "price": "100000",
            "stock_quantity": "0",
            "tryon_details": "دو تکه؛ آستین کوتاه",
        }, follow_redirects=False)
        assert saved.status_code == 303
        db_session.expire_all()
        variant = db_session.get(ProductVariant, variant.id)
        assert variant.tryon_details == "دو تکه؛ آستین کوتاه"

        generated = _post_generate(client, product, variant, paths)
        assert "job_id=" in generated.headers["location"]
        generation = (db_session.query(InstagramGeneration)
                      .order_by(InstagramGeneration.id.desc()).first())
        assert "دو تکه؛ آستین کوتاه" in generation.rendered_prompt
    finally:
        _cleanup(root)


def test_model_scene_tab_shows_the_model_picker_and_controls(client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    model = _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-model-tab")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert "مدل و ژست" in page.text
        assert model.label_fa in page.text
        assert 'name="model_slot"' in page.text
        for field in ("pose_key", "background_key", "framing_key",
                      "lighting_key", "composition_key", "show_full_garment"):
            assert f'name="{field}"' in page.text
    finally:
        _cleanup(root)


def test_model_scene_card_shows_the_outfit_override_for_clothing(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-outfit-select")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert 'name="outfit_category"' in page.text
        assert "قاعده ست" in page.text
        assert "خودکار — بالاتنه" in page.text
    finally:
        _cleanup(root)


def test_model_scene_card_hides_the_outfit_override_for_accessories(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    product.category = "کفش"
    product.name = "کتانی تست"
    db_session.commit()
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-outfit-shoes")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert 'name="outfit_category"' not in page.text
        assert "قاعده پوشش این محصول خودکار است (کفش)" in page.text
    finally:
        _cleanup(root)


def test_model_scene_prompt_preview_uses_the_outfit_override(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-outfit-preview")
    try:
        response = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "model_scene"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("model_slot", "girl_7"),
                ("outfit_category", "one_piece"),
                ("aspect_ratio", "4:5"),
            ])
        assert response.status_code == 200
        assert "قاعده ست کامل" in response.text
        assert "ONE-PIECE garment" in response.text
        assert "UPPER-BODY piece" not in response.text
    finally:
        _cleanup(root)


def test_model_scene_without_model_images_tells_the_manager(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-model-empty")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert "عکس مرجعی برای مدل‌ها ثبت نشده" in page.text
    finally:
        _cleanup(root)


def test_studio_preselects_the_default_brand_background(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-bg-default")
    try:
        page = client.get(
            f"/admin/instagram-studio?product_id={product.id}"
            f"&variant_id={variant.id}")
        assert page.status_code == 200
        assert ('data-room-background-scenes='
                '"hanger,wooden_hanger_wall,folded_stack"') in page.text
        assert 'value="starter_ivory"' in page.text
        assert "selected>استودیو کرم" in page.text
        assert "دیوار کرم، کف چوب روشن، نور نرم پنجره" in page.text
        assert "/static/js/instagram_backgrounds.js" in page.text
    finally:
        _cleanup(root)


def test_model_scene_lists_brand_backgrounds_before_generic_ones(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-bg-model")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert page.text.index("استودیو کرم") < page.text.index("پارک")
        assert 'data-source="brand"' in page.text
        assert 'data-source="generic"' in page.text
        assert "data-lighting-field hidden" in page.text
    finally:
        _cleanup(root)


def test_accessory_product_hides_hanger_scenes_and_preselects_flat_lay(
        client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    product.category = "کفش"
    product.name = "کتانی تست"
    db_session.commit()
    _login(client, db_session, "manager", "ig-accessory")
    try:
        page = client.get(
            f"/admin/instagram-studio?product_id={product.id}"
            f"&variant_id={variant.id}")
        assert page.status_code == 200
        assert "پیشنهاد برای این محصول (کفش)" in page.text
        assert "آویزان روی رگال" not in page.text
        assert 'value="flat_lay" selected' in page.text
        assert "رگال و مانکن مناسب نیست" in page.text
        assert "data-background-field hidden" in page.text
    finally:
        _cleanup(root)


def test_set_product_preselects_hanger_and_explains_both_hangers(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    product.category = "ست"
    product.name = "ست لس"
    db_session.commit()
    _login(client, db_session, "manager", "ig-set")
    try:
        page = client.get(
            f"/admin/instagram-studio?product_id={product.id}"
            f"&variant_id={variant.id}")
        assert page.status_code == 200
        assert "پیشنهاد برای این محصول (ست)" in page.text
        assert "بالاتنه روی رگال شانه‌ای و پایین‌تنه روی رگال گیره‌ای" in page.text
        assert 'value="hanger" selected' in page.text
    finally:
        _cleanup(root)


def test_product_brief_suggests_the_product_shot_tab(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    product.category = "ست"
    product.name = "ست لس"
    db_session.commit()
    _login(client, db_session, "manager", "ig-brief-recommend")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=product_brief"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert "پیشنهاد برای این محصول (ست)" in page.text
        assert "scenario=garment" in page.text
        assert "عکاسی محصول" in page.text
    finally:
        _cleanup(root)


def test_generate_model_scene_creates_a_generation(client, db_session, tmp_path):
    import json

    product, variant, paths, root = _make_variant(db_session)
    model = _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-model-generate")
    _seed_key(db_session)
    try:
        response = _post_generate(
            client, product, variant, paths,
            scenario="model_scene", garment_mode="", model_slot="girl_7",
            pose_key="seated_bench", background_key="park",
            framing_key="full_body", lighting_key="golden_hour",
            composition_key="candid_lifestyle",
            show_full_garment="1", user_text="soft morning light")
        assert response.status_code == 303, response.text[:300]
        assert "job_id=" in response.headers["location"]

        generation = db_session.query(InstagramGeneration).one()
        assert generation.scenario == "model_scene"
        assert generation.model_slot == "girl_7"
        assert generation.pose_key == "seated_bench"
        assert generation.background_key == "park"
        job = db_session.get(BackgroundJob, generation.job_id)
        payload = json.loads(job.payload)
        assert payload["reference_paths"][0] == model.image_path
        assert payload["reference_paths"][1:] == [path.lstrip("/") for path in paths[:1]]
    finally:
        _cleanup(root)


def test_generate_model_scene_stores_the_outfit_override(client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-model-outfit-generate")
    _seed_key(db_session)
    try:
        response = _post_generate(
            client, product, variant, paths,
            scenario="model_scene", garment_mode="", model_slot="girl_7",
            outfit_category="one_piece", pose_key="standing_front",
            background_key="studio_white", framing_key="full_body",
            lighting_key="soft_daylight",
            composition_key="centered_catalog", show_full_garment="1")
        assert response.status_code == 303, response.text[:300]
        generation = db_session.query(InstagramGeneration).one()
        assert generation.outfit_category == "one_piece"
        assert "ONE-PIECE garment" in generation.rendered_prompt
        assert "UPPER-BODY piece" not in generation.rendered_prompt

        job = db_session.get(BackgroundJob, generation.job_id)
        job.status = "completed"
        job.result_url = "/static/uploads/instagram/fake.jpg"
        db_session.commit()
        partial = client.get(f"/admin/instagram-studio/jobs/{job.id}",
                             headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert 'name="outfit_category" value="one_piece"' in partial.text
    finally:
        _cleanup(root)


def _post_text(client, **overrides):
    token = csrf_token(client, "/admin/instagram-studio?scenario=text")
    data = {
        "csrf_token": token,
        "scenario": "text",
        "aspect_ratio": "4:5",
        "user_text": "cozy autumn campaign with warm knit layers",
    }
    data.update(overrides)
    return client.post("/admin/instagram-studio/generate", data=data,
                       follow_redirects=False)


def test_text_scene_tab_works_without_a_product(client, db_session):
    _login(client, db_session, "manager", "ig-text-tab")
    page = client.get("/admin/instagram-studio?scenario=text")
    assert page.status_code == 200
    assert "کمپین متنی" in page.text
    assert 'name="user_text"' in page.text
    assert 'minlength="10"' in page.text
    assert 'name="aspect_ratio"' in page.text
    assert 'name="product_id"' not in page.text


def test_generate_text_scene_creates_a_reference_free_job(client, db_session):
    import json

    _login(client, db_session, "manager", "ig-text-generate")
    _seed_key(db_session)
    response = _post_text(client)
    assert response.status_code == 303, response.text[:300]
    assert "job_id=" in response.headers["location"]

    generation = db_session.query(InstagramGeneration).one()
    assert generation.scenario == "text"
    assert generation.product_id is None
    assert generation.variant_id is None
    assert generation.provider == "avalai"
    assert generation.model == "qwen-image-3.0-pro"
    assert "cozy autumn campaign" in generation.rendered_prompt
    job = db_session.get(BackgroundJob, generation.job_id)
    assert json.loads(job.payload)["reference_paths"] == []


def test_generate_text_scene_refuses_a_short_brief(client, db_session):
    _login(client, db_session, "manager", "ig-text-short")
    _seed_key(db_session)
    response = _post_text(client, user_text="short")
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert db_session.query(InstagramGeneration).count() == 0




def test_studio_reload_keeps_the_completed_and_failed_result_actions(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-result-reload")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        job.status = "completed"
        job.result_url = "/static/uploads/instagram/fake.jpg"
        db_session.commit()

        url = (f"/admin/instagram-studio?product_id={product.id}"
               f"&variant_id={variant.id}&job_id={job.id}")
        page = client.get(url)
        assert page.status_code == 200
        assert "تولید تنوع" in page.text

        job.status = "failed"
        job.error_message = "Provider refused"
        db_session.commit()
        page = client.get(url)
        assert page.status_code == 200
        assert "تلاش دوباره" in page.text
        assert f'href="/admin/instagram-studio?product_id={product.id}' in page.text
    finally:
        _cleanup(root)


def test_studio_shows_usage_windows(client, db_session):
    _login(client, db_session, "manager", "ig-usage")
    page = client.get("/admin/instagram-studio")
    assert page.status_code == 200
    assert "۷ روز گذشته" in page.text
    assert "۳۰ روز گذشته" in page.text






def test_generate_rejects_when_daily_quota_is_exhausted(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-quota")
    _seed_key(db_session)
    try:
        today = datetime.now(timezone.utc)
        db_session.add_all([
            BackgroundJob(job_type="instagram_image", payload="{}", status="pending",
                          created_at=today)
            for _ in range(20)
        ])
        db_session.commit()
        response = _post_generate(client, product, variant, paths)
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 0
    finally:
        _cleanup(root)


def test_job_partial_shows_a_completed_image(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-history")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        out_dir = Path("static/uploads/instagram")
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"test_{uuid.uuid4().hex[:8]}.jpg"
        output = out_dir / filename
        Image.new("RGB", (80, 80), (90, 120, 150)).save(output, "JPEG")
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.result_path = str(output)
        job.result_url = f"/static/uploads/instagram/{filename}"
        db_session.commit()

        partial = client.get(f"/admin/instagram-studio/jobs/{job.id}",
                             headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert f"/static/uploads/instagram/{filename}" in partial.text
        assert "دانلود" in partial.text
    finally:
        output.unlink(missing_ok=True)
        _cleanup(root)




def test_download_serves_the_clean_image(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-download")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        out_dir = Path("static/uploads/instagram")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"download_{uuid.uuid4().hex[:8]}.jpg"
        Image.new("RGB", (80, 80), (10, 20, 30)).save(output, "JPEG")
        job.status = "completed"
        job.result_path = str(output)
        job.result_url = f"/static/uploads/instagram/{output.name}"
        db_session.commit()

        response = client.get(f"/admin/instagram-studio/generations/{generation.id}/download")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
        assert response.content
    finally:
        output.unlink(missing_ok=True)
        _cleanup(root)


def test_logo_checkbox_is_on_every_scenario_tab(client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    logo = _store_logo(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-logo-tabs")
    try:
        for scenario in ("text", "text_image"):
            page = client.get(f"/admin/instagram-studio?scenario={scenario}")
            assert page.status_code == 200, scenario
            assert 'name="logo"' in page.text, scenario

        for scenario in ("garment", "product_brief", "model_scene"):
            page = client.get(
                f"/admin/instagram-studio?scenario={scenario}"
                f"&product_id={product.id}&variant_id={variant.id}")
            assert page.status_code == 200, scenario
            assert 'name="logo"' in page.text, scenario
    finally:
        logo.unlink(missing_ok=True)
        _cleanup(root)


def test_logo_download_uses_tryon_placement_and_size(client, db_session):
    import io

    product, variant, paths, root = _make_variant(db_session)
    logo = _store_logo(db_session)
    _login(client, db_session, "manager", "ig-logo-download")
    _seed_key(db_session)
    out_dir = Path("static/uploads/instagram")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"logo_{uuid.uuid4().hex[:8]}.jpg"
    try:
        assert _post_generate(client, product, variant, paths,
                              logo="1").status_code == 303
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        Image.new("RGB", (400, 400), (255, 255, 255)).save(output, "JPEG")
        job.status = "completed"
        job.result_path = str(output)
        job.result_url = f"/static/uploads/instagram/{output.name}"
        db_session.commit()

        partial = client.get(f"/admin/instagram-studio/jobs/{job.id}",
                             headers={"HX-Request": "true"})
        assert "?logo=yes" in partial.text
        assert "دانلود با لوگو" in partial.text
        # The checked generation's primary download carries the logo.
        assert partial.text.index("?logo=yes") < partial.text.index("دانلود بدون لوگو")

        badged = client.get(
            f"/admin/instagram-studio/generations/{generation.id}/download?logo=yes")
        assert badged.headers["content-type"].startswith("image/png")
        image = Image.open(io.BytesIO(badged.content)).convert("RGB")
        assert image.getpixel((5, 5)) == (255, 0, 0)
        assert image.getpixel((229, 5)) == (255, 0, 0)   # same 230px width as try-on
        assert image.getpixel((231, 5)) == (255, 255, 255)

        clean = client.get(f"/admin/instagram-studio/generations/{generation.id}/download")
        assert clean.headers["content-type"].startswith("image/jpeg")
        assert Image.open(io.BytesIO(clean.content)).convert("RGB").getpixel((5, 5)) == (255, 255, 255)
    finally:
        output.unlink(missing_ok=True)
        logo.unlink(missing_ok=True)
        _cleanup(root)


def test_history_and_favorite_endpoints_are_gone(client, db_session):
    _login(client, db_session, "manager", "ig-history-removed")
    assert client.get("/admin/instagram-studio/history").status_code == 404
    token = csrf_token(client, "/admin/instagram-studio")
    response = client.post(
        "/admin/instagram-studio/history/1/favorite",
        data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 404


def test_variant_search_finds_by_name_and_barcode_and_hides_no_photo(
        client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-name")
    try:
        hidden = ProductVariant(product_id=product.id, price=100_000,
                                barcode=f"IGH{uuid.uuid4().hex[:6]}", color="قرمز")
        db_session.add(hidden)
        db_session.commit()

        items = client.get("/admin/instagram-studio/variants",
                           params={"q": "تیشرت"}).json()["items"]
        assert [item["variant_id"] for item in items] == [variant.id]
        assert items[0]["label"] == variant.display_name
        assert items[0]["barcode"] == variant.barcode
        assert items[0]["stock"] == variant.available_quantity

        by_code = client.get("/admin/instagram-studio/variants",
                             params={"q": variant.barcode}).json()["items"]
        assert by_code[0]["variant_id"] == variant.id
        assert by_code[0]["exact"] is True

        # Colour and size are not search fields by design.
        assert client.get("/admin/instagram-studio/variants",
                          params={"q": "آبی"}).json()["items"] == []

        fragment = client.get("/admin/instagram-studio/variants",
                              params={"q": "تیشرت"},
                              headers={"HX-Request": "true"})
        assert fragment.status_code == 200
        assert 'data-variant-id="' in fragment.text
        assert "موجودی" in fragment.text
    finally:
        _cleanup(root)


def test_variant_search_ranks_an_exact_barcode_before_its_prefix(
        client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-rank")
    try:
        longer = ProductVariant(product_id=product.id, price=100_000,
                                barcode=variant.barcode + "X", color="سبز")
        db_session.add(longer)
        db_session.flush()
        path = root / "longer.jpg"
        Image.new("RGB", (80, 80), (10, 90, 40)).save(path, "JPEG")
        db_session.add(VariantImage(variant_id=longer.id,
                                    image_path="/" + str(path), sort_order=0))
        db_session.commit()

        items = client.get("/admin/instagram-studio/variants",
                           params={"q": variant.barcode}).json()["items"]
        assert items[0]["variant_id"] == variant.id
        assert items[0]["exact"] is True
        assert longer.id in [item["variant_id"] for item in items]
    finally:
        _cleanup(root)


def test_variant_search_caps_the_list_at_five(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-five")
    try:
        for index in range(6):
            extra = ProductVariant(
                product_id=product.id, price=100_000,
                barcode=f"IGE{uuid.uuid4().hex[:8]}", color=f"رنگ {index}")
            db_session.add(extra)
            db_session.flush()
            path = root / f"extra{index}.jpg"
            Image.new("RGB", (80, 80), (index * 10, 20, 90)).save(path, "JPEG")
            db_session.add(VariantImage(variant_id=extra.id,
                                        image_path="/" + str(path), sort_order=0))

        db_session.commit()
        items = client.get("/admin/instagram-studio/variants",
                           params={"q": "تیشرت"}).json()["items"]
        assert len(items) == 5
    finally:
        _cleanup(root)


def test_variant_search_normalizes_persian_digits(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-digits")
    try:
        variant.barcode = "IG12345"
        db_session.commit()
        items = client.get("/admin/instagram-studio/variants",
                           params={"q": "IG۱۲۳۴۵"}).json()["items"]
        assert items[0]["variant_id"] == variant.id
        assert items[0]["exact"] is True
    finally:
        _cleanup(root)


def test_variant_search_is_manager_only_and_needs_two_characters(
        client, db_session):
    _login(client, db_session, "cashier", "ig-vs-cashier")
    assert client.get("/admin/instagram-studio/variants",
                      params={"q": "ab"}).status_code == 403

    _login(client, db_session, "manager", "ig-vs-short")
    assert client.get("/admin/instagram-studio/variants",
                      params={"q": "a"}).json() == {"items": []}


def test_variant_card_returns_the_selected_variant(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-card")
    try:
        page = client.get("/admin/instagram-studio/variant-card", params={
            "scenario": "garment", "product_id": product.id,
            "variant_id": variant.id})
        assert page.status_code == 200
        assert 'id="ig-variant-card"' in page.text
        assert f'name="product_id" value="{product.id}"' in page.text
        assert f'name="variant_id" value="{variant.id}"' in page.text
        assert paths[0] in page.text
        assert 'name="csrf_token"' in page.text

        missing = client.get("/admin/instagram-studio/variant-card", params={
            "scenario": "garment", "product_id": product.id,
            "variant_id": variant.id + 999})
        assert missing.status_code == 404
    finally:
        _cleanup(root)


def test_variant_card_model_scene_carries_the_model_controls(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-vs-card-model")
    try:
        page = client.get("/admin/instagram-studio/variant-card", params={
            "scenario": "model_scene", "product_id": product.id,
            "variant_id": variant.id})
        assert page.status_code == 200
        assert 'name="model_slot"' in page.text
        assert 'name="pose_key"' in page.text
    finally:
        _cleanup(root)


def test_studio_uses_the_variant_combobox_instead_of_chips(client, db_session):
    _login(client, db_session, "manager", "ig-vs-page")
    page = client.get("/admin/instagram-studio")
    assert 'id="ig-variant-search"' in page.text
    assert 'role="combobox"' in page.text
    assert 'hx-get="/admin/instagram-studio/variants"' in page.text
    assert '<form method="get" action="/admin/instagram-studio"' not in page.text


def test_studio_prefills_the_selected_variant(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-vs-prefill")
    try:
        page = client.get(
            f"/admin/instagram-studio?product_id={product.id}"
            f"&variant_id={variant.id}")
        assert f'value="{variant.display_name}"' in page.text
    finally:
        _cleanup(root)


def _post_text_image(client, tmp_path, **overrides):
    source = tmp_path / "campaign.jpg"
    Image.new("RGB", (100, 100), (120, 90, 60)).save(source, "JPEG")
    token = csrf_token(client, "/admin/instagram-studio?scenario=text_image")
    data = {
        "csrf_token": token,
        "scenario": "text_image",
        "aspect_ratio": "4:5",
        "user_text": "warm autumn window light, clean negative space",
    }
    data.update(overrides)
    with source.open("rb") as handle:
        return client.post(
            "/admin/instagram-studio/generate", data=data,
            files={"reference_image": ("campaign.jpg", handle, "image/jpeg")},
            follow_redirects=False)


def test_text_image_tab_offers_a_file_picker_and_no_product(client, db_session):
    _login(client, db_session, "manager", "ig-text-image-tab")
    page = client.get("/admin/instagram-studio?scenario=text_image")
    assert page.status_code == 200
    assert "کمپین متنی با عکس" in page.text
    assert 'name="reference_image"' in page.text
    assert 'data-image-field' in page.text
    assert 'enctype="multipart/form-data"' in page.text
    assert 'name="user_text"' in page.text
    assert 'name="product_id"' not in page.text
    assert 'id="ig-variant-search"' not in page.text


def test_generate_text_image_uses_the_edit_model_with_the_upload(client, db_session,
                                                                tmp_path):
    import json

    _login(client, db_session, "manager", "ig-text-image-generate")
    _seed_key(db_session)
    response = _post_text_image(client, tmp_path)
    assert response.status_code == 303, response.text[:300]
    assert "job_id=" in response.headers["location"]

    generation = db_session.query(InstagramGeneration).one()
    assert generation.scenario == "text_image"
    assert generation.product_id is None
    assert generation.model == "seedream-5-0-260128"
    assert "warm autumn window light" in generation.rendered_prompt
    job = db_session.get(BackgroundJob, generation.job_id)
    payload = json.loads(job.payload)
    source = Path(payload["reference_paths"][0])
    assert payload["temporary_paths"] == [str(source)]
    assert source.exists()
    source.unlink(missing_ok=True)


def test_generate_text_image_rejects_a_file_that_is_not_an_image(client, db_session):
    _login(client, db_session, "manager", "ig-text-image-bad")
    _seed_key(db_session)
    token = csrf_token(client, "/admin/instagram-studio?scenario=text_image")
    response = client.post(
        "/admin/instagram-studio/generate",
        data={"csrf_token": token, "scenario": "text_image", "aspect_ratio": "4:5",
              "user_text": "warm autumn window light, clean negative space"},
        files={"reference_image": ("notes.txt", b"not an image", "text/plain")},
        follow_redirects=False)
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert db_session.query(InstagramGeneration).count() == 0


def test_prompt_preview_labels_the_selected_options(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-preview")
    try:
        response = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "garment"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("garment_mode", "packshot"),
                ("aspect_ratio", "1:1"),
                ("user_text", "soft morning light"),
            ])
        assert response.status_code == 200
        assert "حالت عکاسی: پکشات یکدست" in response.text
        assert "نسبت تصویر: ۱:۱ (مربع)" in response.text
        assert "انتخاب شما" in response.text
        assert "متن شما" in response.text
        assert "ثابت" in response.text
        assert "soft morning light" in response.text
    finally:
        _cleanup(root)


def test_prompt_preview_includes_the_resolved_background(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-preview-bg")
    try:
        response = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "garment"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("garment_mode", "hanger"),
                ("background_key", "starter_oak"),
                ("aspect_ratio", "4:5"),
            ])
        assert response.status_code == 200
        assert "پس‌زمینه: اتاق چوبی گرم" in response.text
        assert "fixed studio set" in response.text
    finally:
        _cleanup(root)


def test_prompt_preview_rejects_a_hanger_scene_for_shoes(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    product.category = "کفش"
    product.name = "کتانی تست"
    db_session.commit()
    _login(client, db_session, "manager", "ig-preview-shoes")
    try:
        response = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "garment"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("garment_mode", "hanger"),
                ("aspect_ratio", "4:5"),
            ])
        assert response.status_code == 200
        assert "این حالت عکاسی برای این نوع محصول مناسب نیست" in response.text
    finally:
        _cleanup(root)


def test_prompt_preview_uses_only_the_scene_references(client, db_session):
    product, variant, paths, root = _make_variant(db_session, images=2)
    _login(client, db_session, "manager", "ig-preview-scene-refs")
    try:
        base = [
            ("scenario", "garment"),
            ("product_id", product.id),
            ("variant_id", variant.id),
            ("aspect_ratio", "4:5"),
        ] + [("image_paths", path) for path in paths]

        back = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=base + [("garment_mode", "back_view")])
        assert back.status_code == 200
        assert "Image 2 is the back view" in back.text
        assert "Image 3" not in back.text
    finally:
        _cleanup(root)


def test_prompt_preview_never_names_a_missing_back_photo(client, db_session):
    product, variant, paths, root = _make_variant(db_session, images=1)
    _login(client, db_session, "manager", "ig-preview-missing-views")
    try:
        response = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "garment"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("garment_mode", "back_view"),
                ("aspect_ratio", "4:5"),
            ])
        assert response.status_code == 200
        assert "Image 1 is the primary product photo" in response.text
        # Only main is sent, so no second-image line may be invented.
        assert "Image 2" not in response.text
        assert "is the back view" not in response.text
    finally:
        _cleanup(root)


def test_model_prompt_preview_sends_only_the_pose_references(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session, images=2)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-preview-model-refs")
    try:
        base = [
            ("scenario", "model_scene"),
            ("product_id", product.id),
            ("variant_id", variant.id),
            ("model_slot", "girl_7"),
            ("aspect_ratio", "4:5"),
        ] + [("image_paths", path) for path in paths]

        front = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=base + [("pose_key", "standing_front")])
        assert front.status_code == 200
        assert "Image 2 is the primary product photo" in front.text
        assert "Image 3" not in front.text
        assert "detail close-up" not in front.text

        back = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=base + [("pose_key", "back_view")])
        assert back.status_code == 200
        assert "Image 2 is the primary product photo" in back.text
        assert "Image 3 is the back view" in back.text
        assert "detail close-up" not in back.text
    finally:
        _cleanup(root)


def test_reference_picker_exposes_the_scene_and_pose_views(
        client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session, images=3)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-ref-picker")
    try:
        page = client.get(
            f"/admin/instagram-studio?scenario=garment"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert page.status_code == 200
        assert 'data-supported-views="main,back"' in page.text
        # Main is always sent, so it has no toggle — only the optional slots do.
        assert (f'<input type="hidden" name="image_paths" value="{paths[0]}"'
                in page.text)
        assert "همیشه ارسال می‌شود" in page.text
        assert 'data-view="back"' in page.text

        # What the browser submits for a main-only variant must still reach
        # the prompt as the primary product photo.
        preview = client.get(
            "/admin/instagram-studio/prompt-preview",
            params=[
                ("scenario", "garment"),
                ("product_id", product.id),
                ("variant_id", variant.id),
                ("image_paths", paths[0]),
                ("aspect_ratio", "4:5"),
            ])
        assert preview.status_code == 200
        assert "Image 1 is the primary product photo" in preview.text
        assert 'data-view="detail"' not in page.text
        assert 'data-views="main,back"' in page.text
        assert "data-reference-warning" in page.text

        model_page = client.get(
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}")
        assert model_page.status_code == 200
        assert 'data-views="main,back"' in model_page.text
        assert 'type="hidden" name="show_full_garment" value="1"' in model_page.text
        assert 'type="checkbox" name="show_full_garment"' not in model_page.text
    finally:
        _cleanup(root)


def test_prompt_preview_for_text_image_needs_no_product(client, db_session):
    _login(client, db_session, "manager", "ig-preview-text-image")
    response = client.get(
        "/admin/instagram-studio/prompt-preview",
        params={"scenario": "text_image", "aspect_ratio": "4:5",
                "user_text": "warm autumn window light"})
    assert response.status_code == 200
    assert "عکس مرجع شما" in response.text
    assert "متن کمپین شما" in response.text
    assert "warm autumn window light" in response.text


def test_every_scenario_page_shows_the_prompt_preview(client, db_session, tmp_path):
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-preview-every-scenario")
    try:
        for scenario in ("garment", "product_brief", "model_scene"):
            page = client.get(
                f"/admin/instagram-studio?scenario={scenario}"
                f"&product_id={product.id}&variant_id={variant.id}")
            assert page.status_code == 200, scenario
            assert 'hx-get="/admin/instagram-studio/prompt-preview"' in page.text, scenario
            assert 'id="ig-prompt-preview"' in page.text, scenario
        for scenario in ("text", "text_image"):
            page = client.get(f"/admin/instagram-studio?scenario={scenario}")
            assert page.status_code == 200, scenario
            assert 'hx-get="/admin/instagram-studio/prompt-preview"' in page.text, scenario
            assert 'id="ig-prompt-preview"' in page.text, scenario
    finally:
        _cleanup(root)


def test_preview_load_trigger_is_not_on_the_form(client, db_session, tmp_path):
    """htmx refuses a form's initial load when a required field is empty, which
    left «در حال آماده‌سازی…» forever. The trigger is on a wrapper that includes
    the form, so the first preview always renders."""
    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    _login(client, db_session, "manager", "ig-preview-load")
    try:
        urls = [
            "/admin/instagram-studio?scenario=text",
            "/admin/instagram-studio?scenario=text_image",
            f"/admin/instagram-studio?scenario=product_brief"
            f"&product_id={product.id}&variant_id={variant.id}",
            f"/admin/instagram-studio?scenario=model_scene"
            f"&product_id={product.id}&variant_id={variant.id}",
            f"/admin/instagram-studio?product_id={product.id}"
            f"&variant_id={variant.id}",
        ]
        for url in urls:
            page = client.get(url)
            assert page.status_code == 200, url
            forms = [form for form in re.findall(r"<form\b[^>]*>", page.text, re.S)
                     if "/admin/instagram-studio/generate" in form]
            assert forms, url
            for form in forms:
                assert "hx-get" not in form, url
            assert 'hx-include="closest form"' in page.text, url
            assert 'hx-trigger="load, change, input changed delay:400ms"' in page.text, url
    finally:
        _cleanup(root)


def test_prompt_preview_is_manager_only(client, db_session):
    _login(client, db_session, "cashier", "ig-preview-cashier")
    response = client.get("/admin/instagram-studio/prompt-preview",
                          params={"scenario": "text", "user_text": "hello there"})
    assert response.status_code == 403
