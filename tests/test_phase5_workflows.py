from models import Supplier, Purchase, SupplierPayment, CashSession
from tests.test_accounting import csrf_token


def test_supplier_payment_and_balance(client, db_session, authed):
    supplier = Supplier(name="Test supplier")
    db_session.add(supplier)
    db_session.commit()
    purchase = Purchase(supplier_id=supplier.id, total_cost=1000)
    db_session.add(purchase)
    db_session.commit()
    token = csrf_token(client, "/admin/suppliers")
    response = client.post(f"/admin/suppliers/{supplier.id}/payment", data={"amount": "400", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    payment = db_session.query(SupplierPayment).one()
    assert payment.amount == 400


def test_cash_session_open_and_close(client, db_session, authed):
    token = csrf_token(client, "/admin/cashbox")
    response = client.post("/admin/cashbox/open", data={"opening": "500", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    session = db_session.query(CashSession).one()
    assert session.status == "open"
    token = csrf_token(client, "/admin/cashbox")
    response = client.post("/admin/cashbox/close", data={"counted": "500", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(session)
    assert session.status == "closed"
    assert session.variance == 0
