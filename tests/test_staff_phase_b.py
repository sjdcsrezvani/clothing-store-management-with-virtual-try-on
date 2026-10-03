"""Phase B: safety hardening — toggles, validators, impact preview."""
from urllib.parse import unquote

from models import AdminLog, CashSession, StaffUser
from migrations import MIGRATION_VERSION
from services.security import effective_cap
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def _owner_client(client, db_session, name="phaseb-owner"):
    owner, password = _staff(db_session, name, "owner")
    _session_as(client, owner, password)
    return owner, password


def _flip(client, user, **caps):
    data = {"csrf_token": csrf_token(client, f"/admin/staff/{user.id}?tab=permissions")}
    data.update({key: "1" for key, on in caps.items() if on})
    return client.post(f"/admin/staff/{user.id}/caps", data=data, follow_redirects=False)


def test_effective_cap_is_narrow_only(db_session):
    cashier, _ = _staff(db_session, "phaseb-cap-c", "cashier")
    manager, _ = _staff(db_session, "phaseb-cap-m", "manager")
    owner, _ = _staff(db_session, "phaseb-cap-o", "owner")

    assert effective_cap(cashier, "can_discount") is False      # cashier default off
    assert effective_cap(manager, "can_refund") is True        # manager default on
    assert effective_cap(manager, "can_view_payroll") is False  # manager default off
    assert effective_cap(owner, "can_view_payroll") is True    # owner bypasses
    assert effective_cap(cashier, "can_nonsense") is False     # unknown denies

    manager.can_refund = False
    assert effective_cap(manager, "can_refund") is False       # toggle narrows role
    cashier.can_refund = True
    assert effective_cap(cashier, "can_refund") is False       # toggle never widens
    owner.can_view_payroll = False
    assert effective_cap(owner, "can_view_payroll") is True    # bypass holds


def test_caps_endpoint_flips_and_audits(client, db_session):
    _owner_client(client, db_session)
    manager, _ = _staff(db_session, "phaseb-flip", "manager")

    response = _flip(client, manager, can_refund=False, can_view_payroll=True)
    assert response.status_code == 303
    assert unquote(response.headers["location"]).endswith("?tab=permissions&msg=کلیدهای دسترسی ذخیره شد.")
    db_session.refresh(manager)
    assert manager.can_refund is False
    assert manager.can_view_payroll is True
    assert manager.can_discount is False                   # unchecked writes off
    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "staff_caps", AdminLog.target_id == manager.id).one()
    assert '"can_refund": null' in (audit.before_json or "")
    assert '"can_refund": false' in (audit.after_json or "")


def test_owner_row_pins_all_on(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-pin")
    second, _ = _staff(db_session, "phaseb-owner2", "owner")
    second.can_refund = False
    db_session.commit()

    _flip(client, second)                                       # empty form, owner row
    db_session.refresh(second)
    assert second.can_refund is True
    assert second.can_discount is True


def test_refund_refuses_without_capability(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-ref")
    manager, password = _staff(db_session, "phaseb-refmgr", "manager")
    _flip(client, manager, can_refund=False)
    _session_as(client, manager, password)
    token = csrf_token(client, "/sales/new")

    refused = client.post("/sales/999999/refund",
                          data={"csrf_token": token, "refund_reason": "x"},
                          follow_redirects=False)
    assert refused.status_code == 403                          # cap denies first

    _session_as(client, *_owner_client(client, db_session, name="phaseb-owner-ref2"))
    _flip(client, manager, can_refund=True)
    _session_as(client, manager, password)
    token = csrf_token(client, "/sales/new")
    passed = client.post("/sales/999999/refund",
                         data={"csrf_token": token, "refund_reason": "x"},
                         follow_redirects=False)
    assert passed.status_code == 404                           # past the guard, no sale


def test_payroll_receipt_and_pos_review_refuse_without_capability(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-guard")
    manager, password = _staff(db_session, "phaseb-guardmgr", "manager")
    _flip(client, manager, can_reconcile_pos=False)         # payroll already off
    _session_as(client, manager, password)
    token = csrf_token(client, "/sales/new")

    assert client.get("/admin/payroll/999999/receipt", follow_redirects=False).status_code == 403
    refused = client.post("/admin/pos-reconciliation/999/review",
                          data={"csrf_token": token, "resolution_type": "duplicate",
                                "evidence": "x"}, follow_redirects=False)
    assert refused.status_code == 403

    _session_as(client, *_owner_client(client, db_session, name="phaseb-owner-guard2"))
    _flip(client, manager, can_view_payroll=True, can_reconcile_pos=True)
    _session_as(client, manager, password)
    assert client.get("/admin/payroll/999999/receipt", follow_redirects=False).status_code == 404
    missing = client.post("/admin/pos-reconciliation/999/review",
                          data={"csrf_token": csrf_token(client, "/sales/new"),
                                "resolution_type": "duplicate", "evidence": "x"},
                          follow_redirects=False)
    assert missing.status_code == 303                          # past the guard, no row


def test_manual_discount_refuses_without_capability(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-disc")
    cashier, password = _staff(db_session, "phaseb-cashier", "cashier")
    _session_as(client, cashier, password)

    refused = client.post("/sales/apply-discount", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "customer_id": "0",
        "basket_json": "[]",
        "custom_discount_amount": "5000",
        "custom_discount_percent": "",
    })
    assert refused.status_code == 200
    assert "تخفیف دستی برای حساب شما فعال نیست" in refused.text

    _session_as(client, *_owner_client(client, db_session, name="phaseb-owner-disc2"))
    _flip(client, cashier, can_discount=True)
    _session_as(client, cashier, password)
    allowed = client.post("/sales/apply-discount", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "customer_id": "0",
        "basket_json": "[]",
        "custom_discount_amount": "5000",
        "custom_discount_percent": "",
    })
    assert allowed.status_code == 200
    assert "تخفیف دستی برای حساب شما فعال نیست" not in allowed.text


def test_identity_and_bank_validators(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-val")
    token = csrf_token(client, "/admin/staff/new")

    def attempt(**fields):
        data = {"csrf_token": token, "username": fields.pop("username"),
                "password": "valid-pass", "role": "cashier"}
        data.update(fields)
        return client.post("/admin/staff", data=data, follow_redirects=False)

    bad_code = attempt(username="phaseb-badnid", national_id="0012345678")
    assert bad_code.status_code == 303
    assert "کد ملی" in unquote(bad_code.headers["location"])

    bad_phone = attempt(username="phaseb-badph", phone="02122334455")
    assert "تلفن همراه" in unquote(bad_phone.headers["location"])

    bad_iban = attempt(username="phaseb-badiban", iban="IR000000000000000000000000")
    assert "شبا" in unquote(bad_iban.headers["location"])

    good = attempt(username="phaseb-good", national_id="0023456787",
                   phone="09120000000", iban="IR062960000000100324200001")
    assert good.status_code == 303
    assert db_session.query(StaffUser).filter(
        StaffUser.username == "phaseb-good").one().national_id == "0023456787"


def test_deactivation_names_what_it_strands(client, db_session):
    _owner_client(client, db_session, name="phaseb-owner-impact")
    worker, _ = _staff(db_session, "phaseb-impact", "cashier")
    worker.salary_amount = 10_000_000
    db_session.add(CashSession(cashier_user_id=worker.id, opening_balance=100_000, status="open"))
    db_session.commit()

    page = client.get(f"/admin/staff/{worker.id}").text
    assert "شیفت باز" in page
    assert "پرداخت‌نشده" in page


def test_revision_28_adds_null_toggle_columns(tmp_path):
    """Existing shops gain four NULL toggles: NULL reads as role default, so
    every account behaves exactly as before the upgrade."""
    from sqlalchemy import create_engine, text
    from migrations import upgrade
    engine = create_engine(f"sqlite:///{tmp_path / 'toggles.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 27)"))
        conn.execute(text("""
            CREATE TABLE staff_users (
                id INTEGER PRIMARY KEY, username VARCHAR(100), role VARCHAR(20))"""))
        conn.execute(text("INSERT INTO staff_users (username, role) VALUES ('vet-cashier', 'cashier')"))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT can_refund, can_discount, can_view_payroll, can_reconcile_pos"
            " FROM staff_users WHERE username = 'vet-cashier'")).one()
        assert tuple(row) == (None, None, None, None)
