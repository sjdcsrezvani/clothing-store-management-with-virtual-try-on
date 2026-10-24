"""Phase 4 polish: no inline styles/handlers, Persian labels, and the
screen-reader pass on the till and its invoice."""
import re

from tests.conftest import csrf_token

_EMOJI = re.compile(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]')
_ALLOWED_GLYPHS = {"✕", "✓", "−", "+", "•"}


def _scan_page(client, db_session):
    import json
    from tests.test_sales_money import _make_variant
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session)
    return client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text


def test_no_inline_styles_or_handlers_on_sales_pages(client, db_session):
    page = _scan_page(client, db_session)
    body = page.split("</head>", 1)[1]  # past the theme-token :root block
    assert 'style="' not in body  # token classes own every pixel now
    assert "onclick" not in page and "onsubmit" not in page
    assert "oninput" not in page and "onchange" not in page
    assert "data-sync" in page  # one delegated listener does the mirroring


def test_no_emoji_on_sales_surfaces(client, db_session):
    page = _scan_page(client, db_session)
    for path in ("templates/sales/checkout.html", "templates/sales/invoice.html",
                 "templates/partials/buys_for.html"):
        found = [c for c in set(_EMOJI.findall(
            open(path, encoding="utf-8").read())) if c not in _ALLOWED_GLYPHS]
        assert not found, (path, found)
    assert "آخرین اسکن:" in page  # plain words, not a clipped label


def test_screen_reader_landmarks_on_till(client, db_session):
    page = _scan_page(client, db_session)
    assert "<fieldset" in page and "روش پرداخت" in page  # method group named
    assert "<ol" in page and 'aria-current="step"' in page  # timeline is a list
    assert 'role="status"' in page  # just-added strip announces


def test_invoice_names_credit_leg_and_prints_without_handlers(client, db_session):
    from models import Sale, SaleItem, SalePaymentPart
    from tests.test_sales_money import _make_variant
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    sale = Sale(total_amount=100_000, final_amount=100_000, payment_method="split",
                payment_confirmed=True)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=variant.product_id,
                            variant_id=variant.id, quantity=1,
                            unit_price=100_000, unit_cost=50_000, total_price=100_000))
    db_session.add(SalePaymentPart(sale_id=sale.id, method="cash", amount=40000))
    db_session.add(SalePaymentPart(sale_id=sale.id, method="credit", amount=60000))
    db_session.commit()
    page = client.get(f"/sales/invoice/{sale.id}").text
    assert "نسیه" in page  # the Phase-1 leg the invoice could not name
    assert "onclick" not in page and "data-print" in page
    assert 'role="status"' in page  # the recorded flash announces
    assert "فیش حرارتی" in page and "دانلود فاکتور (PDF)" in page


def test_sales_templates_balance_scripts(client, db_session):
    _scan_page(client, db_session)
    for path in ("templates/sales/checkout.html", "templates/sales/invoice.html"):
        text = open(path, encoding="utf-8").read()
        opens = len(re.findall(r"<script(?![a-zA-Z])", text))
        closes = len(re.findall(r"</script\s*>", text))
        assert opens == closes, (path, opens, closes)
