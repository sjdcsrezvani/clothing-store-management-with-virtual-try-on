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


def test_shoe_sizes_save_the_shared_band(client, db_session, authed):
    assert _create_product(client, size_system="shoe_eu", size_type="shoes",
                           variant_size_0="30", variant_barcode_0="SIZ-SHOE").status_code == 303
    variant = _variant(db_session, "SIZ-SHOE")
    assert (variant.size, variant.size_system) == ("30", "shoe_eu")
    assert (variant.band_min_cm, variant.band_max_cm) == (104, 116)
    assert (variant.band_min_months, variant.band_max_months) == (48, 72)


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

    assert _edit_product(client, product, size_system="age", size_type="upper").status_code == 303
    db_session.expire_all()
    variant = _variant(db_session, "SIZ-D")
    assert (variant.band_min_cm, variant.band_max_cm) == (128, 134)
    assert variant.size_system == "age"


def test_variant_edit_recomputes_its_own_band(client, db_session, authed):
    assert _create_product(client, size_system="age", size_type="upper",
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


def test_product_form_carries_the_sizing_pickers(client, db_session, authed):
    page = client.get("/admin/products/add").text
    assert 'id="size_type"' in page and 'id="size_system"' in page
    assert 'id="sizing-ladders"' in page
    assert "/static/js/sizing.js" in page
    assert "data-size-select" in page            # the row template the script stamps
    assert "variant_size_system_" not in page    # the per-variant system field is gone
    assert "سایز کفش — مثل 8C" in page and "طول پا — مثل 14 سانت" in page


def test_edit_form_preselects_the_stored_system(client, db_session, authed):
    assert _create_product(client, size_system="age", size_type="upper",
                           variant_size_0="8", variant_barcode_0="SIZ-H").status_code == 303
    variant = _variant(db_session, "SIZ-H")
    page = client.get(f"/admin/products/{variant.product_id}").text
    assert 'value="age" selected' in page
    assert 'value="upper" selected' in page


def test_variant_page_carries_its_products_system(client, db_session, authed):
    assert _create_product(client, size_system="age", size_type="upper",
                           variant_size_0="6-7", variant_barcode_0="SIZ-I").status_code == 303
    variant = _variant(db_session, "SIZ-I")
    page = client.get(f"/admin/variants/{variant.id}/edit").text
    assert 'data-size-field' in page
    assert 'data-size-system="age"' in page
    assert 'id="sizing-ladders"' in page


def test_preview_answers_in_the_shops_single_standard(client, db_session, authed):
    page = client.get("/admin/sizing/preview?system=age&type=upper&size=6-7").text
    assert page == "6-7 سال"
    assert "قد" not in page
    junk = client.get("/admin/sizing/preview?system=iran&type=upper&size=خیلی%20بزرگ").text
    assert "تبدیل ندارد" in junk
    empty = client.get("/admin/sizing/preview?system=age&type=upper&size=").text
    assert empty.strip() == ""

    from models import Settings
    row = db_session.query(Settings).filter(Settings.key == "sizing_standard").first()
    if row:
        row.value = "height"
    else:
        db_session.add(Settings(key="sizing_standard", value="height"))
    db_session.commit()
    body = client.get("/admin/sizing/preview?system=age&type=upper&size=6-7").text
    assert body.startswith("قد")
    assert "سال" not in body


def test_preview_reads_the_type_and_gender_the_form_actually_sends(client, db_session, authed):
    assert "تبدیل ندارد" not in client.get(
        "/admin/sizing/preview?system=iran&type=upper&size=50").text
    assert "تبدیل ندارد" not in client.get(
        "/admin/sizing/preview?system=kids_letters&type=upper&size=M").text
    assert "تبدیل ندارد" not in client.get(
        "/admin/sizing/preview?system=adult&type=upper&size=38&gender=woman").text
    assert client.get(
        "/admin/sizing/preview?system=shoe_uk&type=shoes&size=7").text == "2-4 سال"
    # And a size that genuinely cannot be read still says so.
    assert client.get(
        "/admin/sizing/preview?system=iran&type=upper&size=999").text == "تبدیل ندارد"


def test_product_list_and_tag_print_speak_the_chosen_unit(client, db_session, authed):
    assert _create_product(client, size_system="age", size_type="upper",
                           variant_size_0="6-7", variant_barcode_0="SIZ-LST").status_code == 303
    page = client.get("/admin/products").text
    assert "6-7 سال" in page

    from models import Settings
    row = db_session.query(Settings).filter(Settings.key == "sizing_standard").first()
    if row:
        row.value = "height"
    else:
        db_session.add(Settings(key="sizing_standard", value="height"))
    db_session.commit()

    page = client.get("/admin/products").text
    assert "قد 116 تا 122" in page

    tag_page = client.get("/admin/barcodes/print").text
    assert "قد 116 تا 122" in tag_page


def test_search_finds_a_size_by_its_band_label(client, db_session, authed):
    from models import Settings

    assert _create_product(client, name="پیراهن جستجوی سایز", size_system="height",
                           size_type="upper", variant_size_0="122",
                           variant_barcode_0="SIZ-S").status_code == 303
    row = db_session.query(Settings).filter(Settings.key == "sizing_standard").first()
    if row:
        row.value = "height"
    else:
        db_session.add(Settings(key="sizing_standard", value="height"))
    db_session.commit()

    # 116 is in the band's label, not in the raw 122 tag.
    page = client.get("/admin/products?search=116").text
    assert "پیراهن جستجوی سایز" in page


def test_height_double_is_entered_by_its_lower_number(client, db_session, authed):
    assert _create_product(client, size_system="height", size_type="upper",
                           variant_size_0="122", variant_barcode_0="SIZ-H1").status_code == 303
    variant = _variant(db_session, "SIZ-H1")
    assert (variant.band_min_cm, variant.band_max_cm) == (116, 128)
    assert (variant.band_min_months, variant.band_max_months) == (72, 96)

    assert _create_product(client, size_system="height", size_type="upper",
                           variant_size_0="112", variant_barcode_0="SIZ-H2").status_code == 303
    variant = _variant(db_session, "SIZ-H2")
    assert (variant.band_min_cm, variant.band_max_cm) == (112, 118)


def test_adult_letters_and_numbers_store_the_adult_band(client, db_session, authed):
    assert _create_product(client, size_system="adult", size_type="upper",
                           gender="woman", variant_size_0="S", variant_barcode_0="SIZ-AD1").status_code == 303
    variant = _variant(db_session, "SIZ-AD1")
    assert (variant.band_min_adult, variant.band_max_adult) == (36, 38)
    assert variant.band_min_cm is None

    assert _create_product(client, size_system="adult", size_type="upper",
                           gender="man", variant_size_0="50", variant_barcode_0="SIZ-AD2").status_code == 303
    variant = _variant(db_session, "SIZ-AD2")
    assert (variant.band_min_adult, variant.band_max_adult) == (50, 50)


def test_changing_adult_gender_re_resolves_letter_bands(client, db_session, authed):
    assert _create_product(client, size_system="adult", size_type="upper",
                           gender="woman", variant_size_0="S", variant_barcode_0="SIZ-ADG").status_code == 303
    variant = _variant(db_session, "SIZ-ADG")
    assert (variant.band_min_adult, variant.band_max_adult) == (36, 38)

    product = db_session.query(Product).filter(Product.id == variant.product_id).one()
    assert _edit_product(client, product, size_system="adult", size_type="upper",
                         gender="man").status_code == 303
    db_session.expire_all()
    variant = _variant(db_session, "SIZ-ADG")
    assert (variant.band_min_adult, variant.band_max_adult) == (46, 48)


def test_kids_letters_and_socks_save_their_child_bands(client, db_session, authed):
    assert _create_product(client, size_system="kids_letters", size_type="upper",
                           variant_size_0="M(8/10)", variant_barcode_0="SIZ-KL").status_code == 303
    variant = _variant(db_session, "SIZ-KL")
    assert (variant.band_min_cm, variant.band_max_cm) == (128, 140)
    assert (variant.band_min_months, variant.band_max_months) == (96, 120)

    assert _create_product(client, size_system="sock", size_type="lower",
                           variant_size_0="23-26", variant_barcode_0="SIZ-SK").status_code == 303
    variant = _variant(db_session, "SIZ-SK")
    assert (variant.band_min_cm, variant.band_max_cm) == (92, 104)
    assert (variant.band_min_months, variant.band_max_months) == (24, 48)
