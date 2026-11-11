"""Fixed photo slots: main/back for garments, hats, socks and gloves,
top/left/right for shoes. The same slots feed the add product page and the
variant edit page."""
from pathlib import Path

from models import Product, ProductVariant, VariantImage
from services.image_slots import image_mode_for, image_slots_for, slot_for_view
from tests.conftest import csrf_token


class _Text:
    def __init__(self, **values):
        self.__dict__.update(values)


def test_image_slots_follow_the_product_family():
    garment = _Text(category="تیشرت", garment_type="", name="")
    assert image_mode_for(garment) == "garment"
    assert [slot["label"] for slot in image_slots_for(garment)] == [
        "تصویر اصلی", "تصویر پشت"]

    for category in ("کلاه", "جوراب", "دستکش"):
        accessory = _Text(category=category, garment_type="", name="")
        assert image_mode_for(accessory) == "accessory"
        assert [slot["label"] for slot in image_slots_for(accessory)] == [
            "تصویر اصلی", "تصویر پشت"]

    shoes = _Text(category="کفش", garment_type="", name="")
    assert image_mode_for(shoes) == "shoes"
    assert [slot["label"] for slot in image_slots_for(shoes)] == [
        "تصویر اصلی (از بالا)", "نمای چپ", "نمای راست"]


def test_slot_for_view_uses_only_the_family_s_real_slots():
    assert slot_for_view("upper", "main") == 0
    assert slot_for_view("upper", "back") == 1
    assert slot_for_view("upper", "detail") is None
    assert slot_for_view("headwear", "back") == 1
    assert slot_for_view("headwear", "detail") is None
    assert slot_for_view("feet", "main") == 0
    assert slot_for_view("feet", "back") is None
    assert slot_for_view("feet", "detail") is None


def _product_with_variant(db_session, category="تیشرت"):
    product = Product(name="محصول عکس", category=category)
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100_000,
                             stock_quantity=1, barcode="IMG-SLOT-1")
    db_session.add(variant)
    db_session.commit()
    return product, variant


def test_variant_edit_shows_only_the_family_s_slots(client, db_session, authed):
    product, variant = _product_with_variant(db_session, category="تیشرت")
    page = client.get(f"/admin/variants/{variant.id}/edit").text

    assert "تصویر اصلی" in page
    assert "تصویر پشت" in page
    assert "تصویر جزئیات" not in page
    assert 'name="variant_image"' in page
    assert 'name="variant_image_1"' in page
    assert 'name="variant_image_2"' not in page


def test_add_product_stores_main_and_back_frames(client, db_session, authed):
    token = csrf_token(client, "/admin/products/add")
    data = {
        "name": "پیراهن دو عکسه",
        "category": "پیراهن",
        "variant_index_0": "0",
        "variant_price_0": "120000",
        "variant_stock_0": "2",
        "variant_barcode_0": "IMG-SLOT-ADD",
        "csrf_token": token,
    }
    files = {
        "variant_image_0": ("main.jpg", b"main-photo", "image/jpeg"),
        "variant_image_1_0": ("back.jpg", b"back-photo", "image/jpeg"),
    }
    response = client.post("/admin/products/add", data=data, files=files,
                           follow_redirects=False)
    assert response.status_code == 303

    variant = db_session.query(ProductVariant).filter(
        ProductVariant.barcode == "IMG-SLOT-ADD").one()
    frames = sorted(variant.images, key=lambda frame: frame.sort_order)
    try:
        assert [frame.sort_order for frame in frames] == [0, 1]
        assert variant.image_path == frames[0].image_path
        assert len({frame.image_path for frame in frames}) == 2
    finally:
        for frame in frames:
            Path(frame.image_path.lstrip("/")).unlink(missing_ok=True)


def test_saving_a_slot_replaces_it_instead_of_stacking_a_gallery(
        client, db_session, authed):
    product, variant = _product_with_variant(db_session)
    variant.image_path = "/static/uploads/products/main-old.jpg"
    db_session.add_all([
        VariantImage(variant_id=variant.id, image_path=variant.image_path, sort_order=0),
        VariantImage(variant_id=variant.id, image_path="/static/uploads/products/back-old.jpg", sort_order=1),
    ])
    db_session.commit()

    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    response = client.post(
        f"/admin/variants/{variant.id}",
        data={"csrf_token": token, "size": "", "color": "", "price": "100000",
              "cost_price": "0", "stock_quantity": "1"},
        files={"variant_image_1": ("back-new.jpg", b"new-back", "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    frames = sorted(variant.images, key=lambda frame: frame.sort_order)
    try:
        assert [frame.sort_order for frame in frames] == [0, 1]
        assert frames[0].image_path == "/static/uploads/products/main-old.jpg"
        assert frames[1].image_path != "/static/uploads/products/back-old.jpg"
        assert variant.image_path == "/static/uploads/products/main-old.jpg"
    finally:
        for frame in frames:
            if frame.image_path.startswith("/static/uploads/products/"):
                Path(frame.image_path.lstrip("/")).unlink(missing_ok=True)


def test_deleting_the_main_photo_leaves_the_slot_empty(client, db_session, authed):
    product, variant = _product_with_variant(db_session)
    variant.image_path = "/static/uploads/products/main.jpg"
    main = VariantImage(variant_id=variant.id, image_path=variant.image_path, sort_order=0)
    back = VariantImage(variant_id=variant.id, image_path="/static/uploads/products/back.jpg", sort_order=1)
    db_session.add_all([main, back])
    db_session.commit()

    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    response = client.post(f"/admin/variant-images/{main.id}/delete",
                           data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(variant)
    assert variant.image_path is None
    assert db_session.query(VariantImage).filter(
        VariantImage.variant_id == variant.id).one().sort_order == 1
