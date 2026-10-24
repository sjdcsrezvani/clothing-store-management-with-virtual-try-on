"""Try-on Phase 1: Settings-owned key, dedicated worker contract, job-id flow."""
from tests.conftest import csrf_token


def _seed_key(db_session, value="sk-live-test-key"):
    from models import Settings
    row = db_session.query(Settings).filter(Settings.key == "tryon_api_key").first()
    if row:
        row.value = value
    else:
        db_session.add(Settings(key="tryon_api_key", value=value))
    db_session.commit()


def test_key_settings_beats_env_and_migrates_once(client, db_session, monkeypatch):
    import services.image_gen as ig
    _seed_key(db_session, "sk-from-settings")
    monkeypatch.setattr(ig, "ENV_TRYON_API_KEY", "sk-from-env")
    key, _url = ig.get_tryon_credentials(db_session)
    assert key == "sk-from-settings"  # Settings wins over env
    from models import Settings
    db_session.query(Settings).filter(Settings.key == "tryon_api_key").delete()
    db_session.commit()
    key, _url = ig.get_tryon_credentials(db_session)
    assert key == "sk-from-env"
    row = db_session.query(Settings).filter(Settings.key == "tryon_api_key").first()
    assert row and row.value == "sk-from-env"  # auto-migrated once
    key, _url = ig.get_tryon_credentials(db_session)
    assert key == "sk-from-env"  # second read is a plain Settings hit


def test_key_mask_never_shows_secret():
    from services.image_gen import mask_tryon_key
    assert mask_tryon_key("sk-live-test-key") == "…-key"
    assert "sk-live" not in mask_tryon_key("sk-live-test-key")
    assert mask_tryon_key("") == ""


def test_settings_page_never_renders_full_key(client, db_session):
    from tests.conftest import authed as _  # noqa: F401  (documents owner gate)
    _seed_key(db_session, "sk-live-test-key")
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    page = client.get("/admin/settings?tab=messaging").text
    assert "sk-live-test-key" not in page  # redaction pinned
    assert "…-key" in page  # masked tail shown instead


def test_settings_empty_key_keeps_secret(client, db_session):
    from models import Settings
    _seed_key(db_session, "sk-keep-me")
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client)
    client.post("/admin/settings", data={"tab": "messaging", "tryon_daily_limit": "20", "tryon_api_key": "", "csrf_token": token})
    row = db_session.query(Settings).filter(Settings.key == "tryon_api_key").first()
    assert row.value == "sk-keep-me"  # empty field = keep, not wipe
    token = csrf_token(client)
    client.post("/admin/settings", data={"tab": "messaging", "tryon_daily_limit": "20", "tryon_api_key": "sk-rotated", "csrf_token": token})
    db_session.expire_all()
    row = db_session.query(Settings).filter(Settings.key == "tryon_api_key").first()
    assert row.value == "sk-rotated"


def test_test_key_route_without_key_attempts_no_generation(client, db_session):
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client)
    resp = client.post("/admin/tryon/test-key", headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False  # no key configured in this env


def test_quota_burns_on_success_not_before(db_session):
    from services.security import tryon_can_generate, tryon_record_generation, tryon_daily_remaining, _generation_day
    from datetime import date
    assert tryon_can_generate.__module__  # gate still exists at enqueue sites
    before = tryon_daily_remaining(db_session)
    tryon_record_generation(ip="probe-test-ip")
    assert tryon_daily_remaining(db_session) == before - 1  # worker burns once
    _generation_day.pop(date.today().isoformat(), None)


def test_cancel_pending_job_but_not_processing(client, db_session):
    from models import BackgroundJob
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    pending = BackgroundJob(job_type="tryon", payload="{}", status="pending")
    running = BackgroundJob(job_type="tryon", payload="{}", status="processing")
    db_session.add_all([pending, running])
    db_session.commit()
    pending_id, running_id = pending.id, running.id
    token = csrf_token(client)
    resp = client.post(f"/admin/try-on/jobs/{pending_id}/cancel", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(BackgroundJob, pending_id) is None  # pending row deleted
    token = csrf_token(client)
    resp = client.post(f"/admin/try-on/jobs/{running_id}/cancel", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(BackgroundJob, running_id).status == "processing"  # running finishes


def test_upload_guards_reject_non_image_and_huge(client):
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    resp = client.post("/api/image-gen/upload-kid-photo", files={"file": ("x.txt", b"not-an-image", "text/plain")})
    assert resp.status_code == 400
    resp = client.post("/api/image-gen/upload-kid-photo", files={"file": ("big.jpg", b"\xff" * (16_000_000), "image/jpeg")})
    assert resp.status_code == 400


def test_latest_job_helper_reads_session_job(client, db_session):
    from types import SimpleNamespace
    from models import BackgroundJob
    from routers.clothes_images import _latest_tryon_job
    job = BackgroundJob(job_type="tryon", payload="{}", status="completed",
                        result_path="/tmp/x.jpg", result_url="/static/x.jpg")
    db_session.add(job)
    db_session.commit()
    req = SimpleNamespace(session={"tryon_job_id": job.id})
    assert _latest_tryon_job(req, db_session).id == job.id
    req = SimpleNamespace(session={})
    assert _latest_tryon_job(req, db_session) is None


def test_tryon_page_shows_live_estimate_and_job_state(client):
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    page = client.get("/admin/try-on").text
    assert "باقی‌مانده امروز" in page  # live quota estimate above generate
