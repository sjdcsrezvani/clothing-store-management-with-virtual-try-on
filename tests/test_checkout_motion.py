"""POS micro-interactions (Phase D): pop the final total, shake the bad field.

The till is high-frequency, so motion is rationed: the final total — the one
figure the cashier reads — re-enters digit by digit, and a server-refused
barcode or phone shakes its own field. Per-keystroke figures (cash change)
stay still by design.
"""
import re
from pathlib import Path

from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]


def _login(client):
    token = csrf_token(client)
    response = client.post(
        "/admin/login",
        data={"username": "owner", "password": "test-admin-pass", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_digit_tokens_ship_with_a_guard_and_no_colours():
    motion = (ROOT / "static" / "css" / "motion.css").read_text()
    assert ".t-digit-group" in motion
    assert "t-digit-pop-in" in motion
    assert ".t-digit-group .t-digit { animation: none !important; }" in motion
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", motion)


def test_final_total_pops_but_nothing_else_does():
    """Only the final total carries the hook — line items and the cash
    change must never animate per keystroke or per scan."""
    source = (ROOT / "templates" / "sales" / "checkout.html").read_text()
    hooked = re.findall(r"<[^>]*data-digit-pop", source)
    assert len(hooked) == 1
    assert 'data-amount="{{ final }}"' in source
    assert "cash-change" in source
    change_line = next(line for line in source.splitlines() if 'id="cash-change"' in line)
    assert "data-digit-pop" not in change_line


def test_barcode_field_shakes_on_a_bad_scan(client):
    _login(client)
    token = csrf_token(client, "/sales/new")
    resp = client.post("/sales/add-to-basket", data={
        "customer_id": "", "barcode": "NO-SUCH-BARCODE", "basket_json": "[]",
        "campaign_code": "", "csrf_token": token,
    })
    assert resp.status_code == 200
    assert "یافت نشد" in resp.text  # the refusal still says its sentence
    assert 'id="barcode-input"' in resp.text
    assert "data-shake-on-load" in resp.text
    assert 'aria-invalid="true"' in resp.text


def test_unknown_number_is_an_invitation_not_a_refusal(client):
    """An unknown-but-valid number opens registration — nothing shook,
    because nothing was wrong."""
    _login(client)
    token = csrf_token(client, "/sales/new")
    resp = client.post("/sales/lookup-customer", data={
        "phone": "09999999999", "csrf_token": token,
    })
    assert resp.status_code == 200
    assert "data-shake-on-load" not in resp.text


def test_phone_field_shakes_on_an_invalid_phone(client):
    _login(client)
    token = csrf_token(client, "/sales/new")
    resp = client.post("/sales/lookup-customer", data={
        "phone": "123", "csrf_token": token,
    })
    assert resp.status_code == 200
    assert "نامعتبر" in resp.text
    assert "data-shake-on-load" in resp.text


def test_error_border_uses_the_attention_token():
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".t-input.is-error" in style
    assert "var(--persimmon)" in style
