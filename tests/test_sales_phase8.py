"""Phase 8 silent refresh — htmx now: basket mutations swap the layout in
place with no full reload, same server HTML. Terminal + confirm keep their own
flows; the fetch-era shims are gone (tests/test_htmx_migration.py guards it)."""
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
        assert f'hx-post="{action}"' in page  # each mutation rides htmx
    assert 'hx-select=".checkout-layout"' in page  # the swap source, in place
    assert 'hx-target=".checkout-layout"' in page
    assert "announceScan" in page
    # htmx includes the clicked stepper's delta natively — the shim is gone.
    assert "event.submitter" not in page
    # The terminal strip and the name search are htmx too.
    assert 'hx-post="/sales/send-to-terminal"' in page
    assert 'hx-get="/sales/terminal-status"' in page
    assert 'hx-get="/sales/api/product-search"' in page
    # Delegated bindings survive the swap; a dead wire still lands somewhere.
    assert "t.name === 'payment_method'" in page
    assert "htmx:sendError" in page
    assert "form.submit()" in page


def test_xhr_gets_the_same_full_page(client, db_session):
    _scan_page(client, db_session)
    resp = client.post("/sales/apply-discount", data={
        "customer_id": "0", "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert "checkout-layout" in resp.text  # the swap source, unchanged server
