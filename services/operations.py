from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import ADMIN_PASSWORD, API_TOKEN, DATABASE_URL, SESSION_SECRET
from deployment import OWNER_MODE


def redact_identifier(value: str | None) -> str:
    if not value:
        return "unknown"
    value = str(value)
    return "*" * max(0, len(value) - 4) + value[-4:]


def validate_production_config(static_root: str | Path = "static") -> list[str]:
    errors = []
    if not ADMIN_PASSWORD:
        errors.append("ADMIN_PASSWORD is missing")
    if not API_TOKEN or API_TOKEN in {"change-me", "change-me-please"}:
        errors.append("API_TOKEN is missing or uses a default value")
    if not SESSION_SECRET or SESSION_SECRET in {"change-me", "change-me-please"}:
        errors.append("SESSION_SECRET is missing or uses a default value")
    if not DATABASE_URL.startswith("sqlite:///"):
        return errors
    path = Path(DATABASE_URL[10:]).expanduser()
    if path.parent.exists() and not path.parent.is_dir():
        errors.append("database parent path is not a directory")
    elif path.parent.exists() and not path.parent.stat().st_mode & 0o200:
        errors.append("database path is not writable")
    root = Path(static_root)
    for directory in (root, root / "uploads"):
        if not directory.is_dir():
            errors.append(f"required static directory is missing: {directory}")
    return errors


def verify_sqlite_backup(path: str | Path) -> dict:
    target = Path(path)
    result = {"path": str(target), "verified": False, "integrity": None, "checksum": None, "size": 0, "verified_at": datetime.now(timezone.utc).isoformat()}
    if not target.is_file():
        result["error"] = "backup file does not exist"
        return result
    result["size"] = target.stat().st_size
    result["checksum"] = hashlib.sha256(target.read_bytes()).hexdigest()
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        result["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
        result["verified"] = result["integrity"] == "ok"
    return result
