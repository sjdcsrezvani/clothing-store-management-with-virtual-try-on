import json
from pathlib import Path

import pytest
from PIL import Image

from services.barcode import generate_barcode_image

from models import Product, ProductVariant, Settings, TagPrintBatch
from services.tags import (
    calculate_a4_fit,
    default_tag_config,
    render_tag_html,
    validate_tag_config,
)
from tests.conftest import csrf_token


def test_generated_barcode_image_contains_only_bars_for_exact_code():
    image_url = generate_barcode_image("10093")
    assert image_url.startswith("/static/uploads/barcodes/barcode_v7_")

    image = Image.open(Path(image_url.lstrip("/"))).convert("L")
    dark_rows = [
        y for y in range(image.height)
        if any(image.getpixel((x, y)) < 128 for x in range(image.width))
    ]
    assert dark_rows
    assert dark_rows == list(range(min(dark_rows), max(dark_rows) + 1))
    assert max(dark_rows) - min(dark_rows) < image.height


def test_tag_defaults_calculate_a4_capacity():
    config = default_tag_config()
    fit = calculate_a4_fit(config)

    assert fit["columns"] == 4
    assert fit["rows"] == 7
    assert fit["tags_per_page"] == 28
    assert fit["max_columns"] >= fit["columns"]
    assert fit["max_rows"] >= fit["rows"]


def test_tag_config_rejects_unknown_fields_and_out_of_bounds_positions():
    config = default_tag_config()
    config["fields"]["unknown"] = {"visible": True}
    with pytest.raises(ValueError):
        validate_tag_config(config)

    config = default_tag_config()
    config["fields"]["product_name"]["x"] = config["tag_width_mm"]
    with pytest.raises(ValueError):
        validate_tag_config(config)


def test_tag_renderer_uses_real_fields_and_escapes_values(monkeypatch):
    monkeypatch.setattr(
        "services.tags.generate_barcode_image",
        lambda value, density="compact": "/static/uploads/barcodes/test.png",
    )
    config = default_tag_config()
    item = {
        "product_name": "لباس <تست>",
        "barcode": "12345",
        "price_display": "250,000",
        "size": "۴ سال",
        "color": "آبی",
        "sku": "SKU-1",
        "brand": "رای کیدز",
        "category": "لباس",
        "image_path": None,
    }
    html = render_tag_html(config, item, {"name": "رای کیدز", "instagram": ""})

    assert "لباس &lt;تست&gt;" in html
    assert "/static/uploads/barcodes/test.png" in html
    assert 'data-field="barcode_text"' in html
    barcode_field = html.split('data-field="barcode"', 1)[1].split('</div>', 1)[0]
    assert "10093" not in barcode_field
    assert "stock_quantity" not in html
    assert "cost_price" not in html


def test_tag_settings_persist_and_print_uses_saved_renderer(client, db_session, authed, monkeypatch):
    monkeypatch.setattr(
        "services.tags.generate_barcode_image",
        lambda value, density="compact": "/static/uploads/barcodes/test.png",
    )
    product = Product(name="محصول تگ", category="لباس", brand="رای کیدز")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(
        product_id=product.id,
        price=250_000,
        stock_quantity=2,
        barcode="TAG-001",
        size="۴ سال",
        color="آبی",
    )
    db_session.add(variant)
    db_session.commit()

    response = client.get("/admin/settings/tags")
    assert response.status_code == 200
    assert "تنظیمات تگ و بارکد" in response.text
    assert "4 × 7" in response.text

    config = default_tag_config()
    config["custom_text"] = "تعویض تا ۷ روز"
    config["fields"]["sku"]["visible"] = True
    token = csrf_token(client, "/admin/settings/tags")
    response = client.post(
        "/admin/settings/tags",
        data={"tag_config": json.dumps(config, ensure_ascii=False), "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = db_session.query(Settings).filter(Settings.key == "tag_layout_config").one()
    assert "تعویض تا ۷ روز" in saved.value

    response = client.get("/admin/barcodes/print")
    assert response.status_code == 200
    assert "tag-render" in response.text
    assert "test.png" in response.text
    assert "tag-gradient" not in response.text


def test_tag_print_count_updates_selected_variant_copies(client, db_session, authed):
    product = Product(name="محصول شمارش", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=2, barcode="TAG-002")
    db_session.add(variant)
    db_session.commit()

    token = csrf_token(client, "/admin/barcodes/print")
    response = client.post(
        "/admin/barcodes/mark-printed",
        data={
            "selected_products": [str(variant.id), str(variant.id)],
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    setting = db_session.query(Settings).filter(Settings.key == f"barcode_printed_{variant.id}").one()
    assert setting.value == "2"

    token = csrf_token(client, "/admin/barcodes/print")
    response = client.post(
        "/admin/barcodes/reset",
        data={"selected_products": str(variant.id), "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(setting)
    assert setting.value == "1"


def test_native_barcode_form_marks_selected_quantity_and_scales_preview(client, db_session, authed):
    product = Product(name="محصول فرم چاپ", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=1, barcode="TAG-NATIVE")
    db_session.add(variant)
    db_session.commit()

    token = csrf_token(client, "/admin/barcodes/print")
    response = client.post(
        "/admin/barcodes/mark-printed",
        data={
            "csrf_token": token,
            "selected_products": str(variant.id),
            f"selected_quantity_{variant.id}": "3",
            "operation": "mark",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    setting = db_session.query(Settings).filter(Settings.key == f"barcode_printed_{variant.id}").one()
    assert setting.value == "3"
    batch = db_session.query(TagPrintBatch).order_by(TagPrintBatch.id.desc()).first()
    assert batch.total_quantity == 3

    page = client.get("/admin/barcodes/print")
    assert page.status_code == 200
    assert 'class="tag-selection-preview"' in page.text
    assert "--tag-scale:" in page.text
    assert "--frame-w:" in page.text
    assert "--frame-h:" in page.text
