"""Business identity: rename, validators, signatory, preview parity."""
from urllib.parse import unquote

from models import AdminLog, Settings, StaffUser
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_staff_phase_b import _owner_client


def _save(client, **fields):
    data = {"csrf_token": csrf_token(client, "/admin/owner-profile")}
    data.update(fields)
    return client.post("/admin/owner-profile", data=data, follow_redirects=False)


def test_page_names_the_employer_not_the_login(client, db_session):
    _owner_client(client, db_session, name="phaseh-owner-page")
    page = client.get("/admin/owner-profile").text
    assert "هویت کسب‌وکار" in page
    assert "نه حساب ورود" in page
    assert "امضاکننده" in page


def test_identity_validators_refuse_garbage(client, db_session):
    _owner_client(client, db_session, name="phaseh-owner-val")
    bad_code = _save(client, owner_full_name="مالک",
                     owner_national_id="1234567890")
    assert bad_code.status_code == 303
    assert "کد ملی" in unquote(bad_code.headers["location"])

    bad_phone = _save(client, owner_full_name="مالک", owner_phone="0212233")
    assert "تلفن همراه" in unquote(bad_phone.headers["location"])

    bad_signer = _save(client, owner_full_name="مالک",
                       owner_signatory_user_id="999999")
    assert "امضاکننده" in unquote(bad_signer.headers["location"])

    good = _save(client, owner_full_name="مالک نمونه",
                 owner_national_id="0023456787", owner_phone="09120000000",
                 owner_business_name="رای‌کیدز", owner_address="تهران")
    assert good.status_code == 303
    assert db_session.query(Settings).filter(
        Settings.key == "owner_national_id",
        Settings.value == "0023456787").count() == 1


def test_signatory_picker_signs_docs_and_preview_matches_gate(client, db_session):
    owner, _ = _owner_client(client, db_session, name="phaseh-owner-sig")
    signer, _ = _staff(db_session, "phaseh-signer", "manager")
    signer.full_name = "امضاکننده نمونه"
    db_session.commit()
    _save(client, owner_full_name="مالک نمونه",
          owner_business_name="رای‌کیدز", owner_address="تهران",
          owner_signatory_user_id=str(signer.id))

    worker, _ = _staff(db_session, "phaseh-worker", "cashier")
    worker.full_name = "کارمند نمونه"
    worker.national_id = "0023456787"
    worker.address = "تهران"
    worker.job_title = "فروشنده"
    worker.salary_amount = 10_000_000
    from datetime import datetime, timezone
    worker.hire_date = datetime(2026, 3, 21, tzinfo=timezone.utc)
    db_session.commit()

    doc = client.get(f"/admin/staff/{worker.id}/contract").text
    assert "امضاکننده نمونه" in doc
    assert "پیش از چاپ" not in doc

    feed = client.get("/admin/owner-profile").text
    assert "آماده چاپ" in feed

    cashier, password = _staff(db_session, "phaseh-peek", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/owner-profile", follow_redirects=False).status_code == 403
    _session_as(client, owner, "role-pass")

    audit = db_session.query(AdminLog).filter(
        AdminLog.action == "owner_profile_update").all()
    assert len(audit) >= 1
    assert "هویت کسب‌وکار" in client.get("/admin/owner-profile").text


def test_empty_business_shows_the_same_three_gate_items(client, db_session):
    _owner_client(client, db_session, name="phaseh-owner-empty")
    page = client.get("/admin/owner-profile").text
    for label in ("نام مالک", "نام کسب‌وکار", "نشانی کسب‌وکار"):
        assert label in page
