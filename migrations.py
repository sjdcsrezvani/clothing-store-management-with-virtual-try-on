"""Versioned database migrations for the local store database."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

MIGRATION_VERSION = 16


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


def _tag_print_batches_table_sql(table_name: str = "tag_print_batches") -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY,
            operator_user_id INTEGER NOT NULL,
            template_snapshot TEXT NOT NULL DEFAULT '{{}}',
            item_count INTEGER NOT NULL DEFAULT 0,
            total_quantity INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(operator_user_id) REFERENCES staff_users(id)
        )
    """


def _tag_print_batch_lines_table_sql(table_name: str = "tag_print_batch_lines") -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            variant_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            product_name VARCHAR(200) NOT NULL,
            barcode VARCHAR(50) NOT NULL,
            sku VARCHAR(50),
            size VARCHAR(20),
            color VARCHAR(50),
            unit_price INTEGER NOT NULL DEFAULT 0,
            is_reprint BOOLEAN NOT NULL DEFAULT 0,
            CONSTRAINT ck_tag_print_line_quantity_positive CHECK (quantity > 0),
            CONSTRAINT ck_tag_print_line_price_nonnegative CHECK (unit_price >= 0),
            FOREIGN KEY(batch_id) REFERENCES tag_print_batches(id),
            FOREIGN KEY(variant_id) REFERENCES product_variants(id)
        )
    """


def _create_tag_print_indexes(conn) -> None:
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batches_operator_user_id ON tag_print_batches (operator_user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batch_lines_batch_id ON tag_print_batch_lines (batch_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batch_lines_variant_id ON tag_print_batch_lines (variant_id)"))


def _add_column_if_missing(conn, table_name: str, column_name: str, column_type: str) -> None:
    exists = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"
    ), {"table_name": table_name}).scalar()
    if not exists:
        return
    columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
    if column_name not in columns:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def _rebuild_business_events(conn) -> None:
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
    conn.execute(text(_business_events_table_sql("business_events_v12")))
    conn.execute(text("""
        INSERT INTO business_events_v12 (
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        )
        SELECT
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        FROM business_events
    """))
    conn.execute(text("DROP TABLE business_events"))
    conn.execute(text("ALTER TABLE business_events_v12 RENAME TO business_events"))
    _create_business_event_indexes(conn)


def _apply_revision(conn, version: int) -> None:
    if version == 16:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tag_templates (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                config_json TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_templates_name ON tag_templates (name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_templates_is_active ON tag_templates (is_active)"))
        _add_column_if_missing(conn, "products", "tag_template_id", "INTEGER")
        products_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'"
        )).scalar()
        if products_exists:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_tag_template_id ON products (tag_template_id)"))
        return

    if version == 15:
        # Rebuild business_events without the event-type CHECK constraint when a
        # stale copy is present. The constraint was frozen into the table at
        # creation time, so databases created before newer event types were
        # released silently rejected them during INSERT OR IGNORE. Event types
        # are validated in code instead. Fresh installs already lack the
        # constraint and are left untouched.
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar()
        if not table_exists:
            conn.execute(text(_business_events_table_sql("business_events")))
            _create_business_event_indexes(conn)
            return
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar() or ""
        if "ck_business_events_type" not in ddl:
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
        conn.execute(text(_business_events_table_sql("business_events_v15")))
        conn.execute(text("""
            INSERT INTO business_events_v15 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v15 RENAME TO business_events"))
        _create_business_event_indexes(conn)
        return

    if version == 13:
        conn.execute(text(_tag_print_batches_table_sql()))
        conn.execute(text(_tag_print_batch_lines_table_sql()))
        _add_column_if_missing(conn, "tag_print_batch_lines", "unit_price", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "tag_print_batch_lines", "is_reprint", "BOOLEAN NOT NULL DEFAULT 0")
        _create_tag_print_indexes(conn)
        return

    if version == 8:
        conn.execute(text(_business_events_table_sql("business_events").replace(
            "CREATE TABLE business_events", "CREATE TABLE IF NOT EXISTS business_events", 1
        )))
        _create_business_event_indexes(conn)
        return

    if version == 11:
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
        conn.execute(text(_business_events_table_sql("business_events_v11")))
        conn.execute(text("""
            INSERT INTO business_events_v11 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v11 RENAME TO business_events"))
        _create_business_event_indexes(conn)
        return

    if version == 10:
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
        conn.execute(text(_business_events_table_sql("business_events_v10")))
        conn.execute(text("""
            INSERT INTO business_events_v10 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v10 RENAME TO business_events"))
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
