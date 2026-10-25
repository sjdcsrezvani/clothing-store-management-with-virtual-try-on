"""Tests for the first-run setup wizard and the desktop data directory."""
import os
from pathlib import Path
from types import SimpleNamespace

from models import Settings
from tests.conftest import csrf_token


def test_setup_wizard_is_available_on_a_fresh_install(client):
    response = client.get("/admin/setup")
    assert response.status_code == 200
    assert "خوش آمدید" in response.text


def test_setup_is_local_only():
    from routers import admin

    remote = SimpleNamespace(client=SimpleNamespace(host="192.168.1.50"))
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert admin._setup_local_request(remote) is False
    assert admin._setup_local_request(local) is True


def test_remote_client_cannot_open_setup(client, monkeypatch):
    from routers import admin
    monkeypatch.setattr(admin, "_setup_local_request", lambda request: False)
    assert client.get("/admin/setup").status_code == 404


def test_setup_creates_the_owner_and_writes_the_marker(client, db_session, monkeypatch, tmp_path):
    from services.security import verify_password

    marker = tmp_path / ".setup_done"
    from routers import admin as admin_router
    monkeypatch.setattr(admin_router, "_setup_marker_path", lambda: str(marker))

    token = csrf_token(client, "/admin/setup")
    response = client.post("/admin/setup", data={
        "csrf_token": token,
        "store_name": "فروشگاه تازه",
        "store_tagline": "پوشاک",
        "store_instagram": "@fresh_store",
        "admin_password": "freshpass123",
        "admin_password_confirm": "freshpass123",
    }, follow_redirects=False)

    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]
    row = db_session.query(Settings).filter(Settings.key == "admin_password_hash").first()
    assert row and verify_password("freshpass123", row.value)
    assert marker.read_text(encoding="utf-8") == "1"
    branding = {
        setting.key: setting.value
        for setting in db_session.query(Settings).filter(
            Settings.key.in_(["store_name", "store_tagline", "store_instagram"])).all()
    }
    assert branding == {
        "store_name": "فروشگاه تازه",
        "store_tagline": "پوشاک",
        "store_instagram": "@fresh_store",
    }

    # Once provisioned the wizard closes and sends the visitor to login.
    again = client.get("/admin/setup", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/admin/login"


def test_setup_rejects_a_short_or_mismatched_password(client):
    token = csrf_token(client, "/admin/setup")
    response = client.post("/admin/setup", data={
        "csrf_token": token,
        "store_name": "فروشگاه",
        "admin_password": "short",
        "admin_password_confirm": "different",
    })
    assert response.status_code == 200
    assert "حداقل ۶ کاراکتر" in response.text
    assert "یکسان نیستند" in response.text


def test_first_run_env_generates_random_secrets(tmp_path):
    import desktop_entry
    path = tmp_path / ".env"
    desktop_entry._generate_first_run_env(str(path))
    content = path.read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1) for line in content.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    assert len(values["SESSION_SECRET"]) == 64
    assert len(values["API_TOKEN"]) == 32
    assert values["ADMIN_PASSWORD"] == ""


def test_provision_data_copies_an_existing_store(tmp_path):
    import sqlite3
    from provision_data import provision

    source_db = tmp_path / "source.db"
    source_env = tmp_path / "source.env"
    target = tmp_path / "target"
    connection = sqlite3.connect(source_db)
    connection.execute("CREATE TABLE check_data (value TEXT)")
    connection.execute("INSERT INTO check_data VALUES ('private')")
    connection.commit()
    connection.close()
    source_env.write_text("SESSION_SECRET=private\n", encoding="utf-8")

    provision(source_db, source_env, target)
    check = sqlite3.connect(target / "referral.db")
    assert check.execute("SELECT value FROM check_data").fetchone()[0] == "private"
    check.close()
    assert (target / ".env").read_text(encoding="utf-8") == "SESSION_SECRET=private\n"
    assert (target / ".setup_done").read_text(encoding="utf-8") == "provisioned\n"


def test_frozen_setup_creates_a_fresh_data_dir(monkeypatch, tmp_path):
    import desktop_entry

    fake_bundle = tmp_path / "bundle"
    (fake_bundle / "static" / "css").mkdir(parents=True)
    (fake_bundle / "static" / "css" / "style.css").write_text("body{}", encoding="utf-8")
    (fake_bundle / "static" / "uploads" / "products").mkdir(parents=True)
    (fake_bundle / "static" / "uploads" / "products" / "x.png").write_bytes(b"png")
    (fake_bundle / "templates").mkdir(parents=True)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(desktop_entry, "_is_frozen", lambda: True)
    monkeypatch.setattr(desktop_entry, "_bundle_dir", lambda: str(fake_bundle))
    monkeypatch.setattr(desktop_entry, "_user_data_dir", lambda: str(data_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "preexisting")
    monkeypatch.chdir(tmp_path)

    result = desktop_entry._setup_frozen_env()

    assert result == str(data_dir)
    assert (data_dir / ".env").is_file()
    # Bundled assets are copied fresh; templates likewise.
    assert (data_dir / "static" / "css" / "style.css").is_file()
    assert (data_dir / "templates").is_symlink()
    # Uploads become a persistent real copy in the data dir, not a link.
    assert not (data_dir / "static" / "uploads").is_symlink()
    assert (
        data_dir / "static" / "uploads" / "products" / "x.png"
    ).read_bytes() == b"png"
    assert os.environ["DATABASE_URL"].endswith(str(data_dir / "referral.db"))
    # Existing environment secrets are preserved (dotenv loads with override=False).
    assert os.environ.get("SESSION_SECRET") == "preexisting"


def test_initial_url_opens_setup_until_marker_exists(tmp_path):
    import desktop_entry
    host = "http://127.0.0.1:8100"

    assert desktop_entry._initial_url(host, str(tmp_path)) == f"{host}/admin/setup"
    (tmp_path / ".setup_done").write_text("1", encoding="utf-8")
    assert desktop_entry._initial_url(host, str(tmp_path)) == f"{host}/sales/new"


def test_data_dir_uses_the_app_name_and_accepts_an_override(monkeypatch, tmp_path):
    import desktop_entry
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STORE_DATA_DIR", raising=False)

    assert desktop_entry._user_data_dir().endswith("StoreManagement")

    custom = tmp_path / "custom-data"
    monkeypatch.setenv("STORE_DATA_DIR", str(custom))
    assert desktop_entry._user_data_dir() == str(custom)


def test_empty_admin_password_is_allowed_before_setup(monkeypatch, tmp_path):
    """A fresh install has no password in the environment yet."""
    import services.operations as ops

    static = tmp_path / "static"
    (static / "uploads").mkdir(parents=True)
    monkeypatch.setattr(ops, "API_TOKEN", "generated-token")
    monkeypatch.setattr(ops, "SESSION_SECRET", "generated-secret")
    monkeypatch.setattr(ops, "DATABASE_URL", f"sqlite:///{tmp_path / 'referral.db'}")

    assert ops.validate_production_config(static_root=static) == []

    monkeypatch.setattr(ops, "API_TOKEN", "")
    assert "API_TOKEN is missing or uses a default value" in ops.validate_production_config(static_root=static)
