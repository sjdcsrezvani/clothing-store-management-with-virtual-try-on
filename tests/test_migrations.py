from sqlalchemy import create_engine, text

from migrations import MIGRATION_VERSION, backup_database, downgrade, migration_status, upgrade


def test_versioned_upgrade_and_downgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    assert migration_status(engine) == 0
    assert upgrade(engine) == MIGRATION_VERSION
    assert migration_status(engine) == MIGRATION_VERSION
    assert downgrade(engine) == 0
    assert migration_status(engine) == 0


def test_migration_rejects_future_version(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE schema_version SET version=99 WHERE id=1"))
    try:
        upgrade(engine)
    except RuntimeError as error:
        assert "newer" in str(error)
    else:
        raise AssertionError("future schema version was accepted")


def test_upgrade_rebuilds_stale_business_events_constraint(tmp_path):
    """A table created before newer event types existed must accept them after upgrade.

    INSERT OR IGNORE in SQLite silently skips rows that violate a CHECK
    constraint, so a frozen event-type CHECK made the journal swallow newer
    events and then raise NoResultFound on the re-read.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    stale_types = "('CheckoutCreated', 'SaleCompleted', 'CashSessionClosed')"
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 13)"))
        conn.execute(text(f"""
            CREATE TABLE business_events (
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
                CONSTRAINT ck_business_events_type CHECK (event_type IN {stale_types}),
                CONSTRAINT ck_business_events_aggregate_id CHECK (aggregate_id IS NULL OR aggregate_id > 0),
                CONSTRAINT ck_business_events_schema_version CHECK (schema_version > 0)
            )
        """))
        conn.execute(text("""
            INSERT INTO business_events
                (event_type, aggregate_type, aggregate_id, idempotency_key, schema_version, occurred_at)
            VALUES ('SaleCompleted', 'sale', 1, 'sale:1:completed', 1, '2026-01-01 00:00:00')
        """))

    # Pre-upgrade, the newer event types are silently dropped by OR IGNORE.
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT OR IGNORE INTO business_events
                (event_type, aggregate_type, aggregate_id, idempotency_key, schema_version, occurred_at)
            VALUES ('TagBatchPrinted', 'tag_print_batch', 1, 'tag-batch:1:printed', 1, '2026-01-01 00:00:00')
        """))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM business_events")).scalar() == 1

    assert upgrade(engine) == MIGRATION_VERSION

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO business_events
                (event_type, aggregate_type, aggregate_id, idempotency_key, schema_version, occurred_at)
            VALUES ('TagBatchPrinted', 'tag_print_batch', 1, 'tag-batch:1:printed', 1, '2026-01-01 00:00:00')
        """))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM business_events")).scalar() == 2
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar()
        assert "ck_business_events_type" not in ddl
        assert "ck_business_events_aggregate_id" in ddl


def test_backup_database_creates_copy(tmp_path):
    source = tmp_path / "store.db"
    source.write_bytes(b"database contents")
    backup = backup_database(source)
    assert backup.exists()
    assert backup.read_bytes() == source.read_bytes()
    assert "before-migration" in backup.name
