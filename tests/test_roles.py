import json

from models import AdminLog, Product, ProductVariant, Sale, StaffUser
from tests.conftest import csrf_token
from tests.test_sales_money import _make_variant


def _login(client, username="owner", password="test-admin-pass"):
    token = csrf_token(client)
    response = client.post("/admin/login", data={
        "username": username,
        "password": password,
        "csrf_token": token,
    }, follow_redirects=False)
    assert response.status_code == 303


def _staff(db_session, username, role, password="role-pass"):
    from services.security import hash_password
    user = StaffUser(username=username, password_hash=hash_password(password), role=role)
    db_session.add(user)
    db_session.commit()
    return user, password


def _session_as(client, user, password):
    _login(client, user.username, password)


def test_unauthenticated_checkout_and_sensitive_routes_are_denied(client):
    assert client.get("/sales/new", follow_redirects=False).status_code == 303
    assert client.get("/admin/settings", follow_redirects=False).status_code == 303
    assert client.get("/admin/analytics", follow_redirects=False).status_code == 303


def test_cashier_can_checkout_but_cannot_refund_or_change_settings(client, db_session):
    cashier, password = _staff(db_session, "cashier-one", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/sales/new").status_code == 200
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403
    assert client.get("/admin/analytics", follow_redirects=False).status_code == 403
    assert client.post("/admin/reset-database", data={"csrf_token": csrf_token(client, "/sales/new")}, follow_redirects=False).status_code == 403


def test_manager_can_manage_inventory_and_credit_but_not_owner_operations(client, db_session):
    manager, password = _staff(db_session, "manager-one", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin/products").status_code == 200
    assert client.get("/admin/credit").status_code == 200
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403
    assert client.get("/admin/analytics", follow_redirects=False).status_code == 403
    assert client.get("/admin/staff", follow_redirects=False).status_code == 403
    assert client.post("/admin/reset-database", data={"csrf_token": csrf_token(client, "/sales/new")}, follow_redirects=False).status_code == 403


def test_owner_can_access_owner_operations(client, db_session):
    owner, password = _staff(db_session, "owner-two", "owner")
    _session_as(client, owner, password)
    assert client.get("/admin/settings").status_code == 200
    assert client.get("/admin/analytics").status_code == 200
    assert client.get("/admin/staff").status_code == 200


def test_successful_login_records_staff_identity(client, db_session):
    user, password = _staff(db_session, "audit-user", "cashier", "audit-pass")
    _login(client, "audit-user", password)
    db_session.expire_all()
    user = db_session.query(StaffUser).filter(StaffUser.id == user.id).one()
    assert user.last_login_at is not None
    audit = db_session.query(AdminLog).filter(AdminLog.action == "login", AdminLog.staff_user_id == user.id).order_by(AdminLog.id.desc()).first()
    assert audit is not None
    assert audit.ip_address
    assert audit.request_id is None


def test_audit_log_carries_target_and_before_after_for_staff_change(client, db_session):
    owner, password = _staff(db_session, "audit-owner", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/sales/new")
    response = client.post("/admin/staff", data={
        "username": "new-cashier",
        "password": "new-pass",
        "role": "cashier",
        "csrf_token": token,
    }, follow_redirects=False)
    assert response.status_code == 303
    user = db_session.query(StaffUser).filter(StaffUser.username == "new-cashier").one()
    audit = db_session.query(AdminLog).filter(AdminLog.action == "staff_create", AdminLog.target_id == user.id).one()
    assert audit.staff_user_id == owner.id
    assert '"role": "cashier"' in audit.after_json
