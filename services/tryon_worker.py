"""Dedicated image-generation worker.

The 5-minute scheduler batch dominated image latency (0–300s of pure waiting),
so paid image work runs here instead: a 10s poll loop with two jobs in flight,
for both try-on and Instagram Studio. The scheduler keeps backups/reminders/stale
upkeep; it no longer gates paid image work.

Quota burns on SUCCESS for try-on (imported lazily to avoid cycles). Instagram
quota is counted from its job rows, so no callback is needed. Billable failures
complete the job as failed immediately — requeueing a billed generation risks
paying twice, per the provider contract.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

POLL_SECONDS = 10
CONCURRENCY = 2
IMAGE_JOB_TYPES = ("tryon", "instagram_image")


async def image_worker_task() -> None:
    """Poll paid image jobs forever. Started from the app lifespan."""
    from database import SessionLocal
    from services.jobs import claim_next, process_one, complete, fail
    from services.security import tryon_record_generation
    from services.image_gen import ImageGenerationError

    sem = asyncio.Semaphore(CONCURRENCY)
    while True:
        try:
            async def _one() -> None:
                async with sem:
                    session = SessionLocal()
                    try:
                        job = claim_next(session, IMAGE_JOB_TYPES)
                        if not job:
                            return
                        try:
                            await process_one(session, job)
                        except ImageGenerationError as error:
                            # Billable failure: fail fast, never requeue.
                            job.status = "failed"
                            job.error_message = str(error)[:1000]
                            job.locked_at = None
                            job.completed_at = datetime.now(timezone.utc)
                            session.commit()
                            return
                        except Exception as error:
                            # Local failure (payload, disk, db): requeue
                            # with backoff like the scheduler; no job
                            # strands as processing, no pass ever dies.
                            fail(session, job, error)
                            return
                        complete(session, job)
                        if job.job_type == "tryon":
                            try:
                                import json
                                queued_ip = (json.loads(job.payload or "{}")).get("ip")
                            except Exception:
                                queued_ip = None
                            tryon_record_generation(ip=queued_ip)
                    finally:
                        session.close()
            await asyncio.gather(*(_one() for _ in range(CONCURRENCY)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("image worker pass failed")
        await asyncio.sleep(POLL_SECONDS)


# Backward-compatible name for existing imports and tests.
tryon_worker_task = image_worker_task
