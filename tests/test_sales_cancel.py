"""A cashier can leave a sale without the till resurrecting it later.

The checkout draft is server-owned, so «فروش جدید» and «لغو فروش» must move
the session pointer as well as the basket. An approved card payment is the
one thing cancellation may not throw away.
"""
import json
import re

from models import CheckoutSession, POSTransaction
from services.checkout import (
    cancel_checkout, finalize_basket, get_checkout, reserve_basket,
)
from services.pos_terminal import POSConnectionError
from tests.conftest import csrf_token
from tests.test_pos_terminal import (
    _configure_pos, _create_server_checkout, _make_pos_variant,
)
from tests.test_sales_money import _make_variant


def _login(client):
    token = csrf_token(client)
    response = client.post(
        "/admin/login",
        data={"username": "owner", "password": "test-admin-pass",
              "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _start_scan(client):
    return client.post("/sales/skip-customer",
                       data={"csrf_token": csrf_token(client, "/sales/new")})


def _add(client, token, variant, basket_json="[]"):
    return client.post("/sales/add-to-basket", data={
        "customer_id": "0",
        "barcode": variant.barcode,
        "basket_json": basket_json,
        "campaign_code": "",
        "csrf_token": token,
    })


def _nonce(page):
    match = re.search(r'name="checkout_nonce" value="([^"]+)"', page)
    assert match, "checkout page carries no nonce"
    return match.group(1)


def test_new_sale_starts_empty_instead_of_resurrecting_the_last_one(client, db_session):
    _login(client)
    token = csrf_token(client, "/sales/new")
    _, variant = _make_variant(db_session, price=50_000, stock=3,
                               name="کالای فروش قبلی")
    scan = _start_scan(client)
    assert scan.status_code == 200
    added = _add(client, token, variant)
    assert "کالای فروش قبلی" in added.text
    # The reported case: the terminal attempt failed and the draft is waiting
    # for a manual answer when the cashier walks away.
    checkout = db_session.query(CheckoutSession).order_by(
        CheckoutSession.id.desc()).first()
    checkout.state = "payment_uncertain"
    db_session.commit()

    # The explicit reset must drop the old session pointer, not just paint
    # an empty page while keeping the draft behind it.
    client.get("/sales/new")
    fresh = client.post("/sales/skip-customer", data={"csrf_token": token})
    assert "سبد خرید خالی است" in fresh.text


def test_cancel_sale_clears_the_till_and_starts_fresh(client, db_session):
    _login(client)
    token = csrf_token(client, "/sales/new")
    _, variant = _make_variant(db_session, price=70_000, stock=3,
                               name="کالای لغوشده")
    _start_scan(client)
    added = _add(client, token, variant)
    assert 'action="/sales/cancel"' in added.text
    nonce = _nonce(added.text)

    response = client.post("/sales/cancel", data={
        "checkout_nonce": nonce, "csrf_token": token,
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "/sales/new" in response.headers["location"]

    db_session.expire_all()
    assert get_checkout(db_session, nonce).state == "expired"
    # No GET /sales/new between cancel and the next scan: only the cancel
    # route can have cleared the stale pointer.
    fresh = client.post("/sales/skip-customer", data={"csrf_token": token})
    assert "سبد خرید خالی است" in fresh.text


def test_cancel_refuses_an_approved_terminal_payment(client, db_session):
    _login(client)
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    checkout = _create_server_checkout(db_session, "cancel-approved", variant.id)
    finalize_basket(db_session, checkout)
    checkout.state = "payment_approved"
    db_session.commit()
    token = csrf_token(client, "/sales/new")

    response = client.post("/sales/cancel", data={
        "checkout_nonce": checkout.checkout_nonce, "csrf_token": token,
    })
    assert response.status_code == 200
    assert "تأیید شده" in response.text
    db_session.expire_all()
    assert get_checkout(db_session, checkout.checkout_nonce).state == "payment_approved"


def test_new_sale_does_not_abandon_an_approved_payment(client, db_session):
    _login(client)
    token = csrf_token(client, "/sales/new")
    _, variant = _make_variant(db_session, price=60_000, stock=2,
                               name="کالای پرداخت‌شده")
    _start_scan(client)
    assert "کالای پرداخت‌شده" in _add(client, token, variant).text
    checkout = db_session.query(CheckoutSession).order_by(
        CheckoutSession.id.desc()).first()
    checkout.state = "payment_approved"
    db_session.commit()

    client.get("/sales/new")
    resumed = client.post("/sales/skip-customer", data={"csrf_token": token})
    assert "کالای پرداخت‌شده" in resumed.text


def test_cancel_releases_stock_holds(client, db_session):
    _login(client)
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    checkout = _create_server_checkout(db_session, "cancel-holds", variant.id)
    data = finalize_basket(db_session, checkout)
    reserve_basket(db_session, checkout, data["basket"])
    db_session.refresh(variant)
    assert variant.reserved_quantity == 1

    cancel_checkout(db_session, checkout)
    db_session.refresh(variant)
    db_session.refresh(checkout)
    assert checkout.state == "expired"
    assert variant.reserved_quantity == 0


def test_unreachable_terminal_is_a_refusal_not_manual_approval(client, db_session, monkeypatch):
    _configure_pos(db_session)
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    _login(client)
    nonce = "pos-unreachable"
    _create_server_checkout(db_session, nonce, variant.id)
    token = csrf_token(client, "/sales/new")

    def fail(*args, **kwargs):
        raise POSConnectionError("connect timed out")

    monkeypatch.setattr("routers.sales.send_terminal_sale", fail)
    response = client.post("/sales/send-to-terminal", data={
        "checkout_nonce": nonce, "csrf_token": token,
    }, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "alert-error" in response.text
    assert "برقرار نشد" in response.text
    assert 'name="manual_pos_approval"' not in response.text

    db_session.expire_all()
    transaction = db_session.query(POSTransaction).filter(
        POSTransaction.checkout_nonce == nonce).one()
    assert transaction.status == "declined"
    assert get_checkout(db_session, nonce).state == "payment_declined"
