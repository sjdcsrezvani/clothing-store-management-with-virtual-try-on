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
    from urllib.parse import unquote_plus
    transaction = _transaction(db_session)
    token = __import__("tests.conftest", fromlist=["csrf_token"]).csrf_token(client, "/admin/pos-reconciliation")
    response = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "confirmed_paid", "evidence": "Provider receipt 123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "فاکتور" in unquote_plus(response.headers["location"])
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


def _token(client):
    return __import__("tests.conftest", fromlist=["csrf_token"]).csrf_token(client, "/admin/pos-reconciliation")


def test_refusals_speak_persian_and_redirect(client, db_session, authed):
    """No raw JSON leaves this page: every refusal is a sentence to read."""
    from urllib.parse import unquote_plus
    missing = client.post("/admin/pos-reconciliation/999999/review",
                          data={"resolution_type": "duplicate", "evidence": "x",
                                "csrf_token": _token(client)}, follow_redirects=False)
    assert missing.status_code == 303
    assert "یافت نشد" in unquote_plus(missing.headers["location"])

    transaction = _transaction(db_session)
    full_card = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "duplicate", "evidence": "x",
              "masked_card": "4111111111111111", "csrf_token": _token(client)},
        follow_redirects=False)
    assert full_card.status_code == 303
    assert "ماسک" in unquote_plus(full_card.headers["location"])
    db_session.refresh(transaction)
    assert transaction.reconciled is False

    twice = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "duplicate", "evidence": "bank slip",
              "csrf_token": _token(client)}, follow_redirects=False)
    assert twice.status_code == 303
    rereview = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"resolution_type": "duplicate", "evidence": "again",
              "csrf_token": _token(client)}, follow_redirects=False)
    assert rereview.status_code == 303
    assert "قبلاً" in unquote_plus(rereview.headers["location"])


def test_note_only_review_still_records_with_a_reason(client, db_session, authed):
    """One دلیل field: a lone note becomes the evidence, not a refusal."""
    transaction = _transaction(db_session)
    response = client.post(
        f"/admin/pos-reconciliation/{transaction.id}/review",
        data={"note": "terminal printed nothing", "csrf_token": _token(client)},
        follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(transaction)
    assert transaction.reconciled is True
    assert transaction.resolution_type == "terminal_error"
    assert transaction.resolution_evidence == "terminal printed nothing"


def test_page_names_risk_witnesses_alarm_and_terminal(client, db_session, authed):
    """The safety surface is painted: exposure, witnesses, alarm, modal."""
    from models import POSTransaction
    db_session.add(POSTransaction(checkout_nonce="phase4-alarm", amount=250000,
                                  host="127.0.0.1", port=8500, status="approved"))
    db_session.commit()
    fresh = client.get("/admin/pos-reconciliation").text
    assert "250,000 تومان" in fresh                          # money at risk, summed
    assert "بدون فاکتور" in fresh                             # the alarm row
    assert "check-overdue" in fresh
    assert 'id="pos-confirm"' in fresh
    assert "/static/js/pos.js" in fresh
    assert "نتیجه بررسی" in fresh                             # visible labels
    assert "دلیل و مدرک" in fresh
    assert "کارت‌خوان" in fresh                               # terminal status line

    # Resolve it: the row then names its witness and date, not just its note.
    alarm = db_session.query(POSTransaction).filter(
        POSTransaction.checkout_nonce == "phase4-alarm").one()
    assert client.post(
        f"/admin/pos-reconciliation/{alarm.id}/review",
        data={"resolution_type": "terminal_error", "evidence": "bank slip 7",
              "csrf_token": _token(client)}, follow_redirects=False).status_code == 303
    witnessed = client.get("/admin/pos-reconciliation").text
    assert "ثبت:" in witnessed
    assert "bank slip 7" in witnessed


def test_list_pages_searches_filters_and_sorts(client, db_session, authed):
    """The wall becomes a list: numbered pages, search, filters, sorting."""
    from models import POSTransaction
    for index in range(30):
        db_session.add(POSTransaction(checkout_nonce=f"phase4-list-{index}", amount=10_000 + index,
                                      host="127.0.0.1", port=8500, status="sent"))
    db_session.commit()

    first = client.get("/admin/pos-reconciliation?per_page=10")
    assert "30 مورد" in first.text
    assert 'aria-label="صفحه 3"' in first.text
    assert 'aria-current="page">3<' in client.get("/admin/pos-reconciliation?per_page=10&page=9").text

    by_amount = client.get("/admin/pos-reconciliation?q=10029")
    assert 'data-label="مبلغ"' in by_amount.text
    assert by_amount.text.count('data-label="شناسه"') == 1

    by_status = client.get("/admin/pos-reconciliation?status=resolved")
    assert "تراکنشی با این فیلترها پیدا نشد" in by_status.text
    assert "پاک‌کردن فیلتر" in by_status.text

    asc = client.get("/admin/pos-reconciliation?sort=amount&dir=asc&per_page=50").text
    desc = client.get("/admin/pos-reconciliation?sort=amount&dir=desc&per_page=50").text
    assert asc.index("10,000 تومان") < asc.index("10,029 تومان")
    assert desc.index("10,029 تومان") < desc.index("10,000 تومان")


def test_search_hit_is_visible_where_it_matched(client, db_session, authed):
    """A reference match prints the reference: no invisible hits."""
    from models import POSTransaction
    row = POSTransaction(checkout_nonce="phase4-ref", amount=50000,
                         host="127.0.0.1", port=8500, status="uncertain",
                         provider_reference="BANK-REF-991")
    db_session.add(row)
    db_session.commit()

    page = client.get("/admin/pos-reconciliation?q=BANK-REF-991").text
    assert "BANK-REF-991" in page
    assert "<mark>" in page
