"""Accounting-lite: نسیه (credit sales), payments, purchases, expenses, P&L, CSV."""
import re

from models import Customer, Expense, Payment, Purchase, PurchaseItem, Sale, Product, ProductVariant, StockMovement
from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_customer, _make_variant


def _post(client, url, data, authed):
    token = csrf_token(client, "/admin/")
    data = dict(data)
    data["csrf_token"] = token
    return client.post(url, data=data, follow_redirects=False)


def _post_invoice(client, data, authed):
    """Record a supplier invoice the way the UI does: draft, then finalise.

    Only finalising touches the cost basis, the payable or the cash register.
    """
    response = _post(client, "/admin/purchases/add", data, authed)
    match = re.search(r"/admin/purchases/(\d+)", response.headers.get("location", ""))
    assert match, response.headers.get("location")
    finalized = _post(client, f"/admin/purchases/{match.group(1)}/finalize", {}, authed)
    assert finalized.status_code == 303
    return response


def test_credit_sale_creates_customer_debt(client, db_session):
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 2, "total_price": 200_000}]
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})
    assert resp.status_code == 200

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.payment_method == "credit"
    assert sale.credit_settled is False
    assert sale.credit_paid_amount == 0
    db_session.refresh(customer)
    assert customer.total_debt == 200_000
    db_session.refresh(variant)
    assert variant.stock_quantity == 3


def test_credit_sale_requires_registered_customer(client, db_session):
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket, customer_id=0, extra={"payment_method": "credit"})
    assert resp.status_code == 200
    assert "نسیه فقط برای مشتری" in resp.text
    assert db_session.query(Sale).count() == 0


def test_payment_settles_debt_fifo(client, db_session, authed):
    customer = _make_customer(db_session)
    _, v1 = _make_variant(db_session, price=100_000, stock=10)
    _, v2 = _make_variant(db_session, price=200_000, stock=10)
    for variant, qty in ((v1, 1), (v2, 1)):
        basket = [{"variant_id": variant.id, "product_id": variant.product_id,
                   "unit_price": variant.price, "quantity": qty, "total_price": variant.price}]
        _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})

    db_session.refresh(customer)
    assert customer.total_debt == 300_000
    sales = db_session.query(Sale).order_by(Sale.id.asc()).all()
    assert [s.final_amount for s in sales] == [100_000, 200_000]

    # Partial payment of 150k → oldest sale fully paid, second half paid.
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "150000", "method": "cash", "note": "قسط اول"}, authed)
    db_session.refresh(customer)
    assert customer.total_debt == 150_000
    db_session.refresh(sales[0])
    db_session.refresh(sales[1])
    assert sales[0].credit_settled is True
    assert sales[0].credit_paid_amount == 100_000
    assert sales[1].credit_paid_amount == 50_000

    # Remaining payment settles everything.
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "150000", "method": "card"}, authed)
    db_session.refresh(customer)
    assert customer.total_debt == 0
    db_session.refresh(sales[1])
    assert sales[1].credit_settled is True
    assert db_session.query(Payment).count() == 2


def test_refund_of_credit_sale_reduces_debt(client, db_session):
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})
    db_session.refresh(customer)
    assert customer.total_debt == 100_000

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    token = csrf_token(client, f"/sales/invoice/{sale.id}")
    client.post(f"/sales/{sale.id}/refund",
                data={"refund_reason": "تست", "csrf_token": token}, follow_redirects=False)

    db_session.refresh(customer)
    assert customer.total_debt == 0


def test_new_product_initial_stock_creates_opening_movement(client, db_session, authed):
    response = _post(client, "/admin/products/add", {
        "name": "محصول اولیه",
        "category": "لباس",
        "brand": "",
        "description": "",
        "variant_index_0": "0",
        "variant_size_0": "",
        "variant_color_0": "",
        "variant_price_0": "100000",
        "variant_cost_price_0": "50000",
        "variant_stock_0": "4",
        "variant_barcode_0": "OPEN-001",
        "variant_sku_0": "",
    }, authed)
    assert response.status_code == 303
    variant = db_session.query(ProductVariant).filter(ProductVariant.barcode == "OPEN-001").one()
    assert variant.stock_quantity == 4
    movement = db_session.query(StockMovement).filter(
        StockMovement.variant_id == variant.id,
        StockMovement.movement_type == "opening_stock",
    ).one()
    assert movement.quantity_delta == 4
    assert movement.unit_cost == 50_000


def test_purchase_updates_cost_but_never_stock(client, db_session, authed):
    """Buying stock records what it cost; where stock is counted is decided on
    the product screens, so a purchase must leave quantities alone."""
    from models import Supplier
    supplier = Supplier(name="عمده‌فروش مرکزی")
    db_session.add(supplier)
    db_session.commit()
    db_session.refresh(supplier)

    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5)
    _post_invoice(client, {
        "supplier_id": str(supplier.id),
        "note": "فاکتور اول",
        "purchase_variant_0": str(variant.id),
        "purchase_qty_0": "10",
        "purchase_cost_0": "45000",
    }, authed)

    db_session.refresh(variant)
    assert variant.stock_quantity == 5
    assert variant.cost_price == 45_000
    purchase = db_session.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.total_cost == 450_000
    assert db_session.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id).count() == 1
    # The cost change is auditable, and writes no stock movement.
    movements = db_session.query(StockMovement).filter(StockMovement.purchase_id == purchase.id).all()
    assert [m.movement_type for m in movements] == ["cost_adjustment"]
    assert movements[0].quantity_delta == 0

    # Reversing keeps the purchase row, restores the previous cost basis and
    # leaves stock exactly where it was.
    _post(client, f"/admin/purchases/{purchase.id}/delete", {}, authed)
    db_session.refresh(variant)
    db_session.refresh(purchase)
    assert variant.stock_quantity == 5
    assert variant.cost_price == 50_000
    assert purchase.is_reversed is True
    assert db_session.query(Purchase).filter(Purchase.id == purchase.id).count() == 1
    assert db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
        StockMovement.movement_type == "purchase_reversal",
    ).count() == 0

    from datetime import datetime, timezone, timedelta
    from services.accounting import debt_totals, get_cashbox
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    assert debt_totals(db_session)["total_purchases"] == 0
    assert get_cashbox(db_session, start, end, 0)["purchases"] == 0


def test_purchase_reversal_needs_no_stock_lock(client, db_session, authed):
    """Sales cannot be invalidated by a reversal, because a purchase never put
    the units into stock in the first place."""
    from models import Supplier
    supplier = Supplier(name="قفل موجودی")
    db_session.add(supplier)
    db_session.commit()
    db_session.refresh(supplier)

    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=10, name="بدون قفل موجودی")
    _post_invoice(client, {
        "supplier_id": str(supplier.id),
        "note": "خرید قابل تطبیق",
        "purchase_variant_0": str(variant.id),
        "purchase_qty_0": "10",
        "purchase_cost_0": "45000",
    }, authed)
    purchase = db_session.query(Purchase).order_by(Purchase.id.desc()).first()

    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 8, "total_price": 800_000}]
    _confirm_sale(client, basket, extra={"payment_method": "cash"})

    response = _post(client, f"/admin/purchases/{purchase.id}/delete", {}, authed)
    assert response.status_code == 303
    db_session.refresh(purchase)
    db_session.refresh(variant)
    assert purchase.is_reversed is True
    # Only the sale moved stock: 10 − 8.
    assert variant.stock_quantity == 2
    assert variant.cost_price == 50_000


def test_multiple_purchase_reversal_keeps_latest_cost_basis(client, db_session, authed):
    """Reversing an older receipt never overwrites a later receipt cost."""
    from models import Supplier
    supplier = Supplier(name="چند خرید تأمین‌کننده")
    db_session.add(supplier)
    db_session.commit()
    db_session.refresh(supplier)

    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="چند خرید")
    for qty, cost in ((10, 45_000), (5, 40_000)):
        _post_invoice(client, {
            "supplier_id": str(supplier.id),
            "note": "خرید کالا",
            "purchase_variant_0": str(variant.id),
            "purchase_qty_0": str(qty),
            "purchase_cost_0": str(cost),
        }, authed)
    purchases = db_session.query(Purchase).order_by(Purchase.id.asc()).all()
    _post(client, f"/admin/purchases/{purchases[0].id}/delete", {}, authed)

    db_session.refresh(variant)
    db_session.refresh(purchases[0])
    assert purchases[0].is_reversed is True
    assert variant.stock_quantity == 0
    assert variant.cost_price == 40_000
    assert db_session.query(Purchase).count() == 2


def test_manual_stock_edit_creates_adjustment_movement(client, db_session, authed):
    """Editing stock changes the balance through an auditable adjustment."""
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5, name="اصلاح موجودی")
    _post(client, f"/admin/variants/{variant.id}", {
        "size": "", "color": "", "price": "100000", "cost_price": "50000",
        "fake_cost_price": "", "stock_quantity": "7", "barcode": variant.barcode,
        "sku": "", "tryon_details": "",
    }, authed)

    db_session.refresh(variant)
    assert variant.stock_quantity == 7
    movement = db_session.query(StockMovement).filter(
        StockMovement.variant_id == variant.id,
        StockMovement.movement_type == "adjustment",
    ).one()
    assert movement.quantity_delta == 2


def _visible_cost_on_edit_page(client, variant):
    """Read the value rendered in the single visible cost field of the edit page."""
    page = client.get(f"/admin/variants/{variant.id}/edit")
    assert page.status_code == 200
    match = re.search(r'id="cost_price".*?value="(\d*)"', page.text, re.S)
    assert match, "cost price field not rendered"
    return match.group(1)


def test_second_cost_price_is_display_only(client, db_session, authed):
    """A second cost price mirrors into the cost field without touching the real cost basis."""
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5, name="قیمت دوم")

    _post(client, f"/admin/variants/{variant.id}", {
        "size": "", "color": "", "price": "100000", "cost_price": "9000",
        "fake_cost_price": "9000", "stock_quantity": "5", "barcode": variant.barcode,
        "sku": "", "tryon_details": "",
    }, authed)

    db_session.refresh(variant)
    assert variant.fake_cost_price == 9_000
    assert variant.cost_price == 50_000
    assert _visible_cost_on_edit_page(client, variant) == "9000"
    # Display-only: no cost-basis adjustment is recorded for the real cost.
    assert db_session.query(StockMovement).filter(
        StockMovement.variant_id == variant.id,
        StockMovement.movement_type == "cost_adjustment",
    ).count() == 0


def test_clearing_second_cost_price_falls_back_to_real_cost(client, db_session, authed):
    """Clearing the second cost hides it and shows the real (creation) cost again."""
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5, name="حذف قیمت دوم")
    variant.fake_cost_price = 9_000
    db_session.commit()

    _post(client, f"/admin/variants/{variant.id}", {
        "size": "", "color": "", "price": "100000", "cost_price": "9000",
        "fake_cost_price": "", "stock_quantity": "5", "barcode": variant.barcode,
        "sku": "", "tryon_details": "",
    }, authed)

    db_session.refresh(variant)
    assert variant.fake_cost_price is None
    assert variant.cost_price == 50_000
    assert _visible_cost_on_edit_page(client, variant) == "50000"


def test_second_cost_price_survives_app_restart(client, db_session, authed):
    """The second cost is a real column: a fresh app start still reads it back."""
    from fastapi.testclient import TestClient

    from main import app

    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=5, name="بقای قیمت دوم")
    _post(client, f"/admin/variants/{variant.id}", {
        "size": "", "color": "", "price": "100000", "cost_price": "9000",
        "fake_cost_price": "9000", "stock_quantity": "5", "barcode": variant.barcode,
        "sku": "", "tryon_details": "",
    }, authed)

    db_session.expire_all()
    variant_id = variant.id

    # A second TestClient boots the app afresh against the same database file.
    with TestClient(app) as restarted:
        token = csrf_token(restarted)
        resp = restarted.post("/admin/login", data={
            "username": "owner", "password": "test-admin-pass", "csrf_token": token,
        }, follow_redirects=False)
        assert resp.status_code == 303
        page = restarted.get(f"/admin/variants/{variant_id}/edit")
        assert page.status_code == 200
        match = re.search(r'id="cost_price".*?value="(\d*)"', page.text, re.S)
        assert match and match.group(1) == "9000"

    stored = db_session.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
    assert stored.fake_cost_price == 9_000
    assert stored.cost_price == 50_000


def test_second_cost_price_cannot_be_set_on_creation(client, db_session, authed):
    """Second cost price lives on the edit page only — creation must ignore it."""
    _post(client, "/admin/products/add", {
        "name": "محصول بدون قیمت دوم", "category": "", "brand": "", "description": "",
        "variant_index_0": "0", "variant_size_0": "", "variant_color_0": "",
        "variant_price_0": "100000", "variant_cost_price_0": "50000",
        "variant_stock_0": "0", "variant_barcode_0": "", "variant_sku_0": "",
        "fake_cost_price": "9999",
    }, authed)

    variant = db_session.query(ProductVariant).filter(ProductVariant.price == 100_000).one()
    assert variant.cost_price == 50_000
    assert variant.fake_cost_price is None


def test_expense_types_are_saved_and_reported(client, db_session, authed):
    _post(client, "/admin/expenses/add", {
        "amount": "1500000",
        "category": "اجاره",
        "expense_type": "monthly",
        "note": "اجاره ماهانه",
    }, authed)
    _post(client, "/admin/expenses/add", {
        "amount": "250000",
        "category": "تعمیرات",
        "expense_type": "one_time",
        "note": "تعمیر قفسه",
    }, authed)

    expenses = db_session.query(Expense).order_by(Expense.id.asc()).all()
    assert [expense.expense_type for expense in expenses] == ["monthly", "one_time"]

    from datetime import datetime, timezone, timedelta
    from services.accounting import get_net_pl
    from services.reporting import canonical_report
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    pl = get_net_pl(db_session, start, end)
    report = canonical_report(db_session, start, end)
    assert pl["expense_type_totals"] == {"one_time": 250_000, "monthly": 1_500_000}
    assert report["one_time_expenses"] == 250_000
    assert report["monthly_expenses"] == 1_500_000

    page = client.get("/admin/expenses")
    assert page.status_code == 200
    assert "هزینه‌های ماهانه" in page.text
    assert "هزینه‌های یک‌باره" in page.text
    assert "اجاره ماهانه" in page.text


def test_invalid_expense_type_is_rejected(client, db_session, authed):
    response = _post(client, "/admin/expenses/add", {
        "amount": "100000",
        "category": "متفرقه",
        "expense_type": "weekly",
        "note": "نامعتبر",
    }, authed)
    assert response.status_code == 303
    assert db_session.query(Expense).count() == 0


def test_net_pl_calculation(client, db_session):
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=200_000, cost=120_000, stock=10)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 200_000, "quantity": 2, "total_price": 400_000}]
    _confirm_sale(client, basket, customer_id=customer.id)
    db_session.add(Expense(amount=100_000, category="متفرقه"))
    db_session.commit()

    from datetime import datetime, timezone, timedelta
    from services.accounting import get_net_pl
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    pl = get_net_pl(db_session, start, end)
    assert pl["revenue"] == 400_000
    assert pl["cogs"] == 240_000
    assert pl["gross"] == 160_000
    assert pl["expenses"] == 100_000
    assert pl["net"] == 60_000


def test_csv_exports(client, db_session, authed):
    _make_customer(db_session, phone="09120000009")
    resp = client.get("/admin/accounting/export?kind=customers")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert resp.text.startswith("\ufeff")  # BOM for Excel
    assert "09120000009" in resp.text

    resp = client.get("/admin/accounting/export?kind=sales")
    assert resp.status_code == 200
    assert "شماره" in resp.text

    resp = client.get("/admin/accounting/export?kind=purchases")
    assert resp.status_code == 200
    resp = client.get("/admin/accounting/export?kind=expenses")
    assert resp.status_code == 200


def test_cashbox_register(client, db_session, authed):
    # Set opening balance, add a cash sale + an expense, then check the register.
    _post(client, "/admin/cashbox/opening", {"opening": "1000000"}, authed)

    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "cash"})
    db_session.add(Expense(amount=50_000, category="متفرقه"))
    db_session.commit()

    from datetime import datetime, timezone, timedelta
    from services.accounting import get_cashbox, get_opening_balance
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    register = get_cashbox(db_session, start, end, get_opening_balance(db_session))
    assert register["cash_sales"] == 100_000
    assert register["expenses"] == 50_000
    assert register["closing"] == 1_000_000 + 100_000 - 50_000


def test_delete_expense_restores_cashbox(client, db_session, authed):
    """Expenses are leaf records: deleting one restores the cash box and P&L."""
    _post(client, "/admin/expenses/add", {"amount": "500000", "category": "اجاره", "note": "تست"}, authed)
    expense = db_session.query(Expense).order_by(Expense.id.desc()).first()
    assert expense.amount == 500_000

    from datetime import datetime, timezone, timedelta
    from services.accounting import get_cashbox, get_opening_balance
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    register = get_cashbox(db_session, start, end, get_opening_balance(db_session))
    assert register["expenses"] == 500_000

    _post(client, f"/admin/expenses/{expense.id}/delete", {}, authed)
    assert db_session.query(Expense).count() == 1
    assert db_session.query(Expense).filter(Expense.reversed_at.is_(None)).count() == 0
    register = get_cashbox(db_session, start, end, get_opening_balance(db_session))
    assert register["expenses"] == 0
    assert register["closing"] == 0


def test_delete_payment_restores_debt_and_allocation(client, db_session, authed):
    """Deleting a payment rebuilds the FIFO allocation from remaining payments:
    debt, per-invoice paid amounts and settled flags all come back consistent."""
    customer = _make_customer(db_session)
    _, v1 = _make_variant(db_session, price=100_000, stock=10)
    _, v2 = _make_variant(db_session, price=200_000, stock=10)
    for variant in (v1, v2):
        basket = [{"variant_id": variant.id, "product_id": variant.product_id,
                   "unit_price": variant.price, "quantity": 1, "total_price": variant.price}]
        _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})

    sales = db_session.query(Sale).order_by(Sale.id.asc()).all()
    # Payment A: 150k → oldest (100k) settled, 50k toward the second.
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "150000", "method": "cash"}, authed)
    # Payment B: 100k → second invoice now 150k paid, debt 50k.
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "100000", "method": "card"}, authed)
    db_session.refresh(customer)
    assert customer.total_debt == 50_000
    assert db_session.query(Payment).count() == 2

    # Undo payment A (the 150k cash receipt).
    payment_a = db_session.query(Payment).order_by(Payment.id.asc()).first()
    _post(client, f"/admin/payments/{payment_a.id}/delete", {}, authed)

    db_session.refresh(customer)
    db_session.refresh(sales[0])
    db_session.refresh(sales[1])
    assert db_session.query(Payment).filter(Payment.reversed_at.is_(None)).count() == 1
    assert customer.total_debt == 200_000
    # Remaining 100k now covers the oldest invoice only.
    assert sales[0].credit_settled is True
    assert sales[0].credit_paid_amount == 100_000
    assert sales[1].credit_settled is False
    assert sales[1].credit_paid_amount == 0


def test_credit_limit_default_blocks_over_limit(client, db_session, authed):
    """Store-wide default credit limit blocks a نسیه sale that would exceed it."""
    from models import Settings
    db_session.add(Settings(key="default_credit_limit", value="150000"))
    db_session.commit()

    customer = _make_customer(db_session)
    _, v1 = _make_variant(db_session, price=100_000, stock=10, name="تیشرت اول")
    _, v2 = _make_variant(db_session, price=100_000, stock=10, name="تیشرت دوم")
    # First credit sale: debt 100k.
    basket1 = [{"variant_id": v1.id, "product_id": v1.product_id,
                "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    _confirm_sale(client, basket1, customer_id=customer.id, extra={"payment_method": "credit"})
    db_session.refresh(customer)
    assert customer.total_debt == 100_000

    # Second 100k credit sale would push debt to 200k > 150k limit → blocked.
    basket2 = [{"variant_id": v2.id, "product_id": v2.product_id,
                "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket2, customer_id=customer.id, extra={"payment_method": "credit"})
    assert resp.status_code == 200
    assert "سقف اعتبار" in resp.text
    assert db_session.query(Sale).count() == 1  # nothing new created
    db_session.refresh(customer)
    assert customer.total_debt == 100_000

    # A 50k credit sale fits exactly within the remaining 50k → allowed.
    _, v3 = _make_variant(db_session, price=50_000, stock=10, name="تیشرت سوم")
    basket3 = [{"variant_id": v3.id, "product_id": v3.product_id,
                "unit_price": 50_000, "quantity": 1, "total_price": 50_000}]
    resp = _confirm_sale(client, basket3, customer_id=customer.id, extra={"payment_method": "credit"})
    assert resp.status_code == 200
    assert "سقف اعتبار" not in resp.text
    assert db_session.query(Sale).count() == 2
    db_session.refresh(customer)
    assert customer.total_debt == 150_000


def test_credit_limit_per_customer_override(client, db_session, authed):
    """A per-customer limit overrides the store default; resetting returns to default."""
    from models import Settings
    db_session.add(Settings(key="default_credit_limit", value="1000000"))
    db_session.commit()
    customer = _make_customer(db_session)

    # Set a strict per-customer cap of 300k.
    _post(client, f"/admin/credit/{customer.id}/limit", {"credit_limit": "300000"}, authed)
    db_session.refresh(customer)
    assert customer.credit_limit == 300_000

    # 350k credit sale → blocked by the 300k cap.
    _, variant = _make_variant(db_session, price=350_000, stock=10)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 350_000, "quantity": 1, "total_price": 350_000}]
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})
    assert "سقف اعتبار" in resp.text
    assert db_session.query(Sale).count() == 0

    # Reset to default (0 in the form) → unlimited again.
    _post(client, f"/admin/credit/{customer.id}/limit", {"credit_limit": "0"}, authed)
    db_session.refresh(customer)
    assert customer.credit_limit is None
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})
    assert "سقف اعتبار" not in resp.text
    assert db_session.query(Sale).count() == 1


def test_delete_payment_restores_cashbox(client, db_session, authed):
    """A cash نسیه receipt shows as cash-box inflow; deleting it removes it."""
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "100000", "method": "cash"}, authed)

    from datetime import datetime, timezone, timedelta
    from services.accounting import get_cashbox, get_opening_balance
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    register = get_cashbox(db_session, start, end, get_opening_balance(db_session))
    assert register["credit_payments"] == 100_000
    assert register["closing"] == 100_000

    payment = db_session.query(Payment).first()
    _post(client, f"/admin/payments/{payment.id}/delete", {}, authed)
    db_session.refresh(customer)
    assert customer.total_debt == 100_000  # debt is back
    register = get_cashbox(db_session, start, end, get_opening_balance(db_session))
    assert register["credit_payments"] == 0
    assert register["closing"] == 0


# ── نسیه surcharge: no discount, configurable % added ────────────────────────

def test_credit_sale_no_discount(client, db_session):
    """نسیه sales take no discount even when a customer is eligible."""
    customer = _make_customer(db_session, tier="gold",
                              referrer_discount=50_000, referred_discount=30_000)
    _, variant = _make_variant(db_session, price=600_000, stock=10)  # ≥ min purchase
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 600_000, "quantity": 1, "total_price": 600_000}]
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={
        "payment_method": "credit",
        "use_referrer_discount": "1",
    })
    assert resp.status_code == 200

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.payment_method == "credit"
    assert sale.discount_amount == 0          # no discount on نسیه
    assert sale.final_amount == 600_000        # subtotal, no surcharge yet
    db_session.refresh(customer)
    assert customer.total_debt == 600_000
    # The loyalty discount was NOT consumed.
    assert customer.has_used_referred_discount is False
    assert customer.referrer_discount == 50_000


def test_credit_surcharge_percent_added(client, db_session):
    """A configured surcharge % is added to the نسیه subtotal; discounts stay zero."""
    from models import Settings
    db_session.add(Settings(key="credit_surcharge_percent", value="5"))
    db_session.commit()

    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=200_000, stock=10)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 200_000, "quantity": 2, "total_price": 400_000}]
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={
        "payment_method": "credit",
        "custom_discount_percent": "10",  # must be ignored on نسیه
    })
    assert resp.status_code == 200

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.discount_amount == 0
    assert sale.credit_surcharge == 20_000    # 5% of 400_000
    assert sale.final_amount == 420_000       # 400k + 20k surcharge
    db_session.refresh(customer)
    assert customer.total_debt == 420_000     # debt includes the surcharge


def test_credit_surcharge_off_by_default(client, db_session):
    """Without the setting, نسیه has no surcharge — just the raw subtotal."""
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=10)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    _confirm_sale(client, basket, customer_id=customer.id, extra={"payment_method": "credit"})

    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.credit_surcharge == 0
    assert sale.final_amount == 100_000


# ── Aged receivables (collection dashboard) ─────────────────────────────────

def test_aged_receivables_buckets(client, db_session, authed):
    """Debtors are bucketed by the age of their oldest unsettled نسیه invoice.

    Invoices with no سررسید age by their own date, which is what the old
    collections report did — so an existing debtor cannot jump buckets just
    because the column appeared. (These three are cleared to model that.)
    """
    from datetime import datetime, timezone, timedelta
    from services.accounting import build_debt_rows, summarise_debts
    from models import Settings
    # No credit limit to interfere.
    db_session.add(Settings(key="default_credit_limit", value="0"))
    db_session.commit()

    now = datetime.now(timezone.utc)

    # Customer A: oldest invoice 10 days ago → current bucket
    cust_a = _make_customer(db_session, phone="09120000011")
    _, va = _make_variant(db_session, price=100_000, stock=10, name="کلاه الف")
    _confirm_sale(client, [{"variant_id": va.id, "product_id": va.product_id,
                           "unit_price": 100_000, "quantity": 1, "total_price": 100_000}],
                  customer_id=cust_a.id, extra={"payment_method": "credit"})
    sale_a = db_session.query(Sale).order_by(Sale.id.desc()).first()
    sale_a.created_at = now - timedelta(days=10)
    sale_a.credit_due_date = None  # a row that predates سررسید
    db_session.flush()

    # Customer B: oldest invoice 45 days ago → 31-60 bucket
    cust_b = _make_customer(db_session, phone="09120000022")
    _, vb = _make_variant(db_session, price=200_000, stock=10, name="کلاه ب")
    _confirm_sale(client, [{"variant_id": vb.id, "product_id": vb.product_id,
                           "unit_price": 200_000, "quantity": 1, "total_price": 200_000}],
                  customer_id=cust_b.id, extra={"payment_method": "credit"})
    sale_b = db_session.query(Sale).order_by(Sale.id.desc()).first()
    sale_b.created_at = now - timedelta(days=45)
    sale_b.credit_due_date = None
    db_session.flush()

    # Customer C: oldest invoice 120 days ago → over_90 bucket
    cust_c = _make_customer(db_session, phone="09120000033")
    _, vc = _make_variant(db_session, price=300_000, stock=10, name="کلاه ج")
    _confirm_sale(client, [{"variant_id": vc.id, "product_id": vc.product_id,
                           "unit_price": 300_000, "quantity": 1, "total_price": 300_000}],
                  customer_id=cust_c.id, extra={"payment_method": "credit"})
    sale_c = db_session.query(Sale).order_by(Sale.id.desc()).first()
    sale_c.created_at = now - timedelta(days=120)
    sale_c.credit_due_date = None
    db_session.commit()

    # Refresh customer debt from the committed state.
    db_session.refresh(cust_a); db_session.refresh(cust_b); db_session.refresh(cust_c)
    rows = build_debt_rows(db_session, now=now)
    by_phone = {row["customer"].phone: row for row in rows}
    assert by_phone["09120000011"]["bucket"] == "current"
    assert by_phone["09120000022"]["bucket"] == "31_60"
    assert by_phone["09120000033"]["bucket"] == "over_90"

    overview = summarise_debts(rows, now=now)
    assert overview["buckets"]["current"] == 100_000
    assert overview["buckets"]["31_60"] == 200_000
    assert overview["buckets"]["over_90"] == 300_000
    assert overview["buckets"]["61_90"] == 0
    assert overview["total_debt"] == 600_000
    assert overview["overdue_amount"] == 500_000

    # Fully settle customer A → out of the report entirely.
    _post(client, "/admin/credit/pay",
          {"customer_id": cust_a.id, "amount": "100000", "method": "cash"}, authed)
    db_session.commit()
    db_session.expire_all()
    overview = summarise_debts(build_debt_rows(db_session, now=now), now=now)
    assert overview["buckets"]["current"] == 0
    assert overview["total_debt"] == 500_000


def test_collections_redirects_into_the_credit_page(client, db_session, authed):
    """There is one نسیه page now; the old dashboard URL forwards to it."""
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=150_000, stock=10, name="شلوار تست")
    _confirm_sale(client, [{"variant_id": variant.id, "product_id": variant.product_id,
                           "unit_price": 150_000, "quantity": 1, "total_price": 150_000}],
                  customer_id=customer.id, extra={"payment_method": "credit"})

    resp = client.get("/admin/collections", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/credit")

    # …and following it lands on the merged page with the money on it.
    page = client.get("/admin/collections")
    assert page.status_code == 200
    assert "حساب نسیه" in page.text
    assert "۱۵۰٬۰۰۰" in page.text or "150,000" in page.text
