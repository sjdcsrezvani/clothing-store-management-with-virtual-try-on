from models import Payment, PaymentReversal, Refund, FinancialEntry, Sale
from tests.test_accounting import _make_customer, _make_variant, _confirm_sale, csrf_token


def test_payment_reversal_keeps_original_row(client, db_session, authed):
    customer = _make_customer(db_session)
    customer.total_debt = 100
    db_session.add(Payment(customer_id=customer.id, amount=100, method="cash"))
    db_session.commit()
    payment = db_session.query(Payment).first()
    token = csrf_token(client, f"/admin/credit/{customer.id}")
    response = client.post("/admin/payments/%s/delete" % payment.id, data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.query(Payment).filter_by(id=payment.id).one().reversed_at is not None
    assert db_session.query(PaymentReversal).filter_by(payment_id=payment.id).count() == 1


def test_refund_creates_immutable_ledger_record(client, db_session, authed):
    _, variant = _make_variant(db_session, price=1000, stock=2)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 1000, "quantity": 1, "total_price": 1000}]
    _confirm_sale(client, basket, extra={"payment_method": "cash"})
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    token = csrf_token(client, f"/sales/invoice/{sale.id}")
    response = client.post(f"/sales/{sale.id}/refund", data={"refund_reason": "test", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(Refund).filter_by(sale_id=sale.id).count() == 1
    assert db_session.query(FinancialEntry).filter_by(entry_type="refund").count() == 1
