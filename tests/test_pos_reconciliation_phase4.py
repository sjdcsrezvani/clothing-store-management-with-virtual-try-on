from models import POSTransaction


def _transaction(db):
    transaction = POSTransaction(
        checkout_nonce="phase4-transaction",
        amount=1000,
        host="127.0.0.1",
        port=8500,
        status="uncertain",
    )
    db.add(transaction)
    db.commit()
    return transaction


def test_reconciliation_requires_resolution_and_evidence(client, db_session, authed):
    transaction = _transaction(db_session)
    token = __import__("tests.conftest", fromlist=["csrf_token"]).csrf_token(client, "/admin/pos-reconciliation")
    response = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "terminal_error", "evidence": "", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(transaction)
    assert transaction.reconciled is False


def test_uncertain_payment_cannot_be_marked_paid_by_review(client, db_session, authed):
    transaction = _transaction(db_session)
    token = __import__("tests.conftest", fromlist=["csrf_token"]).csrf_token(client, "/admin/pos-reconciliation")
    response = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "confirmed_paid", "evidence": "Provider receipt 123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409
    db_session.refresh(transaction)
    assert transaction.sale_id is None
    assert transaction.reconciled is False


def test_reconciliation_records_operator_and_reference_data(client, db_session, authed):
    transaction = _transaction(db_session)
    token = __import__("tests.conftest", fromlist=["csrf_token"]).csrf_token(client, "/admin/pos-reconciliation")
    response = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={
            "resolution_type": "confirmed_cancelled",
            "evidence": "Terminal daily report 42",
            "provider_reference": "provider-42",
            "terminal_transaction_number": "terminal-42",
            "retrieval_reference_number": "rrn-42",
            "masked_card": "****1234",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.refresh(transaction)
    assert transaction.reconciled is True
    assert transaction.resolution_type == "confirmed_cancelled"
    assert transaction.operator_user_id is not None
    assert transaction.provider_reference == "provider-42"
    assert transaction.masked_card == "****1234"
