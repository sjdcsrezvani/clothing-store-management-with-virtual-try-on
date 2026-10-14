"""Phase 8 silent refresh: basket mutations swap the layout by fetch —
no full reload, same server HTML. Terminal + confirm keep real submits."""
import json

from tests.conftest import csrf_token
from tests.test_sales_money import _make_variant


def _scan_page(client, db_session):
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)
    client.post("/sales/skip-customer",
                data={"csrf_token": csrf_token(client, "/sales/new")})
    _, variant = _make_variant(db_session, price=100_000, stock=5,
                               name="کالای بی‌صدا")
    return client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }).text


def test_silent_submit_wiring_present(client, db_session):
    page = _scan_page(client, db_session)
    for action in ("/sales/add-to-basket", "/sales/set-quantity",
                   "/sales/remove-from-basket", "/sales/apply-discount"):
        assert action in page  # intercepted actions
    assert "announceScan" in page
    assert "SILENT_TILL_ACTIONS" in page
    assert "event.submitter" in page  # stepper delta rides the fetch too
    # Delegated bindings survive the swap; no per-node wiring.
    assert "t.id === 'terminal-form'" in page
    assert "t.name === 'payment_method'" in page
    assert "form.submit()" in page  # fallback always lands somewhere


def test_xhr_gets_the_same_full_page(client, db_session):
    _scan_page(client, db_session)
    resp = client.post("/sales/apply-discount", data={
        "customer_id": "0", "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert "checkout-layout" in resp.text  # the swap source, unchanged server
