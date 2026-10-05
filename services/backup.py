"""SQLite backup/restore support.

`VACUUM INTO` produces a consistent snapshot even while the app is running
(WAL journal is folded in), which is what we want for a shop's customer data.
Backups land in `backups/` and are pruned to the newest KEEP_COUNT files.
"""
import logging
import hashlib
import sqlite3
import re
from datetime import datetime
from pathlib import Path

from config import DATABASE_URL
from migrations import migration_status
from services.operations import verify_sqlite_backup

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
KEEP_COUNT = 30
_BACKUP_RE = re.compile(r"^referral_\d{8}_\d{6}(?:_\d+)?\.db$")

# Every database backup pairs with an uploads tarball of the same stem:
# referral_20240101_020000.db <-> referral_20240101_020000_uploads.tar.gz
# A backup without its tarball (older rows, bare uploads) restores DB-only.
UPLOADS_DIR = Path("static/uploads")
_UPLOADS_TARBALL_SUFFIX = "_uploads.tar.gz"

# The owner picks the cadence, not the clock: automatic backups run every
# N days (at the 2 AM UTC pass), keeping the newest K files.
BACKUP_EVERY_OPTIONS = (10, 20, 30)
BACKUP_EVERY_DEFAULT = 30
BACKUP_KEEP_DEFAULT = 10
BACKUP_KEEP_MIN = 3
BACKUP_KEEP_MAX = 30
_VERIFY_CACHE_NAME = ".verify.json"


def _db_path() -> Path | None:
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    return Path(DATABASE_URL[len("sqlite:///"):])


def create_backup(keep_count: int | None = None) -> str | None:
    """Snapshot the live database into backups/. Returns the new file path or
    None on failure. Prunes old backups, keeping the newest keep_count."""
    src = _db_path()
    if not src or not src.exists():
        logger.warning("Backup skipped: database file not found at %s", src)
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"referral_{ts}.db"
        # Two backups inside one second (a manual run plus its snapshot)
        # must not share a name — suffix until the shelf has room.
        n = 0
        while dest.exists():
            n += 1
            dest = BACKUP_DIR / f"referral_{ts}_{n}.db"
        import sqlite3
        con = sqlite3.connect(str(src))
        try:
            con.execute("VACUUM INTO ?", (str(dest),))
        finally:
            con.close()
        if not dest.exists():
            logger.error("Backup failed: VACUUM INTO produced no file")
            return None
        with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as check:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            logger.error("Backup integrity check failed")
            return None
        metadata = verify_sqlite_backup(dest)
        logger.info("Backup verified: size=%s checksum=%s", metadata["size"], metadata["checksum"])
        _remember_verified(dest, metadata)
        pair = archive_uploads(dest)
        if pair is None:
            logger.warning("Backup %s has no uploads tarball", dest.name)
        _prune(keep_count)
        logger.info("Backup created: %s", dest)
        return str(dest)
    except Exception as e:
        logger.error("Backup error: %s", e)
        return None


def _prune(keep_count: int | None = None) -> None:
    keep = max(int(keep_count or KEEP_COUNT), 1)
    files = sorted(BACKUP_DIR.glob("referral_*.db"))
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
        pair = uploads_tarball_for(old.name)
        if pair is not None:
            try:
                pair.unlink()
            except OSError:
                pass
    # Orphaned tarballs (their database row is gone) own nothing — sweep them.
    live_stems = {path.stem for path in BACKUP_DIR.glob("referral_*.db")}
    for tarball in BACKUP_DIR.glob(f"referral_*{_UPLOADS_TARBALL_SUFFIX}"):
        stem = tarball.name[: -len(_UPLOADS_TARBALL_SUFFIX)]
        if stem not in live_stems:
            try:
                tarball.unlink()
            except OSError:
                pass


def list_backups() -> list[dict]:
    """Backup files sorted newest-first, with size + mtime for the admin page."""
    items = []
    for f in BACKUP_DIR.glob("referral_*.db"):
        if not _BACKUP_RE.match(f.name):
            continue
        stat = f.stat()
        metadata = verify_sqlite_backup(f)
        items.append({
            "name": f.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime),
            **metadata,
        })
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def latest_backup() -> datetime | None:
    """The newest backup's timestamp, without opening any file.

    ``list_backups`` verifies every backup it lists, which is the right thing for
    the backups page and far too much work for a number on a dashboard. This
    only stats the newest file, so it is cheap enough to ask on every page load.
    Returns ``None`` when the shop has never been backed up — a different fact
    from “the last one is old”, and the dashboard says so.
    """
    newest: datetime | None = None
    for path in BACKUP_DIR.glob("referral_*.db"):
        if not _BACKUP_RE.match(path.name):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def backup_download_path(name: str) -> Path | None:
    """Resolve a backup filename to a real path, guarding against traversal."""
    if not name or not _BACKUP_RE.match(name):
        return None
    path = (BACKUP_DIR / name).resolve()
    if not path.is_file() or not str(path).startswith(str(BACKUP_DIR.resolve())):
        return None
    return path


def normalize_every_days(value) -> int:
    """Clamp a cadence to an offered option — garbage reads as the default."""
    try:
        days = int(value)
    except (ValueError, TypeError):
        return BACKUP_EVERY_DEFAULT
    return days if days in BACKUP_EVERY_OPTIONS else BACKUP_EVERY_DEFAULT


def normalize_keep_count(value) -> int:
    """Clamp retention into its bounds — garbage reads as the default."""
    try:
        keep = int(value)
    except (ValueError, TypeError):
        return BACKUP_KEEP_DEFAULT
    return min(max(keep, BACKUP_KEEP_MIN), BACKUP_KEEP_MAX)


def newest_backup_mtime() -> float | None:
    """Epoch seconds of the newest backup file, or None when there are none."""
    newest = None
    for path in BACKUP_DIR.glob("referral_*.db"):
        if not _BACKUP_RE.match(path.name):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def backup_due(every_days: int) -> bool:
    """True when no backup exists or the newest is older than the cadence.

    A manual backup resets the clock too — it is a fresh copy either way.
    """
    import time
    newest = newest_backup_mtime()
    if newest is None:
        return True
    return time.time() - newest >= every_days * 86400


def _verify_cache_path() -> Path:
    return BACKUP_DIR / _VERIFY_CACHE_NAME


def _verify_cache_load() -> dict:
    import json
    try:
        return json.loads(_verify_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _verify_cache_save(cache: dict) -> None:
    import json
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        _verify_cache_path().write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def _remember_verified(path: Path, metadata: dict) -> None:
    """File one verification result, keyed by name + size + mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    cache = _verify_cache_load()
    cache[path.name] = {
        "verified": metadata.get("verified", False),
        "integrity": metadata.get("integrity"),
        "checksum": metadata.get("checksum"),
        # Underscored: match keys only, never row data — list_backups owns
        # the row's size and mtime, and a float mtime would break its dates.
        "_size": metadata.get("size", 0),
        "_mtime": mtime,
    }
    _verify_cache_save(cache)


def verify_cached(path: str | Path) -> dict:
    """verify_sqlite_backup, but a matching cache entry spares the re-read.

    The entry keys on size + mtime, so a replaced file never reads stale.
    """
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return verify_sqlite_backup(target)
    entry = _verify_cache_load().get(target.name)
    if (entry and entry.get("_size") == stat.st_size
            and entry.get("_mtime") == stat.st_mtime):
        return {
            "path": str(target),
            "verified": entry.get("verified", False),
            "integrity": entry.get("integrity"),
            "checksum": entry.get("checksum"),
        }
    fresh = verify_sqlite_backup(target)
    _remember_verified(target, fresh)
    return fresh


def forget_verified(name: str) -> None:
    """Drop one cache entry — after a delete or a forced re-check."""
    cache = _verify_cache_load()
    if name in cache:
        del cache[name]
        _verify_cache_save(cache)


def delete_backup(name: str) -> bool:
    """Delete one backup file by name, with its uploads tarball if paired.

    False when the name is not a backup.
    """
    path = backup_download_path(name)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    pair = uploads_tarball_for(name)
    if pair is not None:
        try:
            pair.unlink()
        except OSError:
            pass
    forget_verified(name)
    return True


def backups_storage() -> tuple[int, int]:
    """(file count, total bytes) of the backup shelf, tarballs included."""
    count, total = 0, 0
    for path in BACKUP_DIR.glob("referral_*.db"):
        if not _BACKUP_RE.match(path.name):
            continue
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            continue
    for path in BACKUP_DIR.glob(f"referral_*{_UPLOADS_TARBALL_SUFFIX}"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return count, total


def download_all_bytes() -> tuple[bytes, str]:
    """Every backup file as one zip, newest first. Empty shelf → empty zip."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(BACKUP_DIR.glob("referral_*.db"), reverse=True):
            if _BACKUP_RE.match(path.name):
                archive.write(path, arcname=path.name)
        for path in sorted(
                BACKUP_DIR.glob(f"referral_*{_UPLOADS_TARBALL_SUFFIX}"), reverse=True):
            archive.write(path, arcname=path.name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return buf.getvalue(), f"backups_{stamp}.zip"


def recheck_backup(name: str) -> dict | None:
    """Force a fresh verification of one file, refreshing its cache entry.

    None when the name is not a backup file.
    """
    path = backup_download_path(name)
    if path is None:
        return None
    fresh = verify_sqlite_backup(path)
    _remember_verified(path, fresh)
    return fresh


def uploads_tarball_for(db_name: str) -> Path | None:
    """The uploads tarball paired with a database backup, if it is on disk."""
    if not _BACKUP_RE.match(db_name):
        return None
    stem = db_name[: -len(".db")]
    candidate = BACKUP_DIR / f"{stem}{_UPLOADS_TARBALL_SUFFIX}"
    return candidate if candidate.is_file() else None


def archive_uploads(db_dest: str | Path) -> Path | None:
    """Tarball static/uploads/ next to a database backup. None when there is
    nothing to archive — the backup stands alone and restores DB-only."""
    db_dest = Path(db_dest)
    if not UPLOADS_DIR.is_dir():
        return None
    dest = BACKUP_DIR / f"{db_dest.stem}{_UPLOADS_TARBALL_SUFFIX}"
    try:
        import tarfile
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as archive:
            archive.add(UPLOADS_DIR, arcname="uploads")
        return dest
    except OSError as error:
        logger.warning("Uploads archive failed: %s", error)
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def restore_uploads_from(tarball: str | Path) -> tuple[bool, str]:
    """Replace static/uploads/ with a paired tarball's content.

    The current tree is only touched after the whole archive reads clean,
    and the pre-restore snapshot already holds its tarball — so a failed
    extract keeps the old files, never half of each.
    """
    import shutil
    import tarfile
    try:
        with tarfile.open(tarball, "r:gz") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError) as error:
        logger.error("Uploads tarball unreadable: %s", error)
        return False, "بایگانی فایل‌ها خراب است."
    try:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        for child in UPLOADS_DIR.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        # The tarball's root is "uploads" (see archive_uploads), so it lands
        # back exactly where it came from: static/uploads/ itself.
        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(UPLOADS_DIR.parent, filter="data")
    except (tarfile.TarError, OSError) as error:
        logger.error("Uploads restore failed: %s", error)
        return False, "بازیابی فایل‌ها ناموفق بود."
    if not UPLOADS_DIR.is_dir():
        return False, "بایگانی فایل‌ها ساختار نامشخصی دارد."
    return True, "ok"


def backup_schema_version(path: str | Path) -> int | None:
    """The migration version stamped inside a backup file, if any.

    Read-only and dependency-free: the stamp lives in the file itself, so
    the shelf can warn about newer-schema files without opening them.
    """
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "schema_version" not in tables:
                return None
            row = connection.execute(
                "SELECT version FROM schema_version WHERE id=1").fetchone()
            return int(row[0]) if row and row[0] is not None else None
    except (sqlite3.Error, ValueError, TypeError, OSError):
        return None


_RESTORE_LOCK = None


def _restore_lock():
    import threading
    global _RESTORE_LOCK
    if _RESTORE_LOCK is None:
        _RESTORE_LOCK = threading.Lock()
    return _RESTORE_LOCK


def _quiesce_live(live: Path) -> None:
    """Drain the live database to a state a file copy can replace.

    Checkpoints WAL back into the main file, drops the pooled connections,
    then removes the (now empty) sidecar files — with no open handles a
    leftover -wal would resurrect stale pages over the restored content.
    """
    from database import engine
    staging = sqlite3.connect(str(live), timeout=30)
    try:
        staging.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        staging.commit()
    finally:
        staging.close()
    engine.dispose()
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            (live.parent / (live.name + suffix)).unlink(missing_ok=True)
        except OSError:
            pass


def restore_live_from(backup_path: str | Path, *, make_snapshot: bool = True,
                      keep_count: int | None = None) -> tuple[bool, str]:
    """Replace the live database content with a backup file — no restart.

    Validates first, snapshots the present next (unless asked not to), then
    copies page-by-page through SQLite's own online-backup API, migrates an
    older schema forward in place, swaps the paired uploads tarball when one
    rides along, and re-verifies. Returns (ok, message).
    """
    from database import engine
    target = Path(backup_path)
    if not target.is_file():
        return False, "فایل یافت نشد."
    live = _db_path()
    if live is None or not live.exists():
        return False, "دیتابیس فعلی یافت نشد."
    try:
        if target.resolve() == live.resolve():
            return False, "این همان دیتابیس فعلی است."
    except OSError:
        return False, "فایل یافت نشد."
    lock = _restore_lock()
    if lock.locked():
        return False, "عملیات دیگری در جریان است؛ کمی بعد دوباره تلاش کنید."
    with lock:
        version = backup_schema_version(target)
        if make_snapshot and not create_backup(keep_count):
            return False, "نسخه امن ساخته نشد؛ بازیابی لغو شد."
        try:
            _quiesce_live(live)
            source = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=60)
            try:
                dest = sqlite3.connect(str(live), timeout=60)
                try:
                    source.backup(dest)
                finally:
                    dest.close()
            finally:
                source.close()
        except sqlite3.Error as error:
            logger.error("Restore copy failed: %s", error)
            return False, "کپی اطلاعات ناموفق بود؛ چیزی عوض نشد."
        try:
            from migrations import MIGRATION_VERSION, upgrade
            if version is None or version <= MIGRATION_VERSION:
                upgrade(engine)
        except Exception as error:  # noqa: BLE001 — schema state must surface
            logger.error("Post-restore migrate failed: %s", error)
            return False, "به‌روزرسانی ساختار ناموفق بود."
        if not verify_sqlite_backup(live).get("verified"):
            return False, "راستی‌آزمایی پس از بازیابی ناموفق بود."
        message = f"بازیابی شد از {target.name}."
        pair = uploads_tarball_for(target.name)
        if pair is not None:
            files_ok, files_message = restore_uploads_from(pair)
            if not files_ok:
                return False, files_message + " دیتابیس برگشت، فایل‌ها نه."
            message += " فایل‌ها هم برگشت."
        try:
            from services.store import invalidate_store_cache
            invalidate_store_cache()
        except Exception:  # noqa: BLE001 — caches rebuild themselves
            pass
        return True, message
