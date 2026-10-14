"""Phase 7 no-scroll fit: trimmed card, theme accent, name search, held +
typed campaigns, Persian-digit barcodes, internal basket scroll."""
import json

from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_customer, _make_variant


def _login(client):
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)


def _scan_page(client, db_session, customer_id="0", name="کالای فیت"):
    _login(client)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=100_000, stock=5, name=name)
    page = client.post("/sales/add-to-basket", data={
        "customer_id": customer_id, "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    return page, variant


def test_customer_card_is_name_phone_tier_only(client, db_session):
    customer = _make_customer(db_session, phone="09120000041")
    page, _ = _scan_page(client, db_session, customer_id=str(customer.id))
    assert "امتیاز" not in page  # trimmed per vote
    assert customer.phone in page


def test_till_follows_theme_accent(client, db_session):
    page, _ = _scan_page(client, db_session)
    assert 'page-container accent-full' not in page


def test_product_search_needs_two_chars_and_matches(client, db_session):
    _login(client)
    _, variant = _make_variant(db_session, price=100_000, stock=5,
                               name="پیراهن جستجو")
    short = client.get("/sales/api/product-search?q=پ").json()
    assert short["items"] == []
    hit = client.get("/sales/api/product-search?q=جستجو").json()
    assert any(i["barcode"] == variant.barcode for i in hit["items"])
    assert len(hit["items"]) <= 8


def test_persian_digit_barcode_scans(client, db_session):
    from models import to_english_digits
    _login(client)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=100_000, stock=5,
                               name="کالای فارسی")
    fa_digits = str.translate("0123456789", str.maketrans(
        "0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    fa_barcode = "".join(fa_digits[int(c)] if c.isdigit() else c
                         for c in variant.barcode)
    assert to_english_digits(fa_barcode) == variant.barcode
    page = client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": fa_barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert variant.barcode in page or "آخرین اسکن" in page


def test_basket_scrolls_inside_and_density_present():
    style = open("static/css/style.css", encoding="utf-8").read()
    assert "max-height: 320px" in style  # list scrolls, totals stay put
    assert ".checkout-layout .card" in style
