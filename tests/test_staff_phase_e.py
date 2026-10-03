"""Remaining ideas: discount ceilings, history lens, richer audit."""
from urllib.parse import unquote

from models import AdminLog, StaffUser
from migrations import MIGRATION_VERSION
from services.security import discount_allowed, discount_limit
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_staff_phase_b import _owner_client, _flip


def test_discount_limit_matrix(db_session):
    cashier, _ = _staff(db_session, "phasee-cap-c", "cashier")
    manager, _ = _staff(db_session, "phasee-cap-m", "manager")
    owner, _ = _staff(db_session, "phasee-cap-o", "owner")

    assert discount_allowed(cashier, 0, 0) == (True, "")       # granting nothing
    assert discount_allowed(cashier, 1000, 0)[0] is False      # no toggle
    cashier.can_discount = True
    assert discount_allowed(cashier, 1, 0)[0] is False         # 0-ceiling
    assert "سقف" in discount_allowed(cashier, 1, 0)[1]
    # A personal grant never widens past the role ceiling: cashiers top out
    # at the role's zero, so the key alone changes nothing for them.
    cashier.max_discount_amount = 50_000
    assert discount_limit(cashier, "amount") == 0
    assert discount_allowed(cashier, 40_000, 0)[0] is False

    assert discount_allowed(manager, 150_000, 0) == (True, "")  # role default
    assert discount_allowed(manager, 250_000, 0)[0] is False
    assert discount_allowed(manager, 0, 15)[0] is False
    assert discount_allowed(manager, 0, 10) == (True, "")
    manager.max_discount_amount = 999_999                      # clamped to role
    assert discount_limit(manager, "amount") == 200_000
    assert discount_allowed(manager, 250_000, 0)[0] is False
    manager.max_discount_amount = 50_000                       # narrows role
    assert discount_limit(manager, "amount") == 50_000

    assert discount_allowed(owner, 9_999_999, 99) == (True, "")  # bypass
    assert discount_limit(cashier, "banana") == 0              # unknown unit


def test_limits_save_and_enforce_at_the_till(client, db_session):
    _owner_client(client, db_session, name="phasee-owner-lim")
    manager, password = _staff(db_session, "phasee-manager", "manager")

    response = client.post(f"/admin/staff/{manager.id}/caps", data={
        "csrf_token": csrf_token(client, f"/admin/staff/{manager.id}?tab=permissions"),
        "can_discount": "1",
        "max_discount_amount": "50000",
        "max_discount_percent": "5",
    }, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(manager)
    assert (manager.max_discount_amount, manager.max_discount_percent) == (50_000, 5)
    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "staff_caps", AdminLog.target_id == manager.id).one()
    assert '"max_discount_amount": 50000' in (audit.after_json or "")

    bad = client.post(f"/admin/staff/{manager.id}/caps", data={
        "csrf_token": csrf_token(client, f"/admin/staff/{manager.id}?tab=permissions"),
        "max_discount_amount": "-3",
    }, follow_redirects=False)
    assert "منفی" in unquote(bad.headers["location"])

    _session_as(client, manager, password)
    refused = client.post("/sales/apply-discount", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "customer_id": "0", "basket_json": "[]",
        "custom_discount_amount": "60000", "custom_discount_percent": "",
    })
    assert "از سقف" in refused.text
    allowed = client.post("/sales/apply-discount", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "customer_id": "0", "basket_json": "[]",
        "custom_discount_amount": "40000", "custom_discount_percent": "",
    })
    assert "از سقف" not in allowed.text


def test_history_lens_filters_to_contract_and_pay(client, db_session):
    _owner_client(client, db_session, name="phasee-owner-hist")
    worker, _ = _staff(db_session, "phasee-hist", "cashier")
    token = csrf_token(client, f"/admin/staff/{worker.id}")
    client.post(f"/admin/staff/{worker.id}", data={
        "csrf_token": token, "role": "cashier", "job_title": "فروشنده",
    }, follow_redirects=False)
    client.post(f"/admin/staff/{worker.id}/disable",
                data={"csrf_token": token}, follow_redirects=False)

    full = client.get(f"/admin/staff/{worker.id}?tab=history").text
    assert "بستن دسترسی" in full
    assert "ویرایش پرونده" in full

    lens = client.get(f"/admin/staff/{worker.id}?tab=history&kind=contract").text
    assert "ویرایش پرونده" in lens
    assert "بستن دسترسی" not in lens

    degraded = client.get(f"/admin/staff/{worker.id}?tab=history&kind=banana").text
    assert "بستن دسترسی" in degraded                           # unknown degrades

    db_session.refresh(worker)
    assert worker.job_title == "فروشنده"
    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "staff_update", AdminLog.target_id == worker.id).one()
    assert '"job_title": "فروشنده"' in (audit.after_json or "")
    assert "contract_term_months" in (audit.after_json or "")


def test_revision_31_adds_nullable_ceilings(tmp_path):
    """Old shops gain NULL ceilings: NULL reads as role default, so every
    account discounts exactly as before the upgrade."""
    from sqlalchemy import create_engine, text
    from migrations import upgrade
    engine = create_engine(f"sqlite:///{tmp_path / 'lim31.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 30)"))
        conn.execute(text("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username VARCHAR(100))"))
        conn.execute(text("INSERT INTO staff_users (username) VALUES ('vet-cashier')"))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT max_discount_amount, max_discount_percent FROM staff_users")).one()
        assert tuple(row) == (None, None)
