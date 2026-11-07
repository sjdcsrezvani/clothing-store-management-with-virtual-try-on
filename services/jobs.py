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


def claim_next(db, job_type: str | tuple[str, ...] | None = None) -> BackgroundJob | None:
    now = datetime.now(timezone.utc)
    query = db.query(BackgroundJob).filter(
        BackgroundJob.status == "pending",
        (BackgroundJob.next_retry_at.is_(None) | (BackgroundJob.next_retry_at <= now)),
    )
    if job_type:
        if isinstance(job_type, tuple):
            query = query.filter(BackgroundJob.job_type.in_(job_type))
        else:
            query = query.filter(BackgroundJob.job_type == job_type)
    job = query.order_by(BackgroundJob.id.asc()).with_for_update().first()
    if not job:
        return None
    job.status = "processing"
    job.locked_at = now
    db.commit()
    return job


def _persist_attempt(job, payload: dict) -> None:
    """Keep the last provider attempt on the job row so the log survives a
    restart. Diagnostics must never break the generation itself."""
    try:
        from services import tryon_debug
        attempt = tryon_debug.latest_for_job(job.id)
        if not attempt:
            return
        payload["debug"] = {**attempt, "at": attempt["at"].isoformat()}
        job.payload = json.dumps(payload, ensure_ascii=False)
    except Exception:
        pass


async def process_one(db, job: BackgroundJob) -> bool:
    payload = json.loads(job.payload)
    if job.job_type == "sms":
        # Legacy queue rows from before the on-premise gateway. The message row
        # this payload carried is now *itself* the gateway's queue item, so the
        # phone claims it directly from the log — there is nothing left for the
        # worker to deliver, and completing the job keeps it out of the way.
        return True
    if job.job_type == "tryon":
        from services.image_gen import get_provider, get_tryon_selection
        from routers.clothes_images import _remove_temp_file
        paths = payload["reference_paths"]
        # Provider, model and key resolve at execution time (not enqueue) so a
        # rotated Settings key applies to already-queued jobs. Never stored in
        # the payload — job rows must not hold secrets.
        provider_name, model_name, api_key = get_tryon_selection(db)
        provider = get_provider(api_key, provider_name, model_name)
        try:
            image_bytes = await __import__("asyncio").to_thread(
                provider.generate,
                prompt=payload["prompt"],
                reference_image_paths=paths,
                prompt_mid=payload.get("prompt_mid", ""),
                prompt_suffix=payload.get("prompt_suffix", ""),
                job_id=job.id,
            )
        finally:
            try:
                provider.cleanup()
            except Exception:
                pass
            _persist_attempt(job, payload)
        try:
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
    if job.job_type == "instagram_image":
        from services.image_gen import get_generation_selection, get_tryon_selection
        from services.instagram_gen import generate_instagram_image, write_output
        references = payload.get("reference_paths") or []
        # Text campaigns are the only reference-free path and use the separate
        # generation model; every image path keeps the edit model.
        selection = (get_tryon_selection(db) if references
                     else get_generation_selection(db))
        provider_name, model_name, api_key = selection
        image_bytes = await __import__("asyncio").to_thread(
            generate_instagram_image,
            prompt=payload["prompt"],
            reference_paths=references,
            aspect_ratio=payload.get("aspect_ratio", "4:5"),
            provider=provider_name,
            model=model_name,
            api_key=api_key,
            # Store the clean render; the logo is composited on download so the
            # same generation can be downloaded both ways (plan 6.10).
            logo=False,
            logo_path="",
            job_id=job.id,
        )
        output_path = Path(payload["output_path"])
        write_output(image_bytes, str(output_path))
        job.result_path = str(output_path)
        job.result_url = payload["result_url"]
        db.commit()
        # The uploaded campaign photo is only needed for this one paid request.
        for temporary in payload.get("temporary_paths", []):
            Path(temporary).unlink(missing_ok=True)
        return True
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
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = "pending"
        job.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=2 ** min(job.retry_count, 4))
    db.commit()
