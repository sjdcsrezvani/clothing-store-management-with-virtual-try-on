"""The checkout pilot's htmx contract: fragments where the till asks, JSON for
every other caller, and a terminal strip that carries its own token swap."""
from tests.conftest import csrf_token
from tests.test_pos_terminal import _configure_pos, _create_server_checkout, _make_pos_variant
from tests.test_sales_money import _make_variant


def _login(client):
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)


def test_terminal_status_answers_the_till_with_a_fragment(client):
    response = client.get("/sales/terminal-status", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert 'id="terminal-indicator"' in response.text
    assert 'hx-get="/sales/terminal-status"' in response.text


def test_product_search_answers_the_till_with_option_rows(client, db_session):
    _login(client)
    _, variant = _make_variant(db_session, price=100_000, stock=5, name="پیراهن جستجو")
    response = client.get("/sales/api/product-search?q=جستجو", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert 'role="option"' in response.text
    assert 'hx-post="/sales/add-to-basket"' in response.text
    assert variant.barcode in response.text
    # Non-htmx callers keep the JSON contract, silence under two chars included.
    assert client.get("/sales/api/product-search?q=جستجو").json()["items"]
    assert client.get("/sales/api/product-search?q=پ").json()["items"] == []


def test_send_to_terminal_swaps_a_fragment_with_token_and_indicator(client, db_session, monkeypatch):
    _configure_pos(db_session)
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    monkeypatch.setattr(
        "routers.sales.send_terminal_sale",
        lambda host, port, amount, r4=None, r8=None: {
            "status": "approved", "response_code": "00200",
            "label": "Approved", "response": "RS003RS00200"})
    _login(client)
    nonce = "checkout-htmx-fragment-1"
    _create_server_checkout(db_session, nonce, variant.id)
    response = client.post(
        "/sales/send-to-terminal",
        data={"checkout_nonce": nonce, "csrf_token": csrf_token(client, "/sales/new")},
        headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "alert-success" in response.text
    assert 'id="pos-approval-token"' in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert 'id="terminal-indicator"' in response.text


def test_send_to_terminal_refusal_is_a_fragment_for_the_till(client, db_session):
    _login(client)
    data = {"checkout_nonce": "no-such-checkout",
            "csrf_token": csrf_token(client, "/sales/new")}
    framed = client.post("/sales/send-to-terminal", data=data, headers={"HX-Request": "true"})
    # The strip shows the refusal in place — htmx only swaps 2xx responses.
    assert framed.status_code == 200
    assert "alert-error" in framed.text
    assert "سبد خرید یافت نشد" in framed.text
    # Without the htmx header the JSON path (and its error page) stands.
    plain = client.post("/sales/send-to-terminal", data=data)
    assert plain.status_code == 404
