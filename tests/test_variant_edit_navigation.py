"""The single-variant edit page: its way back, its neighbours, and its fields.

The four text fields on this page were written without `type="text"`, so the
global field rules never matched them and they kept the browser's own square
box. The page also had no route back to the product and no way to walk the
product's other variants.
"""
from models import Product, ProductVariant


def _product_with_three_variants(db_session):
    product = Product(name="محصول پیمایش", category="لباس")
    db_session.add(product)
    db_session.flush()
    variants = [
        ProductVariant(
            product_id=product.id,
            price=100,
            stock_quantity=1,
            barcode=f"NAV-{position}",
            size=str(30 + position),
            color=f"رنگ {position}",
        )
        for position in (1, 2, 3)
    ]
    db_session.add_all(variants)
    db_session.commit()
    return product, variants


def test_variant_edit_marks_its_text_fields_as_text(client, db_session, authed):
    product, variants = _product_with_three_variants(db_session)
    page = client.get(f"/admin/variants/{variants[0].id}/edit").text

    for field in ("color", "color_code", "sku", "storage_location"):
        assert f'<input type="text" id="{field}"' in page, field


def test_variant_edit_guards_unsaved_work_when_walking_the_shelf(
        client, db_session, authed):
    product, variants = _product_with_three_variants(db_session)
    page = client.get(f"/admin/variants/{variants[0].id}/edit").text

    assert 'id="variant-edit-form"' in page
    assert "beforeunload" in page


def test_variant_edit_links_back_to_the_product_and_to_its_neighbours(
        client, db_session, authed):
    product, variants = _product_with_three_variants(db_session)
    middle = variants[1]
    page = client.get(f"/admin/variants/{middle.id}/edit").text

    assert f'href="/admin/products/{product.id}"' in page
    assert "بازگشت به محصول" in page
    assert f'/admin/variants/{variants[0].id}/edit' in page
    assert f'/admin/variants/{variants[2].id}/edit' in page
    assert "تنوع 2 از 3" in page


def test_first_and_last_variants_disable_the_missing_direction(
        client, db_session, authed):
    product, variants = _product_with_three_variants(db_session)

    first = client.get(f"/admin/variants/{variants[0].id}/edit").text
    assert "disabled>قبلی</button>" in first
    assert f'/admin/variants/{variants[1].id}/edit' in first

    last = client.get(f"/admin/variants/{variants[2].id}/edit").text
    assert "disabled>بعدی</button>" in last
    assert f'/admin/variants/{variants[1].id}/edit' in last
