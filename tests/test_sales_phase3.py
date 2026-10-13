"""Phase 3 counter flow: louder scans, 44px targets, contextual cash box,
explicit discount Apply, split credit input + client sum guard, and a
confirm that lands straight on a new sale."""
from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_variant


def _scan_page(client, db_session):
    import json
    from tests.test_sales_money import _make_variant
    _login = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": _login}, follow_redirects=False)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session)
    return client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text


def test_split_editor_has_credit_leg_and_sum_guard(client, db_session):
    page = _scan_page(client, db_session)
    assert 'id="split-credit-input"' in page  # the نسیه leg is enterable
    assert 'name="split_credit"' in page
    assert 'id="split-sum-error"' in page and 'role="alert"' in page
    assert "stopImmediatePropagation" in page  # bad sums stop everything
    assert "سهم نسیه" in page


def test_discount_checkbox_no_longer_auto_submits(client, db_session):
    page = _scan_page(client, db_session)
    assert "f.submit()" not in page  # explicit Apply only
    assert 'id="apply-discount-form"' in page


def test_cash_box_is_contextual_and_targets_are_44px(client, db_session):
    page = _scan_page(client, db_session)
    assert "cash-calculator" in page
    assert "method === 'cash' || isSplit" in page  # shown only for cash/split
    assert "is-new" in page  # just-added row flash
    style = open("static/css/style.css", encoding="utf-8").read()
    assert "min-width: 44px; min-height: 44px" in style  # steppers
    assert "width: 44px; height: 44px" in style  # remove disc
    assert "min-height: 44px" in style  # payment radios


def test_confirm_redirects_to_new_sale_with_named_flash(client, db_session):
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket, extra={"payment_method": "cash"})
    assert resp.history and resp.history[0].status_code == 303
    from urllib.parse import unquote
    location = unquote(resp.history[0].headers["location"])
    assert location.startswith("/sales/new?msg=") and "فاکتور" in location
    from models import Sale
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert f"#{sale.id}" in location  # the finished invoice number rides along
    # …and the invoice itself stays reachable from the ledger for print.
    invoice = client.get(f"/sales/invoice/{sale.id}")
    assert invoice.status_code == 200
