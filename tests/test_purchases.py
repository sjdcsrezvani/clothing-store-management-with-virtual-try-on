"""Purchases: draft invoices, landed cost, invoice periods, settlement and reversal."""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from models import (
    BusinessEvent, Product, Purchase, PurchaseItem, StockMovement, Supplier, SupplierPayment,
)
from services.accounting import (
    get_cashbox,
    get_supplier_balances,
    purchase_overview,
    purchase_settlement,
)
from tests.conftest import csrf_token
from tests.test_sales_money import _make_variant


def _post(client, url, data, authed=None):
    data = dict(data)
    data["csrf_token"] = csrf_token(client, "/admin/")
    return client.post(url, data=data, follow_redirects=False)


def _redirect_text(response) -> str:
    """Decoded redirect target: Persian query strings arrive percent-encoded."""
    return unquote(response.headers.get("location", ""))


def _make_supplier(db_session, name="عمده‌فروش تست"):
    supplier = Supplier(name=name)
    db_session.add(supplier)
    db_session.commit()
    db_session.refresh(supplier)
    return supplier


def _range(days_ago=1, days_ahead=1):
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days_ago), now + timedelta(days=days_ahead)


def _latest_purchase(db_session) -> Purchase:
    return db_session.query(Purchase).order_by(Purchase.id.desc()).first()


def _create_draft(client, variant, quantity=10, unit_cost=45_000, **extra):
    """Post the invoice form, which only ever creates a draft."""
    data = {
        "note": "فاکتور خرید",
        "purchase_variant_0": str(variant.id),
        "purchase_qty_0": str(quantity),
        "purchase_cost_0": str(unit_cost),
    }
    data.update(extra)
    return _post(client, "/admin/purchases/add", data)


def _finalize(client, purchase_id: int, payment_amount=0, **extra):
    data = {"payment_amount": str(payment_amount)}
    data.update(extra)
    return _post(client, f"/admin/purchases/{purchase_id}/finalize", data)


def _add_purchase(client, variant, quantity=10, unit_cost=45_000, payment_amount=0,
                  finalize=True, **extra):
    """Create the invoice the way the UI does: draft first, then finalise it."""
    response = _create_draft(client, variant, quantity=quantity, unit_cost=unit_cost, **extra)
    if not finalize or response.status_code != 303:
        return response
    match = re.search(r"/admin/purchases/(\d+)", response.headers.get("location", ""))
    if not match:
        return response
    finalized = _finalize(client, int(match.group(1)), payment_amount=payment_amount)
    assert finalized.status_code == 303
    return response


def _catalog(client) -> list:
    """The product catalogue the picker searches, as rendered into the page."""
    page = client.get("/admin/purchases")
    assert page.status_code == 200
    match = re.search(
        r'<script type="application/json" id="purchase-catalog">(.*?)</script>',
        page.text, re.S,
    )
    assert match, "the purchases page must ship its product catalogue"
    return json.loads(match.group(1))


def test_draft_invoice_touches_nothing_until_finalized(client, db_session, authed):
    """A draft is a plan: no cost basis, no supplier debt, no cash movement."""
    supplier = _make_supplier(db_session, name="پیش‌نویس")
    _, variant = _make_variant(db_session, price=100_000, cost=40_000, stock=5, name="پیش‌نویس کالا")

    response = _create_draft(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), extra_cost="50000",
    )
    assert response.status_code == 303

    purchase = _latest_purchase(db_session)
    assert purchase.is_draft is True
    assert purchase.total_cost == 500_000
    assert purchase.amount_paid in (None, 0)

    db_session.refresh(variant)
    assert variant.cost_price == 40_000  # untouched
    assert db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
    ).count() == 0
    assert db_session.query(BusinessEvent).filter(
        BusinessEvent.event_type == "PurchaseRecorded",
    ).count() == 0

    balances = {row["supplier"].id: row for row in get_supplier_balances(db_session)}
    assert balances[supplier.id]["invoiced"] == 0
    assert balances[supplier.id]["owed"] == 0

    start, end = _range()
    assert get_cashbox(db_session, start, end, 0)["purchases"] == 0
    overview = purchase_overview(db_session, start, end)
    assert overview["period_spend"] == 0
    assert overview["period_count"] == 0
    assert overview["period_units"] == 0
    assert overview["supplier_owed"] == 0

    settlement = purchase_settlement(db_session, purchase)
    assert settlement["status"] == "draft"
    assert settlement["paid"] == 0
    assert settlement["overdue"] is False


def test_finalizing_a_draft_applies_cost_and_debt_once(client, db_session, authed):
    supplier = _make_supplier(db_session, name="نهایی‌سازی")
    _, variant = _make_variant(db_session, price=100_000, cost=40_000, stock=5, name="نهایی‌سازی کالا")

    response = _create_draft(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), extra_cost="50000",
    )
    purchase = _latest_purchase(db_session)
    assert purchase.is_draft is True

    finalized = _finalize(client, purchase.id)
    assert finalized.status_code == 303

    db_session.refresh(purchase)
    assert purchase.is_draft is False
    db_session.refresh(variant)
    assert variant.cost_price == 50_000  # 45,000 + 5,000 shipping per unit
    assert variant.stock_quantity == 5  # purchases never move stock

    movement = db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
        StockMovement.movement_type == "cost_adjustment",
    ).one()
    assert movement.quantity_delta == 0
    assert movement.unit_cost == 50_000

    assert db_session.query(BusinessEvent).filter(
        BusinessEvent.event_type == "PurchaseRecorded",
        BusinessEvent.aggregate_id == purchase.id,
    ).count() == 1

    balances = {row["supplier"].id: row for row in get_supplier_balances(db_session)}
    assert balances[supplier.id]["invoiced"] == 500_000
    assert balances[supplier.id]["owed"] == 500_000

    # Finalising twice must not apply anything a second time.
    again = _finalize(client, purchase.id)
    assert "err=" in _redirect_text(again)
    db_session.refresh(variant)
    assert variant.cost_price == 50_000
    assert db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
    ).count() == 1


def test_purchase_spreads_shipping_into_landed_cost(client, db_session, authed):
    supplier = _make_supplier(db_session, name="لندد")
    # Starts at a different cost so the landed-cost change is a real change.
    _, variant = _make_variant(db_session, price=100_000, cost=40_000, stock=5, name="لندد تست")

    response = _add_purchase(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), extra_cost="50000",
    )
    assert response.status_code == 303

    purchase = _latest_purchase(db_session)
    assert purchase.total_cost == 500_000  # 450,000 items + 50,000 shipping
    assert purchase.extra_cost == 50_000
    assert purchase.extra_cost_in_landed is True

    db_session.refresh(variant)
    assert variant.stock_quantity == 5  # quantities belong to the product screens
    # Shipping spread by line value: 500k/10 units → 45k + 5k landed cost.
    assert variant.cost_price == 50_000
    movement = db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
        StockMovement.movement_type == "cost_adjustment",
    ).one()
    assert movement.quantity_delta == 0
    assert movement.unit_cost == 50_000


def test_purchase_can_keep_shipping_out_of_cost_basis(client, db_session, authed):
    supplier = _make_supplier(db_session, name="بدون لندد")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="بدون لندد")

    # An unchecked box posts only its "0" companion field.
    response = _add_purchase(
        client, variant, quantity=10, unit_cost=45_000, supplier_id=str(supplier.id),
        extra_cost="50000", **{"extra_cost_in_landed": ["0"]},
    )
    assert response.status_code == 303

    purchase = _latest_purchase(db_session)
    assert purchase.total_cost == 500_000
    assert purchase.extra_cost_in_landed is False
    db_session.refresh(variant)
    # Invoiced total includes shipping, but the cost basis does not.
    assert variant.cost_price == 45_000
    assert variant.stock_quantity == 0


def test_finalizing_can_record_the_first_payment(client, db_session, authed):
    supplier = _make_supplier(db_session)
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="پرداخت هنگام نهایی‌سازی")

    response = _add_purchase(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), payment_amount="200000",
    )
    assert response.status_code == 303

    purchase = _latest_purchase(db_session)
    payment = db_session.query(SupplierPayment).one()
    assert payment.purchase_id == purchase.id
    assert payment.amount == 200_000
    assert payment.method == "cash"

    assert purchase.amount_paid == 200_000
    settlement = purchase_settlement(db_session, purchase)
    assert (settlement["paid"], settlement["remaining"], settlement["status"]) == (200_000, 250_000, "partial")

    balances = {row["supplier"].id: row for row in get_supplier_balances(db_session)}
    assert balances[supplier.id]["invoiced"] == 450_000
    assert balances[supplier.id]["owed"] == 250_000

    start, end = _range()
    register = get_cashbox(db_session, start, end, 0)
    # Only the money that actually left the till counts: the invoice itself is
    # accrual and must not be counted a second time.
    assert register["supplier_payments"] == 200_000
    assert register["purchases"] == 450_000
    assert register["cash_out"] == 200_000
    assert register["closing"] == -200_000


def test_supplier_payment_always_leaves_the_till(client, db_session, authed):
    """Paying a wholesaler is money out however it is handed over: even a client
    that still sends a card method must not drop out of the cash register."""
    supplier = _make_supplier(db_session, name="نقد یا کارت")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="نقد یا کارت کالا")
    _add_purchase(
        client, variant, quantity=4, unit_cost=25_000, supplier_id=str(supplier.id),
        payment_amount="50000", payment_method="card",
    )
    purchase = _latest_purchase(db_session)
    payment = db_session.query(SupplierPayment).one()
    assert payment.purchase_id == purchase.id
    assert payment.method == "cash"

    start, end = _range()
    register = get_cashbox(db_session, start, end, 0)
    assert register["supplier_payments"] == 50_000
    assert register["cash_out"] == 50_000


def test_purchase_never_double_counts_stock_entered_on_the_product(client, db_session, authed):
    """The shop enters bought stock on the product and records the supplier
    invoice afterwards: the quantities must not be added a second time."""
    supplier = _make_supplier(db_session, name="موجودی از قبل ثبت‌شده")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=12, name="موجودی دوباره‌شماری")

    response = _add_purchase(client, variant, quantity=12, unit_cost=45_000, supplier_id=str(supplier.id))
    assert response.status_code == 303

    db_session.refresh(variant)
    assert variant.stock_quantity == 12  # not 24
    assert variant.cost_price == 45_000  # the invoice still updates the cost basis
    assert db_session.query(StockMovement).filter(
        StockMovement.variant_id == variant.id,
        StockMovement.movement_type.in_(("purchase", "purchase_reversal")),
    ).count() == 0


def test_purchase_requires_a_supplier(client, db_session, authed):
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="بدون تأمین‌کننده")

    response = _add_purchase(client, variant, quantity=2, unit_cost=10_000, finalize=False)
    assert response.status_code == 303
    assert "err=" in _redirect_text(response)
    assert db_session.query(Purchase).count() == 0
    assert db_session.query(SupplierPayment).count() == 0


def test_invoice_rejects_products_of_another_supplier(client, db_session, authed):
    """A picker that only shows one supplier's products is enforced server-side."""
    mine = _make_supplier(db_session, name="تأمین‌کننده من")
    other = _make_supplier(db_session, name="تأمین‌کننده دیگر")
    mine_product, mine_variant = _make_variant(db_session, price=100_000, cost=50_000, stock=1, name="کالای خودی")
    other_product, other_variant = _make_variant(db_session, price=100_000, cost=50_000, stock=1, name="کالای دیگری")
    mine_product.supplier_id = mine.id
    other_product.supplier_id = other.id
    db_session.commit()

    response = _create_draft(
        client, other_variant, quantity=1, unit_cost=10_000, supplier_id=str(mine.id),
    )
    assert response.status_code == 303
    assert "err=" in _redirect_text(response)
    assert db_session.query(Purchase).count() == 0

    # Its own supplier's product is accepted.
    accepted = _create_draft(
        client, mine_variant, quantity=1, unit_cost=10_000, supplier_id=str(mine.id),
    )
    assert accepted.status_code == 303
    assert db_session.query(Purchase).count() == 1


def test_payments_are_blocked_on_a_draft(client, db_session, authed):
    supplier = _make_supplier(db_session, name="پرداخت روی پیش‌نویس")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="پرداخت پیش‌نویس")

    _create_draft(client, variant, quantity=2, unit_cost=10_000, supplier_id=str(supplier.id))
    purchase = _latest_purchase(db_session)
    assert purchase.is_draft is True

    response = _post(client, f"/admin/purchases/{purchase.id}/payment", {"amount": "5000"})
    assert response.status_code == 303
    assert "err=" in _redirect_text(response)
    assert db_session.query(SupplierPayment).count() == 0

    start, end = _range()
    assert get_cashbox(db_session, start, end, 0)["cash_out"] == 0


def test_draft_can_be_edited_and_discarded(client, db_session, authed):
    supplier = _make_supplier(db_session, name="ویرایش پیش‌نویس")
    first_product, first_variant = _make_variant(db_session, price=100_000, cost=50_000, stock=1, name="اولی")
    second_product, second_variant = _make_variant(db_session, price=100_000, cost=20_000, stock=1, name="دومی")
    first_product.supplier_id = supplier.id
    second_product.supplier_id = supplier.id
    db_session.commit()

    _create_draft(client, first_variant, quantity=1, unit_cost=10_000, supplier_id=str(supplier.id))
    purchase = _latest_purchase(db_session)

    editor = client.get(f"/admin/purchases/{purchase.id}/edit")
    assert editor.status_code == 200
    assert f'action="/admin/purchases/{purchase.id}/update"' in editor.text
    assert "purchase-draft-lines" in editor.text

    updated = _post(client, f"/admin/purchases/{purchase.id}/update", {
        "supplier_id": str(supplier.id),
        "note": "بازنویسی پیش‌نویس",
        "purchase_variant_0": str(second_variant.id),
        "purchase_qty_0": "3",
        "purchase_cost_0": "20000",
        "extra_cost": "5000",
    })
    assert updated.status_code == 303

    db_session.refresh(purchase)
    assert purchase.total_cost == 65_000  # 3 × 20,000 + 5,000 shipping
    lines = db_session.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id).all()
    assert [line.variant_id for line in lines] == [second_variant.id]

    discarded = _post(client, f"/admin/purchases/{purchase.id}/delete", {})
    assert discarded.status_code == 303
    assert db_session.query(Purchase).filter(Purchase.id == purchase.id).count() == 0
    assert db_session.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase.id).count() == 0
    assert db_session.query(BusinessEvent).filter(
        BusinessEvent.event_type == "PurchaseReversed",
    ).count() == 0


def test_finalized_purchase_can_no_longer_be_edited(client, db_session, authed):
    supplier = _make_supplier(db_session, name="بعد از نهایی")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="بعد از نهایی کالا")
    _add_purchase(client, variant, quantity=1, unit_cost=10_000, supplier_id=str(supplier.id))
    purchase = _latest_purchase(db_session)

    editor = client.get(f"/admin/purchases/{purchase.id}/edit", follow_redirects=False)
    assert editor.status_code == 303
    assert "err=" in _redirect_text(editor)


def test_further_payment_settles_the_purchase(client, db_session, authed):
    supplier = _make_supplier(db_session, name="تسویه تدریجی")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="تسویه تدریجی کالا")
    _add_purchase(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), payment_amount="200000",
    )
    purchase = _latest_purchase(db_session)

    response = _post(client, f"/admin/purchases/{purchase.id}/payment", {"amount": "1000000"})
    assert response.status_code == 303
    assert "err=" in _redirect_text(response)

    response = _post(client, f"/admin/purchases/{purchase.id}/payment", {"amount": "250000"})
    assert response.status_code == 303

    db_session.refresh(purchase)
    settlement = purchase_settlement(db_session, purchase)
    assert (settlement["remaining"], settlement["status"]) == (0, "paid")
    assert purchase.amount_paid == 450_000
    assert db_session.query(SupplierPayment).filter(SupplierPayment.purchase_id == purchase.id).count() == 2


def test_backdated_purchase_reports_in_its_invoice_period(client, db_session, authed):
    supplier = _make_supplier(db_session, name="خرید قدیمی")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="خرید قدیمی")
    today = datetime.now(timezone.utc)
    invoice_day = today - timedelta(days=40)
    period_start = invoice_day.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = period_start + timedelta(days=2)

    response = _add_purchase(
        client, variant, quantity=4, unit_cost=25_000, supplier_id=str(supplier.id),
        purchase_date=invoice_day.strftime("%Y-%m-%d"),
    )
    assert response.status_code == 303

    purchase = _latest_purchase(db_session)
    assert purchase.purchase_date is not None
    # The recorded date stamps the cost history even though the invoice is older.
    assert db_session.query(StockMovement).filter(
        StockMovement.purchase_id == purchase.id,
        StockMovement.movement_type == "cost_adjustment",
    ).count() == 1
    db_session.refresh(variant)
    assert variant.stock_quantity == 0

    assert get_cashbox(db_session, period_start, period_end, 0)["purchases"] == 100_000
    current_start, current_end = _range(days_ago=1, days_ahead=1)
    assert get_cashbox(db_session, current_start, current_end, 0)["purchases"] == 0


def test_reversal_unlinks_payments_without_losing_them(client, db_session, authed):
    supplier = _make_supplier(db_session, name="برگشت خرید")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="برگشت‌پذیر")
    _add_purchase(
        client, variant, quantity=10, unit_cost=45_000,
        supplier_id=str(supplier.id), payment_amount="200000",
    )
    purchase = _latest_purchase(db_session)

    response = _post(client, f"/admin/purchases/{purchase.id}/delete", {})
    assert response.status_code == 303

    db_session.refresh(purchase)
    assert purchase.is_reversed is True
    assert purchase.amount_paid == 0
    payment = db_session.query(SupplierPayment).one()
    assert payment.purchase_id is None
    assert payment.amount == 200_000

    db_session.refresh(variant)
    assert variant.stock_quantity == 0
    assert variant.cost_price == 50_000  # the pre-purchase cost basis is back
    balances = {row["supplier"].id: row for row in get_supplier_balances(db_session)}
    assert balances[supplier.id]["invoiced"] == 0
    assert balances[supplier.id]["owed"] == 0


def test_purchase_pages_render_detail_receipt_and_list(client, db_session, authed):
    supplier = _make_supplier(db_session, name="صفحه خرید")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="کالای صفحه خرید")
    _add_purchase(
        client, variant, quantity=3, unit_cost=30_000, supplier_id=str(supplier.id),
        extra_cost="15000", note="فاکتور آزمایشی",
    )
    purchase = _latest_purchase(db_session)

    listing = client.get("/admin/purchases")
    assert listing.status_code == 200
    assert f"#{purchase.id}" in listing.text
    assert 'id="purchase-suggest"' in listing.text
    assert 'id="purchase-catalog"' in listing.text
    assert "purchase-picker" in listing.text

    detail = client.get(f"/admin/purchases/{purchase.id}")
    assert detail.status_code == 200
    assert "کالای صفحه خرید" in detail.text
    assert "فاکتور آزمایشی" in detail.text
    assert 'class="purchase-amount-list"' in detail.text
    assert f'action="/admin/purchases/{purchase.id}/payment"' in detail.text

    receipt = client.get(f"/admin/purchases/{purchase.id}/print")
    assert receipt.status_code == 200
    assert 'class="print-document"' in receipt.text
    assert "صفحه خرید" in receipt.text


def test_draft_detail_offers_finalizing_instead_of_paying(client, db_session, authed):
    supplier = _make_supplier(db_session, name="صفحه پیش‌نویس")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="کالای پیش‌نویس")
    _create_draft(client, variant, quantity=2, unit_cost=10_000, supplier_id=str(supplier.id))
    purchase = _latest_purchase(db_session)

    detail = client.get(f"/admin/purchases/{purchase.id}")
    assert detail.status_code == 200
    assert f'action="/admin/purchases/{purchase.id}/finalize"' in detail.text
    assert f'action="/admin/purchases/{purchase.id}/payment"' not in detail.text
    assert f"/admin/purchases/{purchase.id}/edit" in detail.text
    assert "پیش‌نویس" in detail.text

    receipt = client.get(f"/admin/purchases/{purchase.id}/print")
    assert receipt.status_code == 200
    assert "پیش‌فاکتور" in receipt.text


def test_purchase_form_scopes_products_to_the_selected_supplier(client, db_session, authed):
    mine = _make_supplier(db_session, name="تأمین‌کننده فهرست")
    mine_product, _ = _make_variant(db_session, price=100_000, cost=50_000, stock=3, name="کالای فهرست")
    mine_product.supplier_id = mine.id
    unassigned_product, _ = _make_variant(db_session, price=100_000, cost=50_000, stock=3, name="کالای بی‌تأمین‌کننده")
    db_session.commit()

    catalog = {row["name"]: row for row in _catalog(client)}
    assert catalog["کالای فهرست"]["supplier_id"] == mine.id
    # Unassigned products stay in the payload but scoped by their (empty) supplier.
    assert catalog["کالای بی‌تأمین‌کننده"]["supplier_id"] == 0

    page = client.get("/admin/purchases")
    # The picker reads the supplier select and filters the catalogue by it.
    assert "purchase-scope-note" in page.text
    assert "scopedVariants" in page.text
    assert "currentSupplierId" in page.text


def test_product_form_carries_one_supplier_field(client, db_session, authed):
    page = client.get("/admin/products/add")
    assert page.status_code == 200
    assert page.text.count('name="supplier_id"') == 1
    assert '<label for="supplier_id">تأمین‌کننده</label>' in page.text


def test_purchase_filters_and_pagination(client, db_session, authed):
    supplier = _make_supplier(db_session, name="فیلتر تأمین‌کننده")
    open_purchase = Purchase(supplier_id=supplier.id, total_cost=100_000, note="بدهی باز")
    db_session.add(open_purchase)
    db_session.commit()
    db_session.refresh(open_purchase)
    db_session.add(SupplierPayment(supplier_id=supplier.id, purchase_id=open_purchase.id,
                                   amount=100_000, operator_user_id=1, method="cash"))
    draft = Purchase(supplier_id=supplier.id, total_cost=55_000, note="پیش‌نویس فیلتر",
                     is_draft=True)
    db_session.add(draft)
    db_session.commit()

    only_unpaid = client.get("/admin/purchases?status=unpaid")
    assert only_unpaid.status_code == 200
    # A fully settled purchase is excluded, and a draft is not an unpaid invoice.
    assert 'purchase-table' not in only_unpaid.text
    assert "products-empty" in only_unpaid.text

    only_paid = client.get("/admin/purchases?status=paid")
    assert f"#{open_purchase.id}" in only_paid.text

    only_drafts = client.get("/admin/purchases?status=draft")
    assert f"#{draft.id}" in only_drafts.text
    # Drafts never show up in the period KPIs.
    assert "پیش‌نویس" in only_drafts.text
    all_purchases = client.get("/admin/purchases")
    assert f"#{draft.id}" in all_purchases.text
    assert "در انتظار نهایی‌سازی" in all_purchases.text

    by_supplier = client.get(f"/admin/purchases?supplier_id={supplier.id}")
    assert f"#{open_purchase.id}" in by_supplier.text

    # 20 rows per page: the 21st purchase pushes onto a second page.
    for index in range(21):
        db_session.add(Purchase(total_cost=1_000, note=f"صفحه‌بندی {index}"))
    db_session.commit()

    first_page = client.get("/admin/purchases")
    assert first_page.status_code == 200
    assert "page=2" in first_page.text
    second_page = client.get("/admin/purchases?page=2")
    assert second_page.status_code == 200
    assert "page=1" in second_page.text
    assert 'purchase-table' in second_page.text


def test_purchase_overview_reports_period_spend_and_arrears(client, db_session, authed):
    supplier = _make_supplier(db_session, name="خلاصه خرید")
    _, variant = _make_variant(db_session, price=100_000, cost=50_000, stock=0, name="خلاصه خرید کالا")
    overdue_day = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    _add_purchase(
        client, variant, quantity=5, unit_cost=20_000, supplier_id=str(supplier.id),
        extra_cost="5000", due_date=overdue_day,
    )
    purchase = _latest_purchase(db_session)
    assert purchase.due_date is not None

    start, end = _range(days_ago=1, days_ahead=1)
    overview = purchase_overview(db_session, start, end)
    assert overview["period_spend"] == 105_000
    assert overview["period_units"] == 5
    assert overview["supplier_owed"] == 105_000
    assert overview["overdue_count"] == 1
    assert overview["overdue_amount"] == 105_000

    detail = client.get(f"/admin/purchases/{purchase.id}")
    assert "badge badge-danger" in detail.text
