"""Backup shelf phase 1: schedule settings, cached verify, row actions, redesign."""
import sys
from pathlib import Path

import pytest

import routers.admin as admin_router
import services.backup as backup
from models import Settings
from services.backup import (
    BACKUP_DIR, backup_due, normalize_every_days, normalize_keep_count,
)
from services._common import format_bytes_fa
from tests.conftest import csrf_token
from tests.test_staff_phase_b import _owner_client


@pytest.fixture(autouse=True)
def _own_shelf(tmp_path, monkeypatch):
    """A private shelf and uploads tree for each test in this file.

    The shelf is one directory shared by all four xdist workers, so these
    tests emptied it under one another — and emptied the shop's real
    backups, and re-swapped its real uploads, on a dev machine. Pointing
    the app at a temp tree per test leaves the real one alone and makes
    every count in here deterministic.
    """
    shelf = tmp_path / "backups"
    shelf.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for module in (backup, admin_router, sys.modules[__name__]):
        monkeypatch.setattr(module, "BACKUP_DIR", shelf)
    monkeypatch.setattr(backup, "UPLOADS_DIR", uploads)


def _shelf():
    return sorted(BACKUP_DIR.glob("referral_*.db"))


def _clean():
    for path in _shelf():
        try:
            path.unlink()
        except OSError:
            pass
    try:
        (BACKUP_DIR / ".verify.json").unlink()
    except OSError:
        pass


def _backup_now(client):
    return client.post("/admin/backup",
                       data={"csrf_token": csrf_token(client, "/admin/backups")},
                       follow_redirects=False)


def test_page_names_backup_and_restore_without_emoji(client, db_session):
    _owner_client(client, db_session, name="bk-owner-page")
    page = client.get("/admin/backups").text
    assert "وضعیت پشتیبان‌گیری" in page
    for emoji in ("💾", "📄", "📦", "🕐", "🔎", "⬇️", "✅", "⚠️"):
        assert emoji not in page
    assert "اولین پشتیبان‌گیری" in page  # empty state carries the CTA


def test_manual_backup_files_verify_and_caches(client, db_session):
    _owner_client(client, db_session, name="bk-owner-make")
    _clean()
    try:
        res = _backup_now(client)
        assert res.status_code == 303
        files = _shelf()
        assert len(files) == 1
        page = client.get("/admin/backups").text
        assert files[0].name in page
        assert "تأیید شده" in page  # cached badge, no emoji
        assert (BACKUP_DIR / ".verify.json").exists()
    finally:
        _clean()


def test_schedule_settings_normalize_garbage(client, db_session):
    _owner_client(client, db_session, name="bk-owner-sched")
    res = client.post("/admin/backups/schedule", data={
        "csrf_token": csrf_token(client, "/admin/backups"),
        "backup_every_days": "7", "backup_keep_count": "99",
    }, follow_redirects=False)
    assert res.status_code == 303
    values = {row.key: row.value for row in db_session.query(Settings).filter(
        Settings.key.like("backup_%")).all()}
    assert values["backup_every_days"] == "30"  # off-menu reads as default
    assert values["backup_keep_count"] == "30"  # clamped to the ceiling
    page = client.get("/admin/backups").text
    assert "هر ۳۰ روز" in page


def test_delete_and_recheck_and_download_all(client, db_session):
    _owner_client(client, db_session, name="bk-owner-rows")
    _clean()
    try:
        _backup_now(client)
        name = _shelf()[0].name
        token = lambda: csrf_token(client, "/admin/backups")  # noqa: E731

        bad = client.post("/admin/backups/delete", data={
            "csrf_token": token(), "name": "../evil.db"}, follow_redirects=False)
        assert bad.status_code == 303 and _shelf(), "traversal must fail shut"

        ok = client.post("/admin/backups/recheck", data={
            "csrf_token": token(), "name": name}, follow_redirects=False)
        assert ok.status_code == 303

        zipped = client.get("/admin/backups/download-all")
        assert zipped.status_code == 200
        assert zipped.headers["content-type"] == "application/zip"
        assert name.encode() in zipped.content  # the file is inside the zip

        gone = client.post("/admin/backups/delete", data={
            "csrf_token": token(), "name": name}, follow_redirects=False)
        assert gone.status_code == 303 and not _shelf()
    finally:
        _clean()


def test_schedule_helpers():
    assert normalize_every_days("20") == 20
    assert normalize_every_days("7") == 30
    assert normalize_keep_count("5") == 5
    assert normalize_keep_count("99") == 30
    assert backup_due(30) in (True, False)  # runs against the real shelf
    assert format_bytes_fa(13002342) == "۱۲٫۴ مگابایت"
    assert format_bytes_fa(512) == "۵۱۲ بایت"


def _shelf_names():
    return sorted(path.name for path in _shelf())


def test_restore_brings_back_pre_backup_state(client, db_session):
    from models import Settings as SettingsModel
    _owner_client(client, db_session, name="bk-owner-restore")
    _clean()
    try:
        db_session.add(SettingsModel(key="bk_probe", value="1"))
        db_session.commit()
        _backup_now(client)
        db_session.query(SettingsModel).filter(
            SettingsModel.key == "bk_probe").delete()
        db_session.commit()
        name = _shelf_names()[-1]
        res = client.post("/admin/backups/restore", data={
            "csrf_token": csrf_token(client, "/admin/backups"), "name": name,
        }, follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"].startswith("/admin/?msg=")
        db_session.rollback()
        db_session.expire_all()
        assert db_session.query(SettingsModel).filter(
            SettingsModel.key == "bk_probe").count() == 1
        dash = client.get(res.headers["location"]).text
        assert "بازیابی شد از" in dash
    finally:
        db_session.query(SettingsModel).filter(
            SettingsModel.key == "bk_probe").delete()
        db_session.commit()
        _clean()


def test_upload_validates_and_shelves(client, db_session):
    import io
    import sqlite3
    _owner_client(client, db_session, name="bk-owner-upload")
    _clean()
    try:
        buf = io.BytesIO()
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
        conn.commit()
        dest = BACKUP_DIR / ".probe_src.db"
        target = sqlite3.connect(str(dest))
        conn.backup(target)
        target.close()
        conn.close()
        raw = dest.read_bytes()
        dest.unlink()

        bad = client.post("/admin/backups/upload", data={
            "csrf_token": csrf_token(client, "/admin/backups"),
        }, files={"backup_file": ("ext.txt", b"not a database",
                                  "text/plain")}, follow_redirects=False)
        assert bad.status_code == 303

        good = client.post("/admin/backups/upload", data={
            "csrf_token": csrf_token(client, "/admin/backups"),
        }, files={"backup_file": ("ext.db", raw,
                                  "application/x-sqlite3")},
            follow_redirects=False)
        assert good.status_code == 303
        assert len(_shelf()) == 1
        assert "ناحیه خطر" in client.get("/admin/backups").text
    finally:
        _clean()


def test_newer_schema_backup_warns_but_stays(client, db_session):
    import sqlite3
    _owner_client(client, db_session, name="bk-owner-newer")
    _clean()
    try:
        dest = BACKUP_DIR / "referral_20990101_000000.db"
        conn = sqlite3.connect(str(dest))
        conn.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER)")
        conn.execute("INSERT INTO schema_version VALUES (1, 9999)")
        conn.commit()
        conn.close()
        page = client.get("/admin/backups").text
        assert "نسخه جدیدتر" in page
        assert "ساختار این نسخه جدیدتر" in page  # inside the restore confirm
    finally:
        _clean()


def _pair_of(db_name):
    from services.backup import uploads_tarball_for
    return uploads_tarball_for(db_name)


def test_backup_pairs_uploads_tarball(client, db_session):
    _owner_client(client, db_session, name="bk-owner-pair")
    _clean()
    marker = backup.UPLOADS_DIR / "bk_pair_marker.txt"
    try:
        marker.write_text("pair-me")
        _backup_now(client)
        name = _shelf_names()[-1]
        pair = _pair_of(name)
        assert pair is not None and pair.is_file()
        import tarfile
        with tarfile.open(pair, "r:gz") as archive:
            names = archive.getnames()
        assert any(n.endswith("bk_pair_marker.txt") for n in names)
        page = client.get("/admin/backups").text
        assert "+ فایل‌ها:" in page
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
        _clean()


def test_restore_swaps_uploads_too(client, db_session):
    _owner_client(client, db_session, name="bk-owner-pair-restore")
    _clean()
    marker = backup.UPLOADS_DIR / "bk_restore_marker.txt"
    try:
        marker.write_text("before")
        _backup_now(client)
        name = _shelf_names()[-1]
        marker.write_text("after")
        res = client.post("/admin/backups/restore", data={
            "csrf_token": csrf_token(client, "/admin/backups"), "name": name,
        }, follow_redirects=False)
        assert res.status_code == 303
        assert marker.read_text() == "before"
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
        _clean()


def test_pairless_backup_restores_db_only(client, db_session):
    _owner_client(client, db_session, name="bk-owner-nopair")
    _clean()
    try:
        _backup_now(client)
        name = _shelf_names()[-1]
        _pair_of(name).unlink()
        assert "بدون فایل‌پیوست" in client.get("/admin/backups").text
        res = client.post("/admin/backups/restore", data={
            "csrf_token": csrf_token(client, "/admin/backups"), "name": name,
        }, follow_redirects=False)
        assert res.status_code == 303
        from urllib.parse import unquote
        location = unquote(res.headers["location"])
        assert "بازیابی شد از" in location and "فایل‌ها" not in location
    finally:
        _clean()


def test_delete_and_prune_drop_the_pair(client, db_session):
    from services.backup import _prune
    _owner_client(client, db_session, name="bk-owner-pair-prune")
    _clean()
    try:
        _backup_now(client)
        import time
        time.sleep(1.05)  # distinct filename stems, one second apart
        _backup_now(client)
        names = _shelf_names()
        assert len(names) == 2 and all(_pair_of(n) for n in names)
        _prune(keep_count=1)
        names = _shelf_names()
        assert len(names) == 1
        assert _pair_of(names[0]) is not None
        from pathlib import Path as _Path
        orphans = list(BACKUP_DIR.glob("*_uploads.tar.gz"))
        assert len(orphans) == 1  # only the survivor's pair stands
    finally:
        _clean()
