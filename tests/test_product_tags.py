import json
from pathlib import Path

import pytest
from PIL import Image

from services.barcode import generate_barcode_image

from models import Product, ProductVariant, Settings, TagPrintBatch, TagTemplate
from services.tags import (
    calculate_a4_fit,
    default_tag_config,
    render_tag_html,
    validate_tag_config,
    save_tag_template,
)
from tests.conftest import csrf_token


def test_generated_barcode_image_contains_only_bars_for_exact_code():
    image_url = generate_barcode_image("10093")
    assert image_url.startswith("/static/uploads/barcodes/barcode_v8_")

    image = Image.open(Path(image_url.lstrip("/"))).convert("L")
    dark_rows = [
        y for y in range(image.height)
        if any(image.getpixel((x, y)) < 128 for x in range(image.width))
    ]
    assert dark_rows
    assert dark_rows == list(range(min(dark_rows), max(dark_rows) + 1))
    assert max(dark_rows) - min(dark_rows) < image.height


def test_catalog_and_tag_pages_render_together(client, db_session, authed):
    product = Product(name="صفحات کاتالوگ", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=1, barcode="PAGE-001")
    db_session.add(variant)
    db_session.commit()

    paths = (
        "/admin/products/add",
        "/admin/products",
        f"/admin/products/{product.id}",
        f"/admin/variants/{variant.id}/edit",
        "/admin/barcodes/print",
        "/admin/settings/tags",
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path


def test_catalog_forms_include_csrf_for_browser_mutations(client, db_session, authed):
    product_form = client.get("/admin/products/add")
    assert product_form.status_code == 200
    assert '<input type="hidden" name="csrf_token"' in product_form.text

    product = Product(name="محصول فرم", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=1, barcode="FORM-001")
    db_session.add(variant)
    db_session.commit()

    product_page = client.get(f"/admin/products/{product.id}")
    variant_page = client.get(f"/admin/variants/{variant.id}/edit")
    assert product_page.status_code == 200
    assert variant_page.status_code == 200
    assert product_page.text.count('<input type="hidden" name="csrf_token"') >= 1
    assert variant_page.text.count('<input type="hidden" name="csrf_token"') >= 2


def test_variant_demand_counter_increments_and_resets(client, db_session, authed):
    product = Product(name="محصول تقاضا", category="لباس")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=0, barcode="DEM-001")
    db_session.add(variant)
    db_session.commit()

    page = client.get(f"/admin/variants/{variant.id}/edit")
    assert page.status_code == 200
    assert "ثبت تقاضا" in page.text

    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    response = client.post(
        f"/admin/variants/{variant.id}/demand",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin/variants/{variant.id}/edit")

    db_session.refresh(variant)
    assert variant.demand_count == 1

    page = client.get(f"/admin/variants/{variant.id}/edit")
    assert "صفر کردن" in page.text

    token = csrf_token(client, f"/admin/variants/{variant.id}/edit")
    response = client.post(
        f"/admin/variants/{variant.id}/demand/reset",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(variant)
    assert variant.demand_count == 0


def test_duplicate_barcodes_in_one_product_submission_are_rejected(client, db_session, authed):
    token = csrf_token(client, "/admin/products/add")
    response = client.post(
        "/admin/products/add",
        data={
            "csrf_token": token,
            "name": "محصول تکراری",
            "variant_index_0": "0",
            "variant_price_0": "100",
            "variant_barcode_0": "DUP-001",
            "variant_index_1": "1",
            "variant_price_1": "200",
            "variant_barcode_1": "DUP-001",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "بارکد DUP-001 تکراری است" in response.text
    assert db_session.query(Product).filter(Product.name == "محصول تکراری").count() == 0


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


def test_product_tag_template_can_be_saved_assigned_and_used_for_print(client, db_session, authed, monkeypatch):
    monkeypatch.setattr(
        "services.tags.generate_barcode_image",
        lambda value, density="compact": "/static/uploads/barcodes/template.png",
    )
    config = default_tag_config("large")
    config["custom_text"] = "کالای لوکس"
    config["fields"]["custom_text"]["visible"] = True
    template = save_tag_template(db_session, "تگ لوکس", config)
    db_session.commit()

    product = Product(name="محصول لوکس", tag_template_id=template.id)
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100, stock_quantity=1, barcode="LUX-001")
    db_session.add(variant)
    db_session.commit()

    response = client.get("/admin/products/" + str(product.id))
    assert response.status_code == 200
    assert "تگ لوکس" in response.text

    response = client.get("/admin/barcodes/print")
    assert response.status_code == 200
    assert "تگ لوکس" in response.text
    assert "کالای لوکس" in response.text
    assert "--tag-w-mm: 60.0" in response.text


def test_tag_template_names_must_be_unique(db_session):
    config = default_tag_config()
    save_tag_template(db_session, "تگ ساده", config)
    with pytest.raises(ValueError):
        save_tag_template(db_session, "تگ ساده", config)


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
