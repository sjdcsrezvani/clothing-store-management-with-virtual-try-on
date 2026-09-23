"""Tests for the developer-controlled owner/demo deployment boundary."""
import os
from pathlib import Path

import pytest

from models import Settings
from tests.conftest import csrf_token


def test_owner_mode_hides_setup_wizard(client):
    """The default private owner build must never expose provisioning UI."""
    from deployment import OWNER_MODE
    assert OWNER_MODE is True
    response = client.get("/admin/setup")
    assert response.status_code == 404


def test_owner_mode_rejects_setup_post(client):
    """A direct POST cannot provision a new store in owner mode either."""
    response = client.post("/admin/setup", data={
        "store_name": "متجر غیرمجاز",
        "admin_password": "secret123",
        "admin_password_confirm": "secret123",
    })
    assert response.status_code in (403, 404)


def test_owner_profile_is_developer_controlled():
    """The deployment profile is source-controlled and setup is disabled."""
    import deployment
    assert deployment.OWNER_MODE is True
    assert deployment.SETUP_ROUTE_ENABLED is False
    assert deployment.BUILD_CHANNEL == "owner"


def test_first_run_env_generates_random_secrets(tmp_path):
    """The low-level generator creates stable-format secrets without exposing
    a default admin password."""
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


def test_provision_owner_copies_private_data(tmp_path):
    """Developer provisioning copies a valid DB/.env and marks the target
    without invoking any customer-facing wizard."""
    import sqlite3
    from provision_owner import provision
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
    assert (target / ".setup_done").read_text(encoding="utf-8") == "owner-provisioned\n"


def test_frozen_owner_first_run_seeds_embedded_bundle(monkeypatch, tmp_path):
    """A packaged owner build self-seeds a fresh data dir from the embedded
    owner bundle and opens the store — the customer never sees a setup page
    and never configures anything."""
    import desktop_entry

    fake_bundle = tmp_path / "bundle"
    (fake_bundle / "owner_bundle").mkdir(parents=True)
    (fake_bundle / "owner_bundle" / "referral.db").write_bytes(b"fake-sqlite-bytes")
    (fake_bundle / "owner_bundle" / ".env").write_text(
        "SESSION_SECRET=bundle-secret\nAPI_TOKEN=bundle-token\n", encoding="utf-8"
    )
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
    assert (data_dir / "referral.db").read_bytes() == b"fake-sqlite-bytes"
    assert (data_dir / ".env").is_file()
    # Bundled assets are symlinked fresh; templates likewise.
    assert (data_dir / "static" / "css" / "style.css").is_file()
    assert (data_dir / "templates").is_symlink()
    # Uploads become a persistent real copy in the data dir, not a link.
    assert not (data_dir / "static" / "uploads").is_symlink()
    assert (
        data_dir / "static" / "uploads" / "products" / "x.png"
    ).read_bytes() == b"png"
    assert os.environ["DATABASE_URL"].endswith("referral.db")
    # Existing environment secrets are preserved (dotenv loads with override=False).
    assert os.environ.get("SESSION_SECRET") == "preexisting"


def test_frozen_owner_without_data_refuses_instead_of_generating(monkeypatch, tmp_path):
    """Without provisioning (no embedded bundle, no data-dir contents), an
    owner build fails loudly for the developer instead of silently creating
    a blank store or offering the setup wizard."""
    import desktop_entry

    fake_bundle = tmp_path / "bundle"  # no owner_bundle inside
    fake_bundle.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(desktop_entry, "_is_frozen", lambda: True)
    monkeypatch.setattr(desktop_entry, "_bundle_dir", lambda: str(fake_bundle))
    monkeypatch.setattr(desktop_entry, "_user_data_dir", lambda: str(data_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="not provisioned"):
        desktop_entry._setup_frozen_env()
    # Nothing was generated: no blank .env, no blank database.
    assert not (data_dir / ".env").exists()
    assert not (data_dir / "referral.db").exists()
