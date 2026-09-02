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
_BACKUP_RE = re.compile(r"^referral_\d{8}_\d{6}\.db$")


def _db_path() -> Path | None:
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    return Path(DATABASE_URL[len("sqlite:///"):])


def create_backup() -> str | None:
    """Snapshot the live database into backups/. Returns the new file path or
    None on failure. Prunes old backups, keeping the newest KEEP_COUNT."""
    src = _db_path()
    if not src or not src.exists():
        logger.warning("Backup skipped: database file not found at %s", src)
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"referral_{ts}.db"
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
        _prune()
        logger.info("Backup created: %s", dest)
        return str(dest)
    except Exception as e:
        logger.error("Backup error: %s", e)
        return None


def _prune() -> None:
    files = sorted(BACKUP_DIR.glob("referral_*.db"))
    for old in files[:-KEEP_COUNT]:
        try:
            old.unlink()
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


def backup_download_path(name: str) -> Path | None:
    """Resolve a backup filename to a real path, guarding against traversal."""
    if not name or not _BACKUP_RE.match(name):
        return None
    path = (BACKUP_DIR / name).resolve()
    if not path.is_file() or not str(path).startswith(str(BACKUP_DIR.resolve())):
        return None
    return path
