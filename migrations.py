"""Versioned database migrations for the local store database."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from models import BUSINESS_EVENT_TYPES

MIGRATION_VERSION = 9


_BUSINESS_EVENT_TYPES_SQL = ", ".join(repr(event_type) for event_type in BUSINESS_EVENT_TYPES)


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


def _business_events_table_sql(table_name: str) -> str:
    return f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY,
            event_type VARCHAR(60) NOT NULL,
            aggregate_type VARCHAR(50) NOT NULL,
            aggregate_id INTEGER,
            idempotency_key VARCHAR(200) NOT NULL UNIQUE,
            actor_user_id INTEGER,
            request_id VARCHAR(100),
            payload TEXT NOT NULL DEFAULT '{{}}',
            schema_version INTEGER NOT NULL DEFAULT 1,
            occurred_at DATETIME NOT NULL,
            CONSTRAINT ck_business_events_type CHECK (event_type IN ({_BUSINESS_EVENT_TYPES_SQL})),
            CONSTRAINT ck_business_events_aggregate_id CHECK (aggregate_id IS NULL OR aggregate_id > 0),
            CONSTRAINT ck_business_events_schema_version CHECK (schema_version > 0),
            FOREIGN KEY(actor_user_id) REFERENCES staff_users(id)
        )
    """


def _create_business_event_indexes(conn) -> None:
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_event_type ON business_events (event_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_aggregate_type ON business_events (aggregate_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_aggregate_id ON business_events (aggregate_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_actor_user_id ON business_events (actor_user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_request_id ON business_events (request_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_occurred_at ON business_events (occurred_at)"))


def _apply_revision(conn, version: int) -> None:
    if version == 8:
        conn.execute(text(_business_events_table_sql("business_events").replace(
            "CREATE TABLE business_events", "CREATE TABLE IF NOT EXISTS business_events", 1
        )))
        _create_business_event_indexes(conn)
        return

    if version != 9:
        return

    table_exists = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
    )).scalar()
    if not table_exists:
        conn.execute(text(_business_events_table_sql("business_events")))
        _create_business_event_indexes(conn)
        return

    for index_name in (
        "ix_business_events_event_type",
        "ix_business_events_aggregate_type",
        "ix_business_events_aggregate_id",
        "ix_business_events_actor_user_id",
        "ix_business_events_request_id",
        "ix_business_events_occurred_at",
    ):
        conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))

    conn.execute(text(_business_events_table_sql("business_events_v9")))
    conn.execute(text("""
        INSERT INTO business_events_v9 (
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        )
        SELECT
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        FROM business_events
    """))
    conn.execute(text("DROP TABLE business_events"))
    conn.execute(text("ALTER TABLE business_events_v9 RENAME TO business_events"))
    _create_business_event_indexes(conn)


def upgrade(engine, target: int = MIGRATION_VERSION) -> int:
    if target < 0 or target > MIGRATION_VERSION:
        raise ValueError(f"Unsupported migration target: {target}")
    _ensure_version_table(engine)
    current = migration_status(engine)
    if current > target:
        raise RuntimeError(f"Database version {current} is newer than requested version {target}")
    for version in range(current + 1, target + 1):
        with engine.begin() as conn:
            _apply_revision(conn, version)
            conn.execute(text("UPDATE schema_version SET version=:version WHERE id=1"), {"version": version})
    return migration_status(engine)


def downgrade(engine, target: int = 0) -> int:
    if target != 0:
        raise ValueError("Only downgrade to version 0 is supported")
    _ensure_version_table(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE schema_version SET version=0 WHERE id=1"))
    return 0
