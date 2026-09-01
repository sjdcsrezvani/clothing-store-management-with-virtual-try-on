"""Versioned database migrations for the local store database."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

MIGRATION_VERSION = 2


def migration_status(engine) -> int:
    inspector = inspect(engine)
    if not inspector.has_table("schema_version"):
        return 0
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).scalar()
        return int(row or 0)


def backup_database(database_path: str | Path) -> Path:
    source = Path(database_path)
    if not source.exists():
        raise FileNotFoundError(source)
    target = source.with_name(
        f"{source.name}.before-migration-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(source, target)
    return target


def _ensure_version_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)"))


def upgrade(engine, target: int = MIGRATION_VERSION) -> int:
    if target < 0 or target > MIGRATION_VERSION:
        raise ValueError(f"Unsupported migration target: {target}")
    _ensure_version_table(engine)
    current = migration_status(engine)
    if current > target:
        raise RuntimeError(f"Database version {current} is newer than requested version {target}")
    if current < 1 <= target:
        with engine.begin() as conn:
            conn.execute(text("UPDATE schema_version SET version=1 WHERE id=1"))
    # Version 2: checkout concurrency tables (CheckoutSession, StockReservation,
    # CheckoutEvent). The tables are created by Base.metadata.create_all on
    # startup; the version row marks the schema as current so a fresh install
    # and an upgraded install both land here.
    if current < 2 <= target:
        with engine.begin() as conn:
            conn.execute(text("UPDATE schema_version SET version=2 WHERE id=1"))
    return migration_status(engine)


def downgrade(engine, target: int = 0) -> int:
    if target != 0:
        raise ValueError("Only downgrade to version 0 is supported")
    _ensure_version_table(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE schema_version SET version=0 WHERE id=1"))
    return 0
