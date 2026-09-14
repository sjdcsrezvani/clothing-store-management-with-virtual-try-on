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


def test_upgrade_rebuilds_stale_sms_message_source_constraint(tmp_path):
    """A log table created before the automatic triggers existed must accept them.

    The frozen ``ck_sms_message_source`` listed the senders of the day, so the new
    «پس از خرید» and «پیگیری» rows were rejected outright — an automatic trigger
    that silently could not record anything it sent. History has to survive the
    rebuild, because it is the only account of what customers received.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'stale-sms.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 16)"))
        conn.execute(text("""
            CREATE TABLE sms_messages (
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
                created_at DATETIME NOT NULL,
                sent_at DATETIME,
                PRIMARY KEY (id),
                CONSTRAINT ck_sms_message_status CHECK (status IN ('queued', 'sent', 'failed')),
                CONSTRAINT ck_sms_message_kind CHECK (kind IN ('marketing', 'transactional')),
                CONSTRAINT ck_sms_message_source CHECK (source IN
                    ('manual', 'welcome', 'birthday', 'tier_up', 'campaign', 'credit_reminder', 'test'))
            )
        """))
        conn.execute(text("""
            INSERT INTO sms_messages (phone, body, status, kind, source, created_at)
            VALUES ('09120000000', 'خوش آمدی سارا', 'sent', 'marketing', 'welcome', '2026-01-01 00:00:00')
        """))

    assert upgrade(engine) == MIGRATION_VERSION

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO sms_messages (phone, body, status, kind, source, ref, delivery_state,
                                      attempts, created_at)
            VALUES ('09120000001', 'ممنون از خریدت', 'queued', 'transactional', 'purchase',
                    'sale:9', '', 0, '2026-01-02 00:00:00')
        """))
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT source, body FROM sms_messages ORDER BY id")).all()
        assert rows[0] == ("welcome", "خوش آمدی سارا")     # history survived
        assert rows[1][0] == "purchase"                     # and the new sender fits
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sms_messages'"
        )).scalar()
        assert "ck_sms_message_source" not in ddl
        assert "ck_sms_message_status" in ddl               # the rest is kept


def test_backup_database_creates_copy(tmp_path):
    source = tmp_path / "store.db"
    source.write_bytes(b"database contents")
    backup = backup_database(source)
    assert backup.exists()
    assert backup.read_bytes() == source.read_bytes()
    assert "before-migration" in backup.name
