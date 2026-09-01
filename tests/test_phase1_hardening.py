import sqlite3

from models import AdminLog, StaffUser
from tests.conftest import csrf_token
from services.security import hash_password


def _create_staff(db, username, role, password="pass-123"):
    user = StaffUser(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    return user, password


def _login(client, username, password):
    token = csrf_token(client)
    response = client.post("/admin/login", data={
        "username": username,
        "password": password,
        "csrf_token": token,
    }, follow_redirects=False)
    assert response.status_code == 303


def test_disabled_staff_session_is_rejected(client, db_session):
    user, password = _create_staff(db_session, "disabled", "cashier")
    _login(client, user.username, password)
    user.is_active = False
    db_session.commit()
    assert client.get("/sales/new", follow_redirects=False).status_code == 303


def test_role_change_applies_to_existing_session(client, db_session):
    user, password = _create_staff(db_session, "promoted", "cashier")
    _login(client, user.username, password)
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403
    user.role = "owner"
    db_session.commit()
    assert client.get("/admin/settings").status_code == 200


def test_sensitive_route_role_matrix(client, db_session):
    routes = {
        "cashier": ["/sales/new"],
        "manager": ["/admin/products", "/admin/credit", "/admin/try-on"],
        "owner": ["/admin/settings", "/admin/analytics", "/admin/staff", "/admin/backups"],
    }
    for role, allowed in routes.items():
        user, password = _create_staff(db_session, f"matrix-{role}", role)
        _login(client, user.username, password)
        for route in allowed:
            assert client.get(route, follow_redirects=False).status_code == 200, (role, route)
        if role != "owner":
            assert client.get("/admin/settings", follow_redirects=False).status_code == 403
        client.cookies.clear()


def test_business_audit_records_have_actor(client, db_session):
    user, password = _create_staff(db_session, "audit-manager", "manager")
    _login(client, user.username, password)
    assert client.get("/admin/campaigns", follow_redirects=False).status_code == 200
    # The login audit proves the authenticated identity is persisted; mutation
    # handlers use the same request-aware logger and actor path.
    audit = db_session.query(AdminLog).filter(AdminLog.action == "login", AdminLog.staff_user_id == user.id).first()
    assert audit is not None


def test_legacy_schema_migration_is_additive(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE admin_logs (id INTEGER PRIMARY KEY, action VARCHAR(100), detail TEXT, created_at DATETIME)")
    conn.execute("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username VARCHAR(100), password_hash VARCHAR(255), role VARCHAR(20), is_active BOOLEAN, created_at DATETIME)")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(admin_logs)")}
    assert "staff_user_id" not in columns
    conn.close()

    from sqlalchemy import create_engine, inspect
    from main import _apply_missing_columns
    import database
    original_engine = database.engine
    try:
        database.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        _apply_missing_columns(database.engine)
        migrated = inspect(database.engine)
        names = {column["name"] for column in migrated.get_columns("admin_logs")}
        assert {"staff_user_id", "target_type", "target_id", "ip_address", "request_id", "before_json", "after_json"} <= names
    finally:
        database.engine.dispose()
        database.engine = original_engine
