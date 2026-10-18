"""Sizing phase 1 — the product-owned tag system resolves each variant's size
into the unified band on save; editing re-resolves when the ladder changes;
an unrecognized size still saves and simply carries no band."""
from models import Product, ProductVariant
from tests.conftest import csrf_token


def _create_product(client, **fields):
    data = {
        "name": "پیراهن سایز تست",
        "category": "پیراهن",
        "brand": "",
        "description": "",
        "variant_index_0": "0",
        "variant_size_0": "",
        "variant_color_0": "",
        "variant_price_0": "100000",
        "variant_cost_price_0": "0",
        "variant_stock_0": "1",
        "variant_barcode_0": "SIZ-ONE",
        "variant_sku_0": "",
    }
    data.update(fields)
    # The token is session-bound, not page-bound: any rendered page carries it.
    data["csrf_token"] = csrf_token(client, "/admin/products/add")
    return client.post("/admin/products/add", data=data, follow_redirects=False)


def _edit_product(client, product, **fields):
    data = {
        "name": product.name or "پیراهن",
        "category": product.category or "",
        "brand": "",
        "description": "",
    }
    data.update(fields)
    data["csrf_token"] = csrf_token(client, "/admin/products/add")
    return client.post(f"/admin/products/{product.id}", data=data, follow_redirects=False)


def _variant(db_session, barcode):
    return db_session.query(ProductVariant).filter(ProductVariant.barcode == barcode).one()


def test_age_sizes_resolve_into_bands_on_save(client, db_session, authed):
    response = _create_product(client, size_system="age", size_type="upper",
                               variant_size_0="6-7", variant_barcode_0="SIZ-A")
    assert response.status_code == 303
    variant = _variant(db_session, "SIZ-A")
    assert (variant.size, variant.size_system) == ("6-7", "age")
    assert (variant.band_min_cm, variant.band_max_cm) == (116, 122)
    assert (variant.band_min_months, variant.band_max_months) == (72, 84)
    product = db_session.query(Product).filter(Product.id == variant.product_id).one()
    assert (product.size_system, product.size_type) == ("age", "upper")


def test_persian_digit_sizes_resolve_too(client, db_session, authed):
    assert _create_product(client, size_system="age", size_type="upper",
                           variant_size_0="۶-۷", variant_barcode_0="SIZ-F").status_code == 303
    variant = _variant(db_session, "SIZ-F")
    assert (variant.band_min_cm, variant.band_max_cm) == (116, 122)


def test_unrecognized_size_saves_without_a_band(client, db_session, authed):
    response = _create_product(client, size_system="iran", size_type="upper",
                               variant_size_0="دور کمر کشی", variant_barcode_0="SIZ-B")
    assert response.status_code == 303
    variant = _variant(db_session, "SIZ-B")
    assert variant.size == "دور کمر کشی"
    assert variant.band_min_cm is None and variant.band_max_months is None


def test_no_system_means_no_band_and_nothing_moves(client, db_session, authed):
    response = _create_product(client, variant_size_0="۳ تا ۴ سال", variant_barcode_0="SIZ-C")
    assert response.status_code == 303
    variant = _variant(db_session, "SIZ-C")
    assert variant.size == "۳ تا ۴ سال"
    assert variant.band_min_cm is None and variant.size_system is None


def test_a_system_the_engine_cannot_name_is_refused(client, db_session, authed):
    response = _create_product(client, size_system="nonsense", size_type="upper",
                               variant_size_0="6-7", variant_barcode_0="SIZ-G")
    assert response.status_code == 303
    variant = _variant(db_session, "SIZ-G")
    assert variant.size_system is None and variant.band_min_cm is None
    product = db_session.query(Product).filter(Product.id == variant.product_id).one()
    assert product.size_system is None


def test_switching_the_system_re_resolves_existing_variants(client, db_session, authed):
    assert _create_product(client, variant_size_0="8", variant_barcode_0="SIZ-D").status_code == 303
    variant = _variant(db_session, "SIZ-D")
    assert variant.band_min_cm is None              # no system yet, no band
    product = db_session.query(Product).filter(Product.id == variant.product_id).one()

    assert _edit_product(client, product, size_system="us", size_type="upper").status_code == 303
    db_session.expire_all()
    variant = _variant(db_session, "SIZ-D")
    assert (variant.band_min_cm, variant.band_max_cm) == (128, 134)
    assert variant.size_system == "us"


def test_variant_edit_recomputes_its_own_band(client, db_session, authed):
    assert _create_product(client, size_system="us", size_type="upper",
                           variant_size_0="8", variant_barcode_0="SIZ-E").status_code == 303
    variant = _variant(db_session, "SIZ-E")
    response = client.post(f"/admin/variants/{variant.id}", data={
        "csrf_token": csrf_token(client, "/admin/products/add"),
        "size": "10", "color": "", "price": "100000", "cost_price": "0",
        "stock_quantity": "1", "sku": "", "tryon_details": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    db_session.expire_all()
    variant = _variant(db_session, "SIZ-E")
    assert variant.size == "10"
    assert (variant.band_min_cm, variant.band_max_cm) == (140, 146)
