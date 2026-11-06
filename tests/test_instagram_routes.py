"""Instagram Studio routes: role gate, enqueue, history, delete, download."""
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


def _seed_key(db_session):
    db_session.add_all([
        Settings(key="tryon_provider", value="avalai"),
        Settings(key="tryon_model", value="seedream-5-0-260128"),
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
        assert "soft morning light, minimal beige studio" in generation.rendered_prompt
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


def test_history_filters_by_scenario(client, db_session):
    from services.instagram_studio import create_generation

    product, variant, paths, root = _make_variant(db_session)
    user, _password = _staff(db_session, "ig-history-filter", "manager")
    try:
        create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="flat_lay", aspect_ratio="4:5",
            user_text="garment-marker",
        )
        create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            user_text="brief-marker", scenario="product_brief",
        )
        _session_as(client, user, _password)
        page = client.get("/admin/instagram-studio/history?scenario=product_brief")
        assert page.status_code == 200
        assert "brief-marker" in page.text
        assert "garment-marker" not in page.text
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
        assert payload["reference_paths"][1:] == [path.lstrip("/") for path in paths]
    finally:
        _cleanup(root)


def test_history_filter_offers_the_model_scene(client, db_session):
    _login(client, db_session, "manager", "ig-model-history")
    page = client.get("/admin/instagram-studio/history")
    assert page.status_code == 200
    assert 'value="model_scene"' in page.text


def test_history_card_shows_the_model_pose_and_background(client, db_session, tmp_path):
    from services.instagram_studio import create_generation

    product, variant, paths, root = _make_variant(db_session)
    _make_shop_model(db_session, tmp_path)
    user, password = _staff(db_session, "ig-model-history-card", "manager")
    try:
        create_generation(
            db_session, staff=user, product=product, variant=variant,
            selected_paths=[paths[0]], scene_key="", aspect_ratio="4:5",
            scenario="model_scene", model_slot="girl_7", pose_key="seated_bench",
            background_key="park", framing_key="full_body",
            lighting_key="golden_hour", composition_key="candid_lifestyle")
        _session_as(client, user, password)
        page = client.get("/admin/instagram-studio/history?scenario=model_scene")
        assert page.status_code == 200
        assert "مدل و ژست" in page.text
        assert "دختر ۷ ساله" in page.text
        assert "نشسته روی نیمکت" in page.text
        assert "پارک" in page.text
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


def test_history_offers_the_text_scene(client, db_session):
    _login(client, db_session, "manager", "ig-text-history")
    page = client.get("/admin/instagram-studio/history")
    assert page.status_code == 200
    assert 'value="text"' in page.text


def test_history_card_offers_a_variation_form(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-variation-card")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        job.status = "completed"
        db_session.commit()

        page = client.get("/admin/instagram-studio/history")
        assert page.status_code == 200
        assert 'action="/admin/instagram-studio/generate"' in page.text
        assert "تولید تنوع" in page.text
        assert f'value="{product.id}"' in page.text
        assert f'value="{variant.id}"' in page.text
        assert f'value="{paths[0]}"' in page.text
        assert 'name="garment_mode"' in page.text
        assert 'name="aspect_ratio"' in page.text
        assert 'name="user_text"' in page.text
    finally:
        _cleanup(root)


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
        assert 'name="back" value="/admin/instagram-studio?product_id=' in page.text

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


def test_manager_toggles_favorite_and_filters_the_history(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-favorite")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        back = f"/admin/instagram-studio/history?favorite=1"
        token = csrf_token(client, "/admin/instagram-studio/history")
        response = client.post(
            f"/admin/instagram-studio/history/{generation.id}/favorite",
            data={"csrf_token": token, "back": back}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == back
        db_session.refresh(generation)
        assert generation.is_favorite is True

        page = client.get("/admin/instagram-studio/history?favorite=1")
        assert page.status_code == 200
        assert product.name in page.text
        assert 'aria-pressed="true"' in page.text

        token = csrf_token(client, "/admin/instagram-studio/history")
        client.post(
            f"/admin/instagram-studio/history/{generation.id}/favorite",
            data={"csrf_token": token, "back": "/admin/instagram-studio/history"},
            follow_redirects=False)
        db_session.refresh(generation)
        assert generation.is_favorite is False
    finally:
        _cleanup(root)


def test_favorite_toggle_is_manager_only(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-favorite-owner")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()

        _login(client, db_session, "cashier", "ig-favorite-cashier")
        token = csrf_token(client, "/admin")
        response = client.post(
            f"/admin/instagram-studio/history/{generation.id}/favorite",
            data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 403
        db_session.refresh(generation)
        assert generation.is_favorite is False
    finally:
        _cleanup(root)


def test_favorite_toggle_refuses_an_external_return_url(client, db_session):
    _login(client, db_session, "manager", "ig-favorite-redirect")
    _seed_key(db_session)
    product, variant, paths, root = _make_variant(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        token = csrf_token(client, "/admin/instagram-studio/history")
        response = client.post(
            f"/admin/instagram-studio/history/{generation.id}/favorite",
            data={"csrf_token": token, "back": "https://evil.example/"},
            follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/admin/instagram-studio")
    finally:
        _cleanup(root)


def test_history_card_shows_the_text_brief(client, db_session):
    _login(client, db_session, "manager", "ig-text-card")
    _seed_key(db_session)
    _post_text(client)
    page = client.get("/admin/instagram-studio/history?scenario=text")
    assert page.status_code == 200
    assert "کمپین متنی" in page.text
    assert "cozy autumn campaign" in page.text


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


def test_history_and_job_partial_show_a_completed_image(client, db_session):
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

        page = client.get("/admin/instagram-studio/history")
        assert page.status_code == 200
        assert product.name in page.text
        payload = __import__("json").loads(job.payload)
        assert payload["thumbnail_url"] in page.text

        partial = client.get(f"/admin/instagram-studio/jobs/{job.id}",
                             headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert f"/static/uploads/instagram/{filename}" in partial.text
        assert "دانلود" in partial.text
    finally:
        output.unlink(missing_ok=True)
        _cleanup(root)


def test_owner_can_delete_a_completed_generation_and_its_file(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-delete-seed")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        job = db_session.get(BackgroundJob, generation.job_id)
        out_dir = Path("static/uploads/instagram")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"delete_{uuid.uuid4().hex[:8]}.jpg"
        output.write_bytes(b"image")
        job.status = "completed"
        job.result_path = str(output)
        job.result_url = f"/static/uploads/instagram/{output.name}"
        db_session.commit()

        owner, password = _staff(db_session, "ig-owner-delete", "owner")
        _session_as(client, owner, password)
        token = csrf_token(client, "/admin/instagram-studio/history")
        response = client.post(
            f"/admin/instagram-studio/history/{generation.id}/delete",
            data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303
        assert db_session.query(InstagramGeneration).count() == 0
        assert not output.exists()
    finally:
        _cleanup(root)


def test_a_running_generation_cannot_be_deleted(client, db_session):
    product, variant, paths, root = _make_variant(db_session)
    _login(client, db_session, "manager", "ig-delete-running-seed")
    _seed_key(db_session)
    try:
        _post_generate(client, product, variant, paths)
        generation = db_session.query(InstagramGeneration).one()
        owner, password = _staff(db_session, "ig-owner-running", "owner")
        _session_as(client, owner, password)
        token = csrf_token(client, "/admin/instagram-studio/history")
        response = client.post(
            f"/admin/instagram-studio/history/{generation.id}/delete",
            data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303
        assert "err=" in response.headers["location"]
        assert db_session.query(InstagramGeneration).count() == 1
    finally:
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

        response = client.get(f"/admin/instagram-studio/history/{generation.id}/download")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
        assert response.content
    finally:
        output.unlink(missing_ok=True)
        _cleanup(root)
