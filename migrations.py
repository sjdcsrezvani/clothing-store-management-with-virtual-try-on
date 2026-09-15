"""Versioned database migrations for the local store database."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

MIGRATION_VERSION = 19


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


def _rebuild_sms_messages(conn) -> None:
    """Rebuild ``sms_messages`` without the frozen CHECK on ``source``.

    A database created before the automatic triggers existed carries
    ``ck_sms_message_source`` listing the senders of that day, so the new
    «پس از خرید» and «پیگیری» rows were rejected outright at INSERT — the same
    trap revision 15 rebuilt ``business_events`` to escape. The column is
    validated in code instead (``services.sms_templates.log_message``).

    A fresh install has no such constraint and is left alone, and every other
    column and constraint is carried over untouched.
    """
    ddl = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sms_messages'"
    )).scalar()
    if not ddl or "ck_sms_message_source" not in ddl:
        return
    # The additive pass may not have run yet, so make sure every column this
    # rebuild copies actually exists before it copies it.
    for column_name, column_type in (
        ("delivery_state", "VARCHAR(20) DEFAULT ''"),
        ("claimed_at", "DATETIME"),
        ("sent_by_device_id", "INTEGER"),
        ("attempts", "INTEGER DEFAULT 0"),
        ("ref", "VARCHAR(60) DEFAULT ''"),
    ):
        _add_column_if_missing(conn, "sms_messages", column_name, column_type)
    conn.execute(text("""
        CREATE TABLE sms_messages_v17 (
            id INTEGER NOT NULL,
            template_id INTEGER,
            template_key VARCHAR(50),
            template_name VARCHAR(200),
            customer_id INTEGER,
            employee_id INTEGER,
            job_id INTEGER,
            phone VARCHAR(20) NOT NULL,
            body TEXT NOT NULL,
            status VARCHAR(20) NOT NULL,
            kind VARCHAR(20) NOT NULL,
            source VARCHAR(30) NOT NULL,
            error TEXT,
            ref VARCHAR(60) NOT NULL DEFAULT '',
            delivery_state VARCHAR(20) NOT NULL DEFAULT '',
            claimed_at DATETIME,
            sent_by_device_id INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            sent_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT ck_sms_message_status CHECK (status IN ('queued', 'sent', 'failed')),
            CONSTRAINT ck_sms_message_kind CHECK (kind IN ('marketing', 'transactional')),
            CONSTRAINT ck_sms_message_delivery_state
                CHECK (delivery_state IN ('', 'claimed', 'delivered', 'undelivered')),
            FOREIGN KEY(template_id) REFERENCES sms_templates (id),
            FOREIGN KEY(customer_id) REFERENCES customers (id),
            FOREIGN KEY(employee_id) REFERENCES staff_users (id),
            FOREIGN KEY(job_id) REFERENCES background_jobs (id),
            FOREIGN KEY(sent_by_device_id) REFERENCES sms_devices (id)
        )
    """))
    conn.execute(text("""
        INSERT INTO sms_messages_v17 (
            id, template_id, template_key, template_name, customer_id, employee_id,
            job_id, phone, body, status, kind, source, error, ref, delivery_state,
            claimed_at, sent_by_device_id, attempts, created_at, sent_at
        )
        SELECT
            id, template_id, template_key, template_name, customer_id, employee_id,
            job_id, phone, body, status, kind, source, error, ref, delivery_state,
            claimed_at, sent_by_device_id, attempts, created_at, sent_at
        FROM sms_messages
    """))
    conn.execute(text("DROP TABLE sms_messages"))
    conn.execute(text("ALTER TABLE sms_messages_v17 RENAME TO sms_messages"))
    for index_name, column_name in (
        ("ix_sms_messages_customer_id", "customer_id"),
        ("ix_sms_messages_job_id", "job_id"),
        ("ix_sms_messages_status", "status"),
        ("ix_sms_messages_source", "source"),
        ("ix_sms_messages_ref", "ref"),
        ("ix_sms_messages_created_at", "created_at"),
    ):
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON sms_messages ({column_name})"))


def _apply_revision(conn, version: int) -> None:
    if version == 19:
        # One drawer, one shift. Two managers clicking «باز کردن صندوق» in the
        # same instant used to leave two rows with status='open', and the
        # register then read whichever it found first — so the closing count of
        # one shift silently belonged to the other. The index makes that
        # impossible; the pass before it closes whatever a database already has.
        #
        # The duplicate is closed without a count rather than with a guessed
        # one: a count nobody performed is worse than no count, and the shift
        # statement says «بستهشده بدون شمارش» so the gap is visible instead of
        # being filled in.
        tables = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_sessions'"
        )).scalar()
        if not tables:
            # A database this new has no till to protect yet; `create_all` builds
            # the table with its index straight from the model.
            return
        conn.execute(text("""
            UPDATE cash_sessions
               SET status = 'abandoned', closed_at = CURRENT_TIMESTAMP
             WHERE status = 'open'
               AND id <> (SELECT MAX(id) FROM cash_sessions WHERE status = 'open')
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_sessions_one_open
                ON cash_sessions (status) WHERE status = 'open'
        """))
        return

    if version == 18:
        # Which customer values a message was rendered from, so a history entry
        # can be replayed and audited rather than merely read. Purely additive:
        # an existing shop's rows keep their meaning, and an empty string says
        # plainly that they were sent before anything recorded it.
        _add_column_if_missing(conn, "sms_messages", "values_json", "TEXT DEFAULT ''")
        return

    if version == 17:
        _rebuild_sms_messages(conn)
        _add_column_if_missing(conn, "sms_templates", "trigger_key", "VARCHAR(30) DEFAULT ''")
        _add_column_if_missing(conn, "sms_templates", "trigger_days", "INTEGER DEFAULT 0")
        return

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
