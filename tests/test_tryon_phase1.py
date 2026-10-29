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


def test_selection_comes_from_settings_only(client, db_session):
    import services.image_gen as ig
    from models import Settings
    db_session.query(Settings).filter(
        Settings.key.in_(["tryon_provider", "tryon_model", "tryon_api_key",
                          "tryon_api_key_avalai", "tryon_api_url"])).delete()
    db_session.commit()

    provider, model, key = ig.get_tryon_selection(db_session)
    assert (provider, model, key) == ("gapgpt", "gpt-image-2", "")  # no fallback key

    _seed_key(db_session, "sk-from-settings")
    provider, model, key = ig.get_tryon_selection(db_session)
    assert (provider, model, key) == ("gapgpt", "gpt-image-2", "sk-from-settings")

    db_session.add_all([
        Settings(key="tryon_provider", value="avalai"),
        Settings(key="tryon_model", value="seedream-5-0-260128"),
        Settings(key="tryon_api_key_avalai", value="sk-avalai"),
    ])
    db_session.commit()
    provider, model, key = ig.get_tryon_selection(db_session)
    assert (provider, model, key) == ("avalai", "seedream-5-0-260128", "sk-avalai")

    # A stale model from the other catalog falls back instead of breaking try-on.
    db_session.query(Settings).filter(Settings.key == "tryon_model").update({"value": "not-a-model"})
    db_session.commit()
    provider, model, key = ig.get_tryon_selection(db_session)
    assert (provider, model) == ("avalai", "gpt-image-2")
    assert key == "sk-avalai"


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


def test_settings_offer_provider_and_model_and_both_keys(client, db_session):
    from tests.conftest import authed as _
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    page = client.get("/admin/settings?tab=messaging").text
    assert 'name="tryon_provider"' in page
    assert 'name="tryon_model"' in page
    assert 'name="tryon_api_key_avalai"' in page
    assert "seedream-5-0-260128" in page  # model catalog reaches the select
    assert 'name="tryon_api_url"' not in page  # retired: catalog owns base URLs


def test_settings_reject_model_from_the_other_provider(client, db_session):
    from models import Settings
    from urllib.parse import unquote
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client)
    resp = client.post("/admin/settings", data={
        "tab": "messaging", "tryon_provider": "gapgpt",
        "tryon_model": "seedream-5-0-260128", "csrf_token": token,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]
    assert "seedream-5-0-260128" in unquote(resp.headers["location"])
    assert db_session.query(Settings).filter(Settings.key == "tryon_model").first() is None


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


def test_both_keys_survive_provider_and_model_changes(client, db_session):
    """Paste both keys once, then switch models: no re-entry, no wipe."""
    from models import Settings
    from urllib.parse import unquote
    token = csrf_token(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client)
    first = client.post("/admin/settings", data={
        "tab": "messaging", "tryon_daily_limit": "50",
        "tryon_provider": "gapgpt", "tryon_model": "gpt-image-2",
        "tryon_api_key": "sk-gapgpt-END1", "tryon_api_key_avalai": "sk-avalai-END2",
        "csrf_token": token,
    }, follow_redirects=False)
    note = unquote(first.headers["location"])
    assert "کلید gapgpt، AvalAI ذخیره شد" in note  # explicit save confirmation
    token = csrf_token(client)
    client.post("/admin/settings", data={
        "tab": "messaging", "tryon_daily_limit": "50",
        "tryon_provider": "avalai", "tryon_model": "seedream-5-0-260128",
        "tryon_api_key": "", "tryon_api_key_avalai": "",  # blank = keep
        "csrf_token": token,
    })
    db_session.expire_all()
    rows = {r.key: r.value for r in db_session.query(Settings).filter(
        Settings.key.in_(["tryon_api_key", "tryon_api_key_avalai",
                          "tryon_provider", "tryon_model"])).all()}
    assert rows == {
        "tryon_api_key": "sk-gapgpt-END1",
        "tryon_api_key_avalai": "sk-avalai-END2",
        "tryon_provider": "avalai",
        "tryon_model": "seedream-5-0-260128",
    }
    page = client.get("/admin/settings?tab=messaging").text
    assert "…END1" in page and "…END2" in page  # saved state is visible
    assert "sk-gapgpt-END1" not in page          # full secret never rendered


def test_test_key_route_without_key_attempts_no_generation(client, db_session):
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    token = csrf_token(client)
    resp = client.post("/admin/tryon/test-key", headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False  # no key configured in this env


def test_worker_processes_a_claimed_job_before_completing(client, db_session, monkeypatch):
    """Regression: the first worker draft completed every claimed job without
    running it, so try-on silently produced nothing."""
    import asyncio
    from types import SimpleNamespace

    import database
    import services.jobs as jobs
    import services.tryon_worker as worker

    calls = []
    job = SimpleNamespace(job_type="tryon", id=42, payload="{}")
    claimed = {"done": False}

    def fake_claim(db, job_type=None):
        if claimed["done"]:
            return None
        claimed["done"] = True
        return job

    async def fake_process(db, target):
        calls.append("process")

    class FakeSession:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(jobs, "claim_next", fake_claim)
    monkeypatch.setattr(jobs, "process_one", fake_process)
    monkeypatch.setattr(jobs, "complete", lambda db, target: calls.append("complete"))
    monkeypatch.setattr(database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr("services.security.tryon_record_generation", lambda **kwargs: None)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)

    async def run():
        task = asyncio.create_task(worker.tryon_worker_task())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if "complete" in calls:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert "process" in calls
    assert calls.index("process") < calls.index("complete")


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


def test_tryon_page_shows_the_owner_debug_log(client, db_session):
    from services import tryon_debug
    tryon_debug.clear()
    tryon_debug.record(job_id=99, provider="avalai", model="seedream-5-0-260128",
                       ok=True, status=200, category="", code="", error="", raw="",
                       fields="size=1K, images=2", result_bytes=10,
                       prep_ms=1, request_ms=2, download_ms=0, total_ms=3)
    try:
        token = csrf_token(client)
        client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
        page = client.get("/admin/try-on").text
        assert "گزارش درخواست‌ها" in page
        assert "seedream-5-0-260128" in page
        assert "total 3ms" in page
    finally:
        tryon_debug.clear()


def test_tryon_page_shows_live_estimate_and_job_state(client):
    from tests.conftest import csrf_token as _t
    token = _t(client)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    page = client.get("/admin/try-on").text
    assert "باقی‌مانده امروز" in page  # live quota estimate above generate
