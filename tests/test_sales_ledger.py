"""Sales ledger phase 1: filters, per-page, refund badges, no emoji."""
from models import Sale
from tests.conftest import csrf_token
from tests.test_tables import _login


def _mix(db_session):
    rows = [
        Sale(total_amount=100_000, final_amount=100_000, payment_method="card",
             payment_confirmed=True),
        Sale(total_amount=200_000, final_amount=200_000, payment_method="cash",
             payment_confirmed=True, is_refunded=True, refund_reason="اشتباه"),
        Sale(total_amount=300_000, final_amount=300_000, payment_method="credit",
             payment_confirmed=True),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def test_method_and_refunded_filters_combine(client, db_session):
    _login(client)
    card, cash, credit = _mix(db_session)
    page = client.get("/sales?method=cash&refunded=yes").text
    assert f"#{cash.id}</strong>" in page
    assert f"#{card.id}</strong>" not in page
    assert f"#{credit.id}</strong>" not in page
    assert "ابطال شد" in page and 'title="اشتباه"' in page


def test_refunded_no_and_garbage_degrade(client, db_session):
    _login(client)
    card, cash, credit = _mix(db_session)
    page = client.get("/sales?refunded=no").text
    assert f"#{cash.id}</strong>" not in page
    assert f"#{card.id}</strong>" in page
    garbage = client.get("/sales?method=nope&refunded=maybe&per_page=7").text
    assert f"#{cash.id}</strong>" in garbage  # unknown values read as all/25


def test_ledger_has_no_emoji_and_names_its_columns(client, db_session):
    _login(client)
    _mix(db_session)
    page = client.get("/sales").text
    for emoji in ("📄", "👤", "🕐", "📦", "🏷️", "💰", "🌟", "💳", "🔧", "🧾", "🛒", "🔍", "📒", "💵"):
        assert emoji not in page
    for heading in ("شماره", "مشتری", "تاریخ", "وضعیت", "عملیات"):
        assert heading in page
    assert "sales-table" in page
    assert "فروش جدید" in page


def test_stepper_clamps_to_stock_and_removes_at_zero(client, db_session):
    from tests.test_sales_money import _make_variant
    from tests.test_tables import _login
    import json
    _login(client)
    _, variant = _make_variant(db_session, price=100_000, stock=2)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "name": "تست", "unit_price": 100_000, "unit_cost": 0,
               "quantity": 1, "total_price": 100_000}]
    state = {"customer_id": "0", "basket_json": json.dumps(basket, ensure_ascii=False)}
    up = client.post("/sales/set-quantity", data={
        **state, "variant_id": str(variant.id), "delta": "1",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "2 ×" in up  # quantity stepped to 2
    capped = client.post("/sales/set-quantity", data={
        **state, "basket_json": _basket_of(up, variant.id),
        "variant_id": str(variant.id), "delta": "1",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "کافی نیست" in capped  # stock is 2, stays put
    down = client.post("/sales/set-quantity", data={
        **state, "basket_json": _basket_of(up, variant.id),
        "variant_id": str(variant.id), "delta": "-1",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "1 ×" in down
    gone = client.post("/sales/set-quantity", data={
        **state, "basket_json": _basket_of(down, variant.id),
        "variant_id": str(variant.id), "delta": "-1",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "سبد خرید خالی است" in gone  # zero removes the row


def _basket_of(html, variant_id):
    import json as _json
    import re
    m = re.search(r'name="basket_json" value="(\[.*?\]?)"', html)
    assert m, "re-rendered page carries the basket forward"
    return m.group(1).replace("&quot;", '"').replace("&#34;", '"')


def test_scan_shows_last_added_strip_and_live_step(client, db_session):
    from tests.test_sales_money import _make_variant
    from tests.test_tables import _login
    _login(client)
    _, variant = _make_variant(db_session)
    page = client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode, "basket_json": "[]",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "data-beep" in page and "آخرین:" in page
    assert 'id="timeline-step-2"' in page
    assert "timeline-step done" in page  # step 1 done with a basket


def test_till_has_no_emoji_outside_js_strings(client, db_session):
    from tests.test_sales_money import _make_variant
    from tests.test_tables import _login
    _login(client)
    page = client.get("/sales/new").text
    for emoji in ("📞", "🔍", "👤", "🧸", "✨", "✅", "🧮", "💳", "💵", "📒", "💰", "📦", "🛒"):
        assert emoji not in page
    _, variant = _make_variant(db_session)
    scan = client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode, "basket_json": "[]",
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text
    assert "تخفیف‌ها" in scan  # collapsed discount card renders
    for emoji in ("🔑", "📱", "🧮"):
        assert emoji not in scan
