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


def test_invoice_print_contract_paper_heading_and_thermal_rules(client, db_session):
    from tests.test_tables import _login
    from models import Sale
    _login(client)
    sale = Sale(total_amount=50_000, final_amount=50_000, payment_method="cash",
                payment_confirmed=True)
    db_session.add(sale)
    db_session.commit()
    page = client.get(f"/sales/invoice/{sale.id}").text
    assert "print-heading" in page  # paper names shop + invoice number
    css = open("static/css/style.css", encoding="utf-8").read()
    for rule in ("@page", ".invoice-item", ".invoice-foot", "#profit-meter"):
        assert rule in css


def test_invoice_phase1_paper_contract(client, db_session):
    """Phase 1 receipt: unit prices, no points, policy, toggle, barcode,
    dialog refund, Persian-digit print swap, emoji hidden on paper."""
    from tests.test_tables import _login
    from datetime import datetime
    from services.store import invalidate_store_cache
    from models import Sale, SaleItem, Customer, Settings, Product, CheckoutSession, StaffUser
    _login(client)
    db_session.add(Customer(id=3, first_name="مشتری", last_name="آزمایشی", phone="09120000003", tier="gold", referral_code="TST0000003"))
    db_session.add(Product(id=11, name="شلوار"))
    db_session.add(Sale(id=21, customer_id=3, total_amount=200_000, discount_amount=20_000,
                        discount_details='["معرف"]', final_amount=180_000,
                        payment_method="cash", payment_confirmed=True))
    db_session.add(SaleItem(id=31, sale_id=21, product_id=11, quantity=2,
                            unit_price=100_000, total_price=200_000))
    db_session.add(Settings(key="store_address", value="تهران، خیابان تست"))
    db_session.add(Settings(key="store_phone", value="021-11111111"))
    db_session.add(StaffUser(id=5, username="sara", password_hash="x",
                             role="cashier", full_name="سارا"))
    db_session.add(CheckoutSession(id=41, checkout_nonce="paper1", customer_id=3,
                                   staff_user_id=5, sale_id=21, state="completed",
                                   total_amount=200_000, final_amount=180_000,
                                   expires_at=datetime(2030, 1, 1),
                                   created_at=datetime(2026, 1, 1),
                                   updated_at=datetime(2026, 1, 1)))
    db_session.commit()
    invalidate_store_cache()
    page = client.get("/sales/invoice/21").text
    assert "۱۰۰٬۰۰۰" not in page  # screen keeps Latin digits
    assert "100,000" in page  # unit price printed per line
    assert "امتیاز کسب‌شده" not in page  # points off the paper
    assert "تعویض کالا با ارائه این فاکتور" in page  # fixed policy line
    assert "تهران، خیابان تست" in page and "021-11111111" in page
    assert "صندوقدار" in page and "سارا" in page
    assert "data-paper" in page and "paper-toggle" in page  # size toggle
    assert "<svg" in page and "بارکد فاکتور" in page  # scannable strip
    assert 'class="em"' in page  # emoji wrapped for paper purge
    assert "data-confirm" in page  # dialog refund…
    assert "return confirm(" not in page  # …not native
    assert "beforeprint" in page  # Persian-digit print swap
    assert "ابطال شد" not in page or "void-mark" in page


def test_invoice_phase1_void_watermark_and_credit_full_info(client, db_session):
    from datetime import datetime
    from tests.test_tables import _login
    from models import Sale, Customer
    _login(client)
    db_session.add(Customer(id=4, first_name="نسیه‌ای", phone="09120000004", tier="silver", referral_code="TST0000004"))
    db_session.add(Sale(id=22, customer_id=4, total_amount=300_000, final_amount=315_000,
                        payment_method="credit", credit_surcharge=15_000,
                        credit_due_date=datetime(2026, 12, 1),
                        payment_confirmed=True, is_refunded=True,
                        refund_reason="اشتباه", refund_date=datetime(2026, 10, 1)))
    db_session.commit()
    page = client.get("/sales/invoice/22").text
    assert "void-mark" in page  # voided prints voided
    assert "کارمزد نسیه" in page and "سررسید" in page  # full credit info
    assert "چاپ فاکتور" in page  # print stays available on voided


def test_code39_strip_shape():
    from services.barcode import code39_svg
    svg = code39_svg(7)
    assert svg.startswith("<svg") and 'viewBox="0 0 ' in svg
    assert svg.count("<rect") == code39_svg(7).count("<rect")
    assert code39_svg("12").count("<rect") > code39_svg("").count("<rect")
    assert code39_svg("12a").count("<rect") == code39_svg("12").count("<rect")  # junk skipped


def _fake_sale(**over):
    from types import SimpleNamespace
    base = dict(id=99, total_amount=200_000, discount_amount=0, discount_details=None,
                final_amount=200_000, payment_method="cash", credit_surcharge=0,
                credit_due_date=None, is_refunded=False, refund_reason=None)
    base.update(over)
    return SimpleNamespace(**base)


def _fake_item(name="شلوار", qty=2, unit=100_000):
    from types import SimpleNamespace
    return SimpleNamespace(unit_price=unit, quantity=qty, total_price=unit * qty,
                           variant=None, product=SimpleNamespace(name=name))


def test_payment_label_single_table():
    from services.invoice import payment_label
    assert payment_label("card") == "کارت"
    assert payment_label("cash") == "نقد"
    assert payment_label("credit") == "نسیه"
    assert payment_label("split") == "ترکیبی"
    assert payment_label("bitcoin") == "نقد"  # unknown never leaks a code


def test_invoice_pdf_generate_once_and_reuse(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # invoices land in scratch, not the repo
    from services.invoice import generate_invoice_pdf
    sale, items = _fake_sale(), [_fake_item()]
    first = generate_invoice_pdf(sale, None, items)
    assert first == "/static/uploads/invoices/invoice_99.pdf"
    second = generate_invoice_pdf(sale, None, items)
    assert second == first  # revisit reuses — no timestamped stacking
    voided = generate_invoice_pdf(_fake_sale(is_refunded=True), None, items)
    assert voided == "/static/uploads/invoices/invoice_99_void.pdf"


def test_invoice_pdf_parity_matrix_no_crash(tmp_path, monkeypatch):
    """Split legs, credit file, void stamp, discount lines, long baskets,
    anonymous buyers — the PDF must survive every shape the HTML can show."""
    monkeypatch.chdir(tmp_path)
    from types import SimpleNamespace
    from services.invoice import generate_invoice_pdf
    parts = [SimpleNamespace(method="cash", amount=60_000),
             SimpleNamespace(method="card", amount=120_000)]
    from datetime import datetime
    cases = [
        _fake_sale(id=1, payment_method="split"),
        _fake_sale(id=2, payment_method="credit", credit_surcharge=15_000,
                   credit_due_date=datetime(2026, 12, 1),
                   discount_amount=20_000, discount_details='["معرف"]'),
        _fake_sale(id=3, is_refunded=True, refund_reason="اشتباه"),
        _fake_sale(id=4, discount_amount=5_000, discount_details=["معرف", "نقدی"]),
        _fake_sale(id=5),
    ]
    customer = SimpleNamespace(first_name="مشتری", last_name="آزمایشی", phone="09120000003")
    for sale in cases:
        path = generate_invoice_pdf(
            sale, customer if sale.id != 5 else None, [_fake_item()] * (30 if sale.id == 5 else 1),
            store={"address": "تهران", "phone": "021-1"}, cashier_name="سارا",
            credit_remaining=300_000, footer_note="متن پایین",
            payment_parts=parts if sale.payment_method == "split" else ())
        assert path and path.startswith("/static/uploads/invoices/invoice_")


def test_invoice_page_links_reused_pdf(client, db_session):
    from datetime import datetime
    from tests.test_tables import _login
    from models import Sale
    _login(client)
    db_session.add(Sale(id=51, total_amount=50_000, final_amount=50_000,
                        payment_method="cash", payment_confirmed=True))
    db_session.commit()
    page = client.get("/sales/invoice/51").text
    assert "/static/uploads/invoices/invoice_51.pdf" in page  # fixed name, reused
