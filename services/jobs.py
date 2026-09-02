from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import BackgroundJob, GeneratedImage

MAX_RETRIES = 5


def enqueue(db, job_type: str, payload: dict) -> BackgroundJob:
    job = BackgroundJob(
        job_type=job_type,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        next_retry_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()
    return job


def reclaim_stale(db, timeout_minutes: int = 15) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    count = db.query(BackgroundJob).filter(
        BackgroundJob.status == "processing",
        BackgroundJob.locked_at.is_not(None),
        BackgroundJob.locked_at < cutoff,
    ).update({"status": "pending", "locked_at": None}, synchronize_session=False)
    db.commit()
    return count


def claim_next(db, job_type: str | None = None) -> BackgroundJob | None:
    now = datetime.now(timezone.utc)
    query = db.query(BackgroundJob).filter(
        BackgroundJob.status == "pending",
        (BackgroundJob.next_retry_at.is_(None) | (BackgroundJob.next_retry_at <= now)),
    )
    if job_type:
        query = query.filter(BackgroundJob.job_type == job_type)
    job = query.order_by(BackgroundJob.id.asc()).with_for_update().first()
    if not job:
        return None
    job.status = "processing"
    job.locked_at = now
    db.commit()
    return job


async def process_one(db, job: BackgroundJob) -> bool:
    payload = json.loads(job.payload)
    if job.job_type == "sms":
        from services.sms import send_pattern_sms
        if not await send_pattern_sms(payload["pattern"], payload["recipient"], payload["attributes"], db):
            raise RuntimeError("SMS delivery failed")
        return True
    if job.job_type == "tryon":
        from routers.clothes_images import image_gen, _remove_temp_file
        paths = payload["reference_paths"]
        try:
            image_bytes = await __import__("asyncio").to_thread(
                image_gen.generate,
                prompt=payload["prompt"],
                reference_image_paths=paths,
                prompt_mid=payload.get("prompt_mid", ""),
                prompt_suffix=payload.get("prompt_suffix", ""),
            )
            output_path = Path(payload["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)
            job.result_path = str(output_path)
            job.result_url = payload["result_url"]
            db.commit()
            return True
        finally:
            for path in payload.get("temporary_paths", []):
                _remove_temp_file(path)
    raise RuntimeError(f"Unsupported background job type: {job.job_type}")


def complete(db, job: BackgroundJob) -> None:
    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    job.error_message = None
    job.locked_at = None
    db.commit()


def fail(db, job: BackgroundJob, error: Exception) -> None:
    job.retry_count += 1
    job.error_message = str(error)[:1000]
    job.locked_at = None
    if job.retry_count >= MAX_RETRIES:
        job.status = "failed"
        job.next_retry_at = None
    else:
        job.status = "pending"
        job.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=2 ** min(job.retry_count, 4))
    db.commit()
