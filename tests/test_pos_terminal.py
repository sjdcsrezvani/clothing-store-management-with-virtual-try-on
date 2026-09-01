"""Tests for the tested Parsian TCP protocol and durable checkout lifecycle."""
import json
import re
import socket
import threading

from models import POSTransaction, Product, ProductVariant, Sale, Settings
from services import pos_terminal
from tests.conftest import csrf_token


def _one_shot_server(response: bytes):
    ready = threading.Event()
    received = {}
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def run():
        ready.set()
        connection, _ = server.accept()
        with connection:
            received["payload"] = connection.recv(4096)
            connection.sendall(response)
        server.close()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(timeout=1)
    return port, received


def test_build_parsian_payload_matches_server_js():
    assert pos_terminal.build_parsian_payload("123456") == (
        "0041RQ036PR006000000AM006123456CU003364PD0011"
    )


def test_classify_approved_response():
    result = pos_terminal.classify_response("RS003RS00200\x00\x00")
    assert result == {
        "status": "approved",
        "response_code": "00200",
        "label": "Approved",
    }


def test_classify_cancelled_response():
    result = pos_terminal.classify_response("RS003RS00250")
    assert result["status"] == "cancelled"
    assert result["response_code"] == "00250"
    assert result["label"] == "Cancelled on POS"


def test_send_sale_sends_ascii_payload_and_returns_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(pos_terminal, "LOG_FILE", tmp_path / "transactions.log")
    port, received = _one_shot_server(b"RS003RS00200\x00")

    result = pos_terminal.send_sale("127.0.0.1", port, 123456, timeout=1)

    assert received["payload"].decode("ascii") == pos_terminal.build_parsian_payload("123456")
    assert result["ok"] is True
    assert result["status"] == "approved"
    assert result["response_code"] == "00200"


def test_send_sale_returns_cancelled_status(monkeypatch, tmp_path):
    monkeypatch.setattr(pos_terminal, "LOG_FILE", tmp_path / "transactions.log")
    port, _ = _one_shot_server(b"RS003RS00250")

    result = pos_terminal.send_sale("127.0.0.1", port, 123456, timeout=1)

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert result["response_code"] == "00250"


def _configure_pos(db_session):
    db_session.add(Settings(key="pos_terminal_host", value="127.0.0.1"))
    db_session.add(Settings(key="pos_terminal_port", value="8500"))
    db_session.commit()


def _make_pos_variant(db_session, price=100_000, stock=2):
    product = Product(name="کالای کارت‌خوان")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(
        product_id=product.id,
        price=price,
        cost_price=40_000,
        stock_quantity=stock,
        barcode="POS-001",
    )
    db_session.add(variant)
    db_session.commit()
    db_session.refresh(variant)
    return variant


def _send_data(nonce, amount=100_000, variant_id=None):
    return {
        "amount": str(amount),
        "checkout_nonce": nonce,
        "customer_id": "0",
        "basket_json": json.dumps([{"variant_id": variant_id, "quantity": 1}]) if variant_id else "[]",
    }


def test_send_to_terminal_persists_approval_and_reuses_nonce(client, db_session, monkeypatch):
    _configure_pos(db_session)
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    calls = []

    def fake_send(host, port, amount):
        calls.append((host, port, amount))
        return {"status": "approved", "response_code": "00200", "label": "Approved", "response": "RS003RS00200"}

    monkeypatch.setattr("routers.sales.send_terminal_sale", fake_send)
    login_token = csrf_token(client)
    login = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": login_token}, follow_redirects=False)
    assert login.status_code == 303
    token = csrf_token(client, "/sales/new")
    data = {**_send_data("checkout-idempotency-1", variant_id=variant.id), "csrf_token": token}

    first = client.post("/sales/send-to-terminal", data=data)
    second = client.post("/sales/send-to-terminal", data=data)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "approved"
    assert second.json()["status"] == "approved"
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    assert len(calls) == 1

    transaction = db_session.query(POSTransaction).one()
    assert transaction.status == "approved"
    assert transaction.sale_id is None
    assert transaction.approval_token_hash


def test_terminal_error_is_persisted_as_uncertain_and_not_retried(client, db_session, monkeypatch):
    _configure_pos(db_session)
    calls = []

    def fake_send(host, port, amount):
        calls.append(amount)
        raise TimeoutError("terminal did not answer")

    monkeypatch.setattr("routers.sales.send_terminal_sale", fake_send)
    login_token = csrf_token(client)
    login = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": login_token}, follow_redirects=False)
    assert login.status_code == 303
    token = csrf_token(client, "/sales/new")
    variant = _make_pos_variant(db_session, price=100_000, stock=2)
    data = {**_send_data("checkout-idempotency-uncertain", variant_id=variant.id), "csrf_token": token}

    first = client.post("/sales/send-to-terminal", data=data)
    second = client.post("/sales/send-to-terminal", data=data)

    assert first.status_code == 502
    assert first.json()["status"] == "uncertain"
    assert second.status_code == 409
    assert second.json()["status"] == "uncertain"
    assert calls == [100_000]
    transaction = db_session.query(POSTransaction).one()
    assert transaction.status == "uncertain"
    assert transaction.error_message == "terminal did not answer"


def test_approved_pos_transaction_links_to_one_sale(client, db_session, monkeypatch):
    _configure_pos(db_session)
    variant = _make_pos_variant(db_session)
    monkeypatch.setattr(
        "routers.sales.send_terminal_sale",
        lambda host, port, amount: {
            "status": "approved", "response_code": "00200",
            "label": "Approved", "response": "RS003RS00200",
        },
    )
    login_token = csrf_token(client)
    login = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": login_token}, follow_redirects=False)
    assert login.status_code == 303
    checkout_page = csrf_token(client, "/sales/new")
    nonce = "checkout-idempotency-sale"
    send_response = client.post(
        "/sales/send-to-terminal",
        data={**_send_data(nonce, variant_id=variant.id), "csrf_token": checkout_page},
    )
    assert send_response.status_code == 200
    approval_token = send_response.json()["approval_token"]

    basket = [{"variant_id": variant.id, "quantity": 1}]
    confirm_response = client.post(
        "/sales/confirm-sale",
        data={
            "customer_id": "0",
            "basket_json": json.dumps(basket),
            "payment_method": "card",
            "pos_approval_token": approval_token,
            "checkout_nonce": nonce,
            "use_referrer_discount": "0",
            "custom_discount_amount": "",
            "custom_discount_percent": "",
            "csrf_token": checkout_page,
        },
    )

    assert confirm_response.status_code == 200
    sale = db_session.query(Sale).one()
    transaction = db_session.query(POSTransaction).one()
    assert transaction.status == "linked_to_sale"
    assert transaction.sale_id == sale.id
    assert sale.pos_transaction is transaction

    replay = client.post(
        "/sales/confirm-sale",
        data={
            "customer_id": "0",
            "basket_json": json.dumps(basket),
            "payment_method": "card",
            "pos_approval_token": approval_token,
            "checkout_nonce": nonce,
            "use_referrer_discount": "0",
            "custom_discount_amount": "",
            "custom_discount_percent": "",
            "csrf_token": checkout_page,
        },
    )
    assert replay.status_code == 409
    assert db_session.query(Sale).count() == 1


def test_admin_can_review_orphaned_approved_pos_transaction(client, db_session, authed):
    transaction = POSTransaction(
        checkout_nonce="orphaned-checkout",
        amount=250_000,
        host="127.0.0.1",
        port=8500,
        status="approved",
        response_code="00200",
        response_label="Approved",
    )
    db_session.add(transaction)
    db_session.commit()

    response = client.get("/admin/pos-reconciliation")
    assert response.status_code == 200
    assert "orphaned-checkout" not in response.text
    assert "250,000" in response.text

    token = csrf_token(client, "/admin/pos-reconciliation")
    reviewed = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"note": "بررسی شد", "csrf_token": token},
        follow_redirects=False,
    )
    assert reviewed.status_code == 303
    db_session.refresh(transaction)
    assert transaction.reconciled is True
    assert transaction.reconciliation_note == "بررسی شد"
