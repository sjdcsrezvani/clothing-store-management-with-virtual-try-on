"""Storage compression: quality, format preservation and the migration helper."""
import io
import os
import shutil
import time
from pathlib import Path

from PIL import Image

from services.image_storage import (
    MAX_STORAGE_DIM,
    compress_for_storage,
    save_upload_image,
)
from scripts.compress_uploads import compress_file
from tests.conftest import csrf_token


def _large_jpeg(size=(3000, 2000)) -> bytes:
    image = Image.linear_gradient("L").resize(size).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def _open(raw: bytes) -> Image.Image:
    return Image.open(io.BytesIO(raw))


def test_large_jpeg_is_resized_without_changing_aspect():
    raw = _large_jpeg()
    data, suffix = compress_for_storage(raw, ".jpg")
    image = _open(data)
    assert suffix == ".jpg"
    assert max(image.size) <= MAX_STORAGE_DIM
    assert image.size == (1600, 1067)
    assert len(data) < len(raw)


def test_small_jpeg_is_returned_byte_identical():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "navy").save(buffer, "JPEG", quality=90)
    raw = buffer.getvalue()
    data, suffix = compress_for_storage(raw, ".jpg")
    assert data == raw
    assert suffix == ".jpg"


def test_png_transparency_is_preserved():
    image = Image.new("RGBA", (2000, 1000), (20, 120, 220, 80))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    data, suffix = compress_for_storage(buffer.getvalue(), ".png")
    result = _open(data)
    assert suffix == ".png"
    assert max(result.size) <= MAX_STORAGE_DIM
    assert result.mode in ("RGBA", "LA", "P")
    if result.mode == "RGBA":
        assert result.getchannel("A").getextrema()[0] < 255


def test_exif_orientation_is_baked_in():
    image = Image.new("RGB", (2000, 1000), "green")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95, exif=exif)
    data, _ = compress_for_storage(buffer.getvalue(), ".jpg")
    assert _open(data).size == (800, 1600)


def test_unreadable_input_is_kept():
    raw = b"not an image"
    data, suffix = compress_for_storage(raw, ".jpg")
    assert data == raw
    assert suffix == ".jpg"


def test_save_upload_image_writes_the_compressed_copy(tmp_path):
    raw = _large_jpeg()
    url = save_upload_image(raw, "garment.jpg", tmp_path, "/static/uploads/products")
    assert url.startswith("/static/uploads/products/")
    saved = tmp_path / url.rsplit("/", 1)[-1]
    assert saved.exists()
    assert max(Image.open(saved).size) <= MAX_STORAGE_DIM
    assert saved.stat().st_size < len(raw)


def test_migration_dry_run_then_apply_preserves_mtime(tmp_path):
    path = tmp_path / "garment.jpg"
    path.write_bytes(_large_jpeg())
    old_time = time.time() - 5 * 86400
    os.utime(path, (old_time, old_time))
    before = path.stat().st_size

    raw_size, new_size = compress_file(path, apply=False)
    assert raw_size == before
    assert new_size < before
    assert path.stat().st_size == before  # dry-run wrote nothing

    backup = tmp_path / "backup"
    raw_size, new_size = compress_file(path, apply=True, backup=backup)
    assert raw_size == before
    assert new_size < before
    assert path.stat().st_size == new_size
    assert abs(path.stat().st_mtime - old_time) < 2
    assert (backup / "garment.jpg").exists()
    assert (backup / "garment.jpg").stat().st_size == before

    # Idempotent: a second pass has nothing to do.
    raw_size, new_size = compress_file(path, apply=True, backup=backup)
    assert raw_size == new_size


def test_person_disk_copy_is_compressed_but_session_keeps_original():
    from routers.clothes_images import KID_PHOTO_DIR, _set_kid_photo

    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)
    raw = _large_jpeg((2600, 1800))
    state = {}
    _set_kid_photo(state, raw, "image/jpeg", save=True)
    assert state["kid_photo_bytes"] == raw  # generation gets the original
    files = list(KID_PHOTO_DIR.glob("*"))
    assert len(files) == 1
    assert max(Image.open(files[0]).size) <= MAX_STORAGE_DIM
    assert files[0].stat().st_size < len(raw)
    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)


def test_duplicate_person_photo_keeps_one_file():
    from routers.clothes_images import KID_PHOTO_DIR, _set_kid_photo

    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)
    raw = _large_jpeg((2600, 1800))
    _set_kid_photo({}, raw, "image/jpeg", save=True)
    _set_kid_photo({}, raw, "image/jpeg", save=True)
    assert len(list(KID_PHOTO_DIR.glob("*"))) == 1
    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)


def test_old_person_photos_are_pruned():
    from routers.clothes_images import KID_PHOTO_DIR, _prune_kid_photos, _set_kid_photo

    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)
    _set_kid_photo({}, _large_jpeg((2600, 1800)), "image/jpeg", save=True)
    stored = next(KID_PHOTO_DIR.glob("*"))
    old = time.time() - 31 * 86400
    os.utime(stored, (old, old))
    _prune_kid_photos()
    assert not stored.exists()
    shutil.rmtree(KID_PHOTO_DIR, ignore_errors=True)


def test_product_upload_stores_a_compressed_image(client, db_session, authed):
    from models import ProductVariant

    raw = _large_jpeg((2400, 1600))
    data = {
        "name": "pytest compressed garment",
        "category": "پیراهن",
        "variant_index_0": "0",
        "variant_size_0": "",
        "variant_color_0": "",
        "variant_price_0": "100000",
        "variant_cost_price_0": "0",
        "variant_stock_0": "1",
        "variant_barcode_0": "PYTEST-IMG-1",
        "variant_sku_0": "",
        "csrf_token": csrf_token(client, "/admin/products/add"),
    }
    response = client.post(
        "/admin/products/add", data=data,
        files={"variant_image_0": ("garment.jpg", raw, "image/jpeg")},
        follow_redirects=False)
    assert response.status_code == 303

    variant = db_session.query(ProductVariant).filter(
        ProductVariant.barcode == "PYTEST-IMG-1").one()
    assert variant.image_path.startswith("/static/uploads/products/")
    stored = Path(variant.image_path.lstrip("/"))
    try:
        assert stored.exists()
        assert max(Image.open(stored).size) <= MAX_STORAGE_DIM
        assert stored.stat().st_size < len(raw)
    finally:
        stored.unlink(missing_ok=True)
