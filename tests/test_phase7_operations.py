import sqlite3

from models import BackgroundJob
from services.jobs import enqueue, fail, reclaim_stale, process_one
from services.operations import verify_sqlite_backup


def test_background_job_is_durable_and_retries(db_session):
    job = enqueue(db_session, "sms", {"recipient": "redacted", "message": "hello"})
    assert job.status == "pending"
    fail(db_session, job, RuntimeError("temporary failure"))
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.retry_count == 1
    assert job.next_retry_at is not None


def test_stale_processing_jobs_are_reclaimed(db_session):
    from datetime import datetime, timedelta, timezone
    job = enqueue(db_session, "sms", {"recipient": "redacted", "message": "hello"})
    job.status = "processing"
    job.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    assert reclaim_stale(db_session, timeout_minutes=15) == 1
    db_session.refresh(job)
    assert job.status == "pending"


def test_tryon_jobs_without_provider_worker_are_retried(db_session):
    import asyncio
    job = enqueue(db_session, "unsupported", {"reference_paths": [], "output_path": "x", "result_url": "/x", "prompt": "p"})
    claimed = __import__('services.jobs', fromlist=['claim_next']).claim_next(db_session)
    try:
        asyncio.run(process_one(db_session, claimed))
    except RuntimeError as error:
        assert "Unsupported" in str(error)
    else:
        raise AssertionError("try-on job unexpectedly completed")


def test_backup_verification_checks_integrity_and_checksum(tmp_path):
    path = tmp_path / "backup.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
        connection.commit()
    result = verify_sqlite_backup(path)
    assert result["verified"] is True
    assert result["integrity"] == "ok"
    assert len(result["checksum"]) == 64
    assert result["size"] > 0
