"""Contract-grade employment: positions, identity fields, gated document."""
from urllib.parse import unquote

from models import AdminLog, JobPosition, StaffUser
from migrations import MIGRATION_VERSION
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_staff_phase_b import _owner_client


def _hire_full(client, db_session, username, position_id=None, **extra):
    data = {"csrf_token": csrf_token(client, "/admin/staff/new"),
            "username": username, "password": "hire-pass", "role": "cashier",
            "full_name": "قراردادی نمونه", "national_id": "0023456787",
            "phone": "09120000000", "address": "تهران، خیابان نمونه",
            "job_title": "فروشنده", "hire_date": "1405/01/01",
            "contract_term_months": "12", "salary_amount": "15000000",
            "gender": "female", "insured": "1"}
    if position_id is not None:
        data["position_id"] = str(position_id)
    data.update(extra)
    return client.post("/admin/staff", data=data, follow_redirects=False)


def _owner_profile(client, **fields):
    data = {"csrf_token": csrf_token(client, "/admin/owner-profile")}
    data.update(fields)
    return client.post("/admin/owner-profile", data=data, follow_redirects=False)


def test_positions_crud_with_audit(client, db_session):
    _owner_client(client, db_session, name="phaseg-owner-pos")
    token = csrf_token(client, "/admin/positions")

    assert client.get("/admin/positions").status_code == 200
    response = client.post("/admin/positions", data={
        "csrf_token": token, "title": "فروشنده ارشد"}, follow_redirects=False)
    assert response.status_code == 303
    position = db_session.query(JobPosition).filter(
        JobPosition.title == "فروشنده ارشد").one()

    duplicate = client.post("/admin/positions", data={
        "csrf_token": token, "title": "فروشنده ارشد"}, follow_redirects=False)
    assert "قبلاً ثبت شده" in unquote(duplicate.headers["location"])

    rename = client.post(f"/admin/positions/{position.id}", data={
        "csrf_token": token, "title": "فروشنده", "is_active": "1"},
        follow_redirects=False)
    assert rename.status_code == 303
    db_session.refresh(position)
    assert position.title == "فروشنده"
    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "position_update", AdminLog.target_id == position.id).one()
    assert '"title": "فروشنده"' in (audit.after_json or "")

    retire = client.post(f"/admin/positions/{position.id}", data={
        "csrf_token": token, "title": "فروشنده"}, follow_redirects=False)
    assert retire.status_code == 303
    db_session.refresh(position)
    assert position.is_active is False

    cashier, password = _staff(db_session, "phaseg-peek", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/positions", follow_redirects=False).status_code == 403


def test_identity_fields_validate_and_save(client, db_session):
    _owner_client(client, db_session, name="phaseg-owner-id")
    token = csrf_token(client, "/admin/positions")
    client.post("/admin/positions", data={
        "csrf_token": token, "title": "صندوقدار"}, follow_redirects=False)
    position = db_session.query(JobPosition).filter(
        JobPosition.title == "صندوقدار").one()

    bad_gender = _hire_full(client, db_session, "phaseg-badg",
                            position_id=position.id, gender="other")
    assert "جنسیت" in unquote(bad_gender.headers["location"])

    bad_position = _hire_full(client, db_session, "phaseg-badp", position_id=999999)
    assert "سمت سازمانی" in unquote(bad_position.headers["location"])

    good = _hire_full(client, db_session, "phaseg-good", position_id=position.id)
    assert good.status_code == 303
    user = db_session.query(StaffUser).filter(
        StaffUser.username == "phaseg-good").one()
    assert (user.gender, user.insured, user.position_id) == ("female", True, position.id)


def test_contract_refuses_until_complete_then_prints_clean(client, db_session):
    owner, _ = _owner_client(client, db_session, name="phaseg-owner-con")
    bare, _ = _staff(db_session, "phaseg-bare", "cashier")

    gate = client.get(f"/admin/staff/{bare.id}/contract")
    assert gate.status_code == 200
    assert "پیش از چاپ" in gate.text
    assert "کد ملی کارمند" in gate.text
    assert "قرارداد کار" not in gate.text                       # no document yet

    _owner_profile(client, owner_full_name="مالک نمونه",
                   owner_business_name="رای‌کیدز", owner_address="تهران")
    token = csrf_token(client, f"/admin/staff/{bare.id}")
    client.post(f"/admin/staff/{bare.id}", data={
        "csrf_token": token, "role": "cashier", "full_name": "کارمند کامل",
        "national_id": "0023456787", "address": "تهران",
        "job_title": "فروشنده", "hire_date": "1405/01/01",
        "salary_amount": "15000000",
    }, follow_redirects=False)

    doc = client.get(f"/admin/staff/{bare.id}/contract").text
    assert "قرارداد کار" in doc
    assert "سرکار خانم" not in doc                               # gender unset
    assert "پانزده میلیون تومان" in doc                          # words beside digits
    assert "تأمین اجتماعی" not in doc                            # not insured
    assert "یادداشت" not in doc or "یادداشت‌های پرسنلی" in doc   # no internal leak
    assert "دوره آزمایشی" in doc
    assert "دو نسخه" in doc

    assert client.get("/admin/staff/999999/contract").status_code == 404


def test_contract_insurance_gender_and_position_variants(client, db_session):
    _owner_client(client, db_session, name="phaseg-owner-var")
    _owner_profile(client, owner_full_name="مالک نمونه",
                   owner_business_name="رای‌کیدز", owner_address="تهران")
    token = csrf_token(client, "/admin/positions")
    client.post("/admin/positions", data={
        "csrf_token": token, "title": "انباردار"}, follow_redirects=False)
    position = db_session.query(JobPosition).filter(
        JobPosition.title == "انباردار").one()
    _hire_full(client, db_session, "phaseg-var", position_id=position.id,
               gender="male", insured="1")
    user = db_session.query(StaffUser).filter(
        StaffUser.username == "phaseg-var").one()

    doc = client.get(f"/admin/staff/{user.id}/contract").text
    assert "جناب آقای" in doc
    assert "انباردار" in doc
    assert "تأمین اجتماعی" in doc


def test_revision_32_adds_identity_columns_and_positions(tmp_path):
    """Old shops gain gender/insurance/position columns (NULL = as before)
    and an empty positions directory."""
    from sqlalchemy import create_engine, text
    from migrations import upgrade
    engine = create_engine(f"sqlite:///{tmp_path / 'con32.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 31)"))
        conn.execute(text("CREATE TABLE staff_users (id INTEGER PRIMARY KEY, username VARCHAR(100))"))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(staff_users)")).all()}
        assert {"gender", "insured", "position_id"} <= cols
        assert conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_positions'"
        )).scalar() == "job_positions"
