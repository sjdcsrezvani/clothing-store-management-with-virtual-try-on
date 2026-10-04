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


def _tiny_png():
    import struct, zlib
    def chunk(kind, data):
        piece = kind + data
        return struct.pack(">I", len(data)) + piece + struct.pack(
            ">I", zlib.crc32(piece) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_logo_upload_replace_remove_and_doc_rendering(client, db_session):
    _owner_client(client, db_session, name="phaseh2-owner-logo")
    token = csrf_token(client, "/admin/owner-profile")

    bad = client.post("/admin/owner-profile", data={
        "csrf_token": token, "owner_full_name": "مالک",
    }, files={"logo": ("nope.txt", b"not an image", "text/plain")},
        follow_redirects=False)
    assert "PNG" in unquote(bad.headers["location"])

    first = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک",
    }, files={"logo": ("logo.png", _tiny_png(), "image/png")},
        follow_redirects=False)
    assert first.status_code == 303
    old_path = db_session.query(Settings).filter(
        Settings.key == "owner_logo_path").one().value
    assert old_path.startswith("/static/uploads/business/logo-")

    second = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک",
    }, files={"logo": ("logo.png", _tiny_png(), "image/png")},
        follow_redirects=False)
    assert second.status_code == 303
    import os
    assert not os.path.exists(old_path.lstrip("/"))           # old file gone

    removed = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک", "remove_logo": "1",
    }, follow_redirects=False)
    assert removed.status_code == 303
    assert db_session.query(Settings).filter(
        Settings.key == "owner_logo_path").one().value == ""

    page = client.get("/admin/owner-profile").text
    assert "در سربرگ قرارداد و رسید" in page


def test_bank_web_fields_validate_and_print(client, db_session):
    _owner_client(client, db_session, name="phaseh2-owner-bank")
    bad_iban = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک", "owner_bank_iban": "IR000",
    }, follow_redirects=False)
    assert "شبا" in unquote(bad_iban.headers["location"])

    bad_web = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک", "owner_website": "not a site",
    }, follow_redirects=False)
    assert "وب‌سایت" in unquote(bad_web.headers["location"])

    good = client.post("/admin/owner-profile", data={
        "csrf_token": csrf_token(client, "/admin/owner-profile"),
        "owner_full_name": "مالک", "owner_business_name": "رای‌کیدز",
        "owner_address": "تهران", "owner_bank_account": "123456",
        "owner_bank_iban": "IR062960000000100324200001",
        "owner_website": "raykid.example", "owner_instagram": "@raykid_store",
    }, files={"logo": ("logo.png", _tiny_png(), "image/png")},
        follow_redirects=False)
    assert good.status_code == 303
    values = {row.key: row.value for row in db_session.query(Settings).filter(
        Settings.key.like("owner_%")).all()}
    assert values["owner_bank_iban"] == "IR062960000000100324200001"
    assert values["owner_instagram"] == "raykid_store"        # @ stripped
    assert values["owner_logo_path"].startswith("/static/uploads/business/")

    import re
    worker, _ = _staff(db_session, "phaseh2-doc", "cashier")
    worker.full_name = "کارمند نمونه"
    worker.national_id = "0023456787"
    worker.address = "تهران"
    worker.job_title = "فروشنده"
    worker.salary_amount = 10_000_000
    from datetime import datetime, timezone
    worker.hire_date = datetime(2026, 3, 21, tzinfo=timezone.utc)
    db_session.commit()
    doc = client.get(f"/admin/staff/{worker.id}/contract").text
    assert "مهر شرکت" in doc
    assert "raykid.example" in doc
    assert "@raykid_store" in doc
    assert 'src="/static/uploads/business/logo-' in doc
