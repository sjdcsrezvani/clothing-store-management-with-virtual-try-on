"""Phase 1 till hardening: idempotent confirms, visible clamp diffs, split
credit legs, and abort-to-basket after a failed terminal payment."""
import json

from models import CheckoutSession, Customer, Sale, SalePaymentPart
from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_customer, _make_variant


def _login_owner(client):
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)


def test_replayed_nonce_returns_first_invoice_not_second_sale(client, db_session):
    from models import StaffUser
    from services.checkout import create_checkout
    _login_owner(client)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    nonce = "phase1-replay-nonce"
    owner = db_session.query(StaffUser).filter(StaffUser.username == "owner").first()
    create_checkout(db_session, staff_user_id=owner.id,
                    basket_json=json.dumps(basket, ensure_ascii=False),
                    checkout_nonce=nonce)
    first = _confirm_sale(client, basket, extra={"checkout_nonce": nonce, "payment_method": "cash"})
    assert first.status_code == 200
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.sale_nonce == nonce

    # The same checkout row, back to a payable state with the same basket: a
    # A retried POST races here in production and must replay, not duplicate.
    db_session.query(CheckoutSession).filter(
        CheckoutSession.checkout_nonce == nonce).update({"state": "reserved"})
    db_session.commit()
    replay = _confirm_sale(client, basket, extra={"checkout_nonce": nonce, "payment_method": "cash"})
    assert replay.status_code == 200
    assert "فاکتور" in replay.text
    assert db_session.query(Sale).count() == 1
    db_session.refresh(variant)
    assert variant.stock_quantity == 4  # sold once, not twice


def test_clamp_diff_blocks_sale_until_reviewed(client, db_session):
    _, variant = _make_variant(db_session, price=50_000, stock=5, name="شلوار تست")
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 50_000, "quantity": 99, "total_price": 99 * 50_000}]
    resp = _confirm_sale(client, basket)
    assert resp.status_code == 200
    assert "موجودی تغییر کرد" in resp.text  # the diff, named
    assert "شلوار تست" in resp.text
    assert db_session.query(Sale).count() == 0  # nothing sold yet

    # The draft now holds the corrected quantities: confirming the corrected
    # basket sells exactly what is there.
    corrected = [{"variant_id": variant.id, "product_id": variant.product_id,
                  "unit_price": 50_000, "quantity": 5, "total_price": 5 * 50_000}]
    resp2 = _confirm_sale(client, corrected)
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.total_amount == 5 * 50_000
    db_session.refresh(variant)
    assert variant.stock_quantity == 0


def test_split_cash_plus_credit_books_only_the_leg_as_debt(client, db_session):
    customer = _make_customer(db_session)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket, customer_id=customer.id, extra={
        "payment_method": "split", "split_cash": "40000",
        "split_card": "", "split_credit": "60000"})
    assert resp.status_code == 200
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.payment_method == "split"
    parts = {p.method: p.amount for p in db_session.query(
        SalePaymentPart).filter(SalePaymentPart.sale_id == sale.id).all()}
    assert parts == {"cash": 40000, "credit": 60000}
    db_session.refresh(customer)
    assert customer.total_debt == 60000  # the leg, not the invoice
    assert sale.credit_due_date is not None


def test_split_credit_leg_needs_a_customer_and_fits_the_limit(client, db_session):
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    resp = _confirm_sale(client, basket, extra={
        "payment_method": "split", "split_cash": "40000",
        "split_card": "", "split_credit": "60000"})
    assert "ثبت‌شده" in resp.text  # anonymous + credit leg refused
    assert db_session.query(Sale).count() == 0

    capped = _make_customer(db_session, phone="09120000002")
    capped.credit_limit = 50_000
    db_session.commit()
    resp = _confirm_sale(client, basket, customer_id=capped.id, extra={
        "payment_method": "split", "split_cash": "40000",
        "split_card": "", "split_credit": "60000"})
    assert "سقف اعتبار" in resp.text  # 60,000 leg over a 50,000 cap
    assert db_session.query(Sale).count() == 0


def test_declined_terminal_reopens_the_basket_for_cash(client, db_session):
    from models import StaffUser
    from services.checkout import (
        create_checkout, finalize_basket, reserve_basket,
        set_payment_outcome, set_payment_pending,
    )
    _login_owner(client)
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    owner = db_session.query(StaffUser).filter(StaffUser.username == "owner").first()
    checkout = create_checkout(
        db_session, staff_user_id=owner.id,
        basket_json=json.dumps(basket, ensure_ascii=False))
    finalize_basket(db_session, checkout)
    reserve_basket(db_session, checkout, json.loads(checkout.basket_json))
    set_payment_pending(db_session, checkout)
    set_payment_outcome(db_session, checkout, "declined", "test decline")
    nonce = checkout.checkout_nonce

    login_token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": login_token}, follow_redirects=False)
    token = csrf_token(client, "/sales/new")
    abort = client.post("/sales/confirm-sale", data={
        "customer_id": "0", "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "cash", "checkout_nonce": nonce, "csrf_token": token})
    assert abort.status_code == 200
    assert "سبد برگشت" in abort.text  # the basket, back for another method
    assert db_session.query(Sale).count() == 0

    token = csrf_token(client, "/sales/new")
    retry = client.post("/sales/confirm-sale", data={
        "customer_id": "0", "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "cash", "checkout_nonce": nonce, "csrf_token": token})
    assert retry.status_code == 200
    assert "فاکتور" in retry.text
    assert db_session.query(Sale).count() == 1
