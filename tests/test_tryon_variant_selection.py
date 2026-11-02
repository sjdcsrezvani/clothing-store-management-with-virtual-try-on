"""The try-on page must send the photo of the variant whose barcode was entered.

Size is irrelevant for try-on; color is not. These tests pin the selection
rules so a refactor cannot silently send another color (a paid, wrong image).
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS

from routers.clothes_images import (
    _build_tryon_prompt,
    _product_image_paths,
    _scanned_product_dict,
)
from services.image_gen import EDIT_REMINDER, ImageGenService, MAIN_PROMPT


def _variant(id, barcode, color="", size="", image_path=None, gallery=(), active=True):
    return NS(id=id, barcode=barcode, color=color, size=size,
              image_path=image_path, images=[NS(image_path=p) for p in gallery],
              is_active=active, tryon_details=None)


def _product(id=1, image_path=None, gallery=(), variants=()):
    return NS(id=id, name="تیشرت تست", image_path=image_path,
              images=[NS(image_path=p) for p in gallery], variants=list(variants))


def test_exact_variant_photo_wins_over_other_sizes_and_product_photo():
    red_s = _variant(1, "111", color="قرمز", size="S", image_path="/static/red-s.jpg")
    red_m = _variant(2, "222", color="قرمز", size="M", image_path="/static/red-m.jpg")
    product = _product(image_path="/static/product.jpg", variants=[red_s, red_m])

    assert _product_image_paths(product, "111") == ["static/red-s.jpg"]


def test_gallery_only_variant_uses_its_own_first_frame():
    blue = _variant(1, "111", color="آبی", size="M",
                    gallery=["/static/blue-1.jpg", "/static/blue-2.jpg"])
    product = _product(image_path="/static/other.jpg", variants=[blue])

    assert _product_image_paths(product, "111") == ["static/blue-1.jpg"]


def test_missing_photo_falls_back_to_same_color_other_size_only():
    red_s = _variant(1, "111", color="قرمز", size="S")
    red_l = _variant(2, "222", color="قرمز", size="L", image_path="/static/red-l.jpg")
    blue_m = _variant(3, "333", color="آبی", size="M", image_path="/static/blue-m.jpg")
    product = _product(image_path="/static/ambiguous.jpg",
                       variants=[red_s, red_l, blue_m])

    assert _product_image_paths(product, "111") == ["static/red-l.jpg"]


def test_no_same_color_photo_blocks_instead_of_sending_another_color():
    red = _variant(1, "111", color="قرمز", size="S")
    blue = _variant(2, "222", color="آبی", size="M", image_path="/static/blue.jpg")
    product = _product(image_path="/static/ambiguous.jpg", variants=[red, blue])

    assert _product_image_paths(product, "111") == []


def test_product_photo_is_used_only_when_every_variant_is_that_color():
    red = _variant(1, "111", color="قرمز", size="S")
    red2 = _variant(2, "222", color="قرمز", size="M", active=False)
    product = _product(image_path="/static/red.jpg", variants=[red, red2])

    assert _product_image_paths(product, "111") == ["static/red.jpg"]


def test_product_photo_is_blocked_when_an_inactive_variant_has_another_color():
    red = _variant(1, "111", color="قرمز", size="S")
    blue = _variant(2, "222", color="آبی", size="M", active=False)
    product = _product(image_path="/static/legacy-blue.jpg", variants=[red, blue])

    assert _product_image_paths(product, "111") == []


def test_stale_barcode_never_guesses_a_variant():
    product = _product(image_path="/static/red.jpg",
                       variants=[_variant(1, "111", color="قرمز", size="S")])

    assert _product_image_paths(product, "999") == []


def test_preview_uses_the_same_photo_the_provider_will_receive():
    red = _variant(1, "111", color="قرمز", size="S", image_path="/static/red-s.jpg")
    red.tryon_details = "آستین کوتاه قرمز"
    blue = _variant(2, "222", color="آبی", size="M", image_path="/static/blue-m.jpg")
    product = _product(variants=[red, blue])

    card = _scanned_product_dict(product, "111")
    assert card["image_path"] == "/static/red-s.jpg"
    assert _product_image_paths(product, card["barcode"]) == ["static/red-s.jpg"]
    assert card["details"] == "آستین کوتاه قرمز"


def test_prompt_contains_only_the_scanned_variants_details():
    prefix, mid, suffix = _build_tryon_prompt(
        "تیشرت تست", "kids_classic", "preserve", "product_only",
        garment_details=["آستین کوتاه قرمز", "Confidence: 0.9"],
        category="upper", face_key="preserve")

    assert "آستین کوتاه قرمز" in mid
    assert "Confidence" not in mid
    assert suffix.startswith("OUTFIT RULE")
    assert prefix.startswith("Virtual try-on photo")


def test_unknown_dropdown_values_fall_back_without_raising():
    prefix, mid, suffix = _build_tryon_prompt(
        "تیشرت تست", "no-such-background", "no-such-pose", "no-such-mode",
        garment_details=None, category="no-such-category", face_key="no-such-face")

    assert prefix
    assert suffix.startswith("OUTFIT RULE")
    assert isinstance(mid, str)


def test_final_prompt_keeps_base_then_main_then_mid_then_outfit_rule():
    service = ImageGenService()
    final = service._build_prompt("تیشرت تست", "جزئیات رنگ قرمز.", "OUTFIT RULE: last.")
    assert final.startswith("تیشرت تست.")
    assert MAIN_PROMPT in final
    assert final.index("جزئیات رنگ قرمز.") < final.index("OUTFIT RULE: last.")
    assert final.endswith(EDIT_REMINDER)


def _seed_product(db_session):
    from models import Product, ProductVariant, StaffUser

    product = Product(name="تیشرت تست", image_path="/static/uploads/products/product.jpg")
    db_session.add(product)
    db_session.flush()
    red = ProductVariant(product_id=product.id, barcode="111", size="S", color="قرمز",
                         price=100000, image_path="/static/uploads/products/red.jpg",
                         tryon_details="آستین کوتاه قرمز")
    blue = ProductVariant(product_id=product.id, barcode="222", size="M", color="آبی",
                          price=100000, image_path="/static/uploads/products/blue.jpg",
                          tryon_details="آستین کوتاه آبی")
    staff = StaffUser(username="owner", password_hash="x", role="owner", is_active=True)
    db_session.add_all([red, blue, staff])
    db_session.commit()
    return product, staff


def _fake_request(staff, sid="variant-selection-test"):
    return NS(session={"staff_user_id": staff.id, "tryon_sid": sid, "staff_role": "owner"},
              client=NS(host="127.0.0.1"), headers={})


def test_switching_variant_then_generating_queues_the_new_variant_photo(
        db_session, monkeypatch):
    from routers import clothes_images as ci

    product, staff = _seed_product(db_session)
    request = _fake_request(staff)

    asyncio.run(ci.admin_tryon_add_product(request, barcode_input="111", db=db_session))
    state = ci._state(request)
    assert state["scanned"][0]["barcode"] == "111"
    assert state["scanned"][0]["image_path"] == "/static/uploads/products/red.jpg"

    asyncio.run(ci.admin_tryon_variant(request, product_id=product.id,
                                       barcode="222", db=db_session))
    assert state["scanned"][0]["barcode"] == "222"
    assert state["scanned"][0]["image_path"] == "/static/uploads/products/blue.jpg"

    state["kid_photo_bytes"] = b"kid-photo-bytes"
    state["kid_photo_content_type"] = "image/jpeg"
    captured = {}

    def fake_enqueue(db, job_type, payload):
        captured["job_type"] = job_type
        captured["payload"] = payload
        return NS(id=7)

    monkeypatch.setattr(ci, "enqueue", fake_enqueue)
    response = asyncio.run(ci.admin_tryon_generate(
        request=request, background="kids_classic", pose="preserve",
        face="preserve", mode="product_only", category="upper",
        custom_prompt="", db=db_session))
    try:
        assert response.status_code == 303
        payload = captured["payload"]
        assert captured["job_type"] == "tryon"
        assert payload["reference_paths"][1] == "static/uploads/products/blue.jpg"
        assert "static/uploads/products/red.jpg" not in payload["reference_paths"]
        assert "آستین کوتاه آبی" in payload["prompt_mid"]
        assert "آستین کوتاه قرمز" not in payload["prompt_mid"]
        assert payload["prompt"].startswith("Virtual try-on photo")
        assert payload["prompt_suffix"].startswith("OUTFIT RULE")
        final = ImageGenService()._build_prompt(
            payload["prompt"], payload["prompt_mid"], payload["prompt_suffix"])
        assert final.count(MAIN_PROMPT) == 1
        assert final.endswith(EDIT_REMINDER)
    finally:
        for path in captured.get("payload", {}).get("temporary_paths", []):
            Path(path).unlink(missing_ok=True)
        ci._tryon_sessions.pop("variant-selection-test", None)


def test_scanning_another_variant_replaces_the_existing_card(db_session):
    """A second code for the same product must not be ignored: the card keeps
    its place but switches to the variant just entered."""
    from routers import clothes_images as ci

    product, staff = _seed_product(db_session)
    request = _fake_request(staff, sid="upsert-selection-test")
    try:
        asyncio.run(ci.admin_tryon_add_product(request, barcode_input="111", db=db_session))
        asyncio.run(ci.admin_tryon_add_product(request, barcode_input="222", db=db_session))

        state = ci._state(request)
        assert len(state["scanned"]) == 1
        assert state["scanned"][0]["barcode"] == "222"
        assert state["scanned"][0]["image_path"] == "/static/uploads/products/blue.jpg"
    finally:
        ci._tryon_sessions.pop("upsert-selection-test", None)
