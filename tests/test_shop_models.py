"""Shop model library: seeding, upload validation, and owner-only settings."""
import io
from pathlib import Path

from PIL import Image

from models import ShopModel
from services import shop_models
from services.shop_models import (
    SHOP_MODEL_SLOTS,
    ensure_shop_models,
    model_age,
    model_gender,
    save_model_image,
)
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def _jpeg(width=600, height=600, color=(180, 200, 220)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _login(client, db_session, role):
    user, password = _staff(db_session, f"models-{role}", role)
    _session_as(client, user, password)
    return user


def test_ensure_shop_models_seeds_the_eight_slots_once(db_session):
    rows = ensure_shop_models(db_session)
    assert [row.slot_key for row in rows] == [key for key, _label in SHOP_MODEL_SLOTS]
    assert [row.label_fa for row in rows] == [label for _key, label in SHOP_MODEL_SLOTS]

    again = ensure_shop_models(db_session)
    assert len(again) == 8
    assert db_session.query(ShopModel).count() == 8


def test_model_age_and_gender_come_from_the_slot_key():
    assert model_age("girl_13") == 13
    assert model_age("boy_2") == 2
    assert model_gender("boy_18") == "boy"
    assert model_gender("girl_7") == "girl"
    assert model_age("unknown") is None


def test_a_valid_upload_is_stored_under_the_model_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(shop_models, "MODEL_DIR", tmp_path / "shop_models")
    url = save_model_image(_jpeg(700, 500), "model.jpg", "image/jpeg")
    assert url.startswith("/static/uploads/shop_models/sm_")
    stored = tmp_path / "shop_models" / url.rsplit("/", 1)[1]
    assert stored.exists()
    assert stored.stat().st_size > 0

    shop_models.delete_model_image(url)
    assert not stored.exists()
    shop_models.delete_model_image(url)  # idempotent


def test_uploads_must_be_a_supported_raster_of_at_least_512px(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(shop_models, "MODEL_DIR", tmp_path / "shop_models")

    with pytest.raises(ValueError):
        save_model_image(_jpeg(100, 100), "small.jpg", "image/jpeg")
    with pytest.raises(ValueError):
        save_model_image(b"not an image", "fake.jpg", "image/jpeg")
    with pytest.raises(ValueError):
        save_model_image(_jpeg(600, 600), "model.jpg", "image/gif")
    with pytest.raises(ValueError):
        save_model_image(b"x" * (shop_models.MAX_UPLOAD_BYTES + 1), "big.jpg", "image/jpeg")


def test_shop_model_settings_page_is_owner_only(client, db_session):
    _login(client, db_session, "cashier")
    assert client.get("/admin/settings/shop-models", follow_redirects=False).status_code == 403

    _login(client, db_session, "manager")
    assert client.get("/admin/settings/shop-models", follow_redirects=False).status_code == 403

    _login(client, db_session, "owner")
    page = client.get("/admin/settings/shop-models")
    assert page.status_code == 200
    for _key, label in SHOP_MODEL_SLOTS:
        assert label in page.text


def test_shop_model_image_fields_use_the_shared_picker(client, db_session):
    _login(client, db_session, "owner")
    page = client.get("/admin/settings/shop-models")
    assert page.status_code == 200
    assert "data-image-field" in page.text
    assert 'class="image-input"' in page.text
    assert "data-image-picked" in page.text
    assert "data-image-clear" in page.text


def test_owner_upload_replaces_the_previous_model_file(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(shop_models, "MODEL_DIR", tmp_path / "shop_models")
    _login(client, db_session, "owner")
    token = csrf_token(client, "/admin/settings/shop-models")

    first = client.post(
        "/admin/settings/shop-models",
        data={"csrf_token": token, "slot_key": "girl_7"},
        files={"image": ("model.jpg", _jpeg(600, 600, (200, 100, 100)), "image/jpeg")},
        follow_redirects=False,
    )
    assert first.status_code == 303
    model = db_session.query(ShopModel).filter(ShopModel.slot_key == "girl_7").one()
    first_path = tmp_path / "shop_models" / model.image_path.rsplit("/", 1)[1]
    assert first_path.exists()
    page = client.get("/admin/settings/shop-models")
    assert model.image_path in page.text
    assert "آماده" in page.text

    token = csrf_token(client, "/admin/settings/shop-models")
    second = client.post(
        "/admin/settings/shop-models",
        data={"csrf_token": token, "slot_key": "girl_7"},
        files={"image": ("model2.jpg", _jpeg(600, 600, (100, 200, 100)), "image/jpeg")},
        follow_redirects=False,
    )
    assert second.status_code == 303
    db_session.refresh(model)
    second_path = tmp_path / "shop_models" / model.image_path.rsplit("/", 1)[1]
    assert second_path.exists()
    assert second_path != first_path
    assert not first_path.exists()


def test_a_bad_upload_leaves_the_saved_model_untouched(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(shop_models, "MODEL_DIR", tmp_path / "shop_models")
    _login(client, db_session, "owner")
    token = csrf_token(client, "/admin/settings/shop-models")
    response = client.post(
        "/admin/settings/shop-models",
        data={"csrf_token": token, "slot_key": "boy_2"},
        files={"image": ("small.jpg", _jpeg(100, 100), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    model = db_session.query(ShopModel).filter(ShopModel.slot_key == "boy_2").one()
    assert model.image_path is None


def test_the_settings_page_links_to_the_model_library(client, db_session):
    _login(client, db_session, "owner")
    page = client.get("/admin/settings")
    assert "/admin/settings/shop-models" in page.text
    assert "مدل‌های فروشگاه" in page.text
