from sqlalchemy import create_engine, text

from migrations import backup_database, downgrade, migration_status, upgrade


def test_versioned_upgrade_and_downgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    assert migration_status(engine) == 0
    assert upgrade(engine) == 1
    assert migration_status(engine) == 1
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


def test_backup_database_creates_copy(tmp_path):
    source = tmp_path / "store.db"
    source.write_bytes(b"database contents")
    backup = backup_database(source)
    assert backup.exists()
    assert backup.read_bytes() == source.read_bytes()
    assert "before-migration" in backup.name
