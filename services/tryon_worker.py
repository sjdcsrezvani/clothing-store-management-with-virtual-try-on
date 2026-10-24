"""Dedicated virtual-try-on worker.

The 5-minute scheduler batch dominated try-on latency (0–300s of pure
waiting), so generations run here instead: a 10s poll loop with two jobs
in flight. The scheduler keeps backups/reminders/stale upkeep; it no
longer gates paid image work.

Quota burns on SUCCESS (imported lazily to avoid cycles). Billable
failures complete the job as failed immediately — requeueing a billed
generation risks paying twice, per the provider contract.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

POLL_SECONDS = 10
CONCURRENCY = 2


async def tryon_worker_task() -> None:
    """Poll tryon jobs forever. Started from the app lifespan."""
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
                        job = claim_next(session, "tryon")
                        if not job:
                            return
                            try:
                                await process_one(session, job)
                            except ImageGenerationError as error:
                                # Billable failure: fail fast, never requeue.
                                job.status = "failed"
                                job.error_message = str(error)[:1000]
                                job.locked_at = None
                                session.commit()
                                return
                            except Exception as error:
                                # Local failure (payload, disk, db): requeue
                                # with backoff like the scheduler; no job
                                # strands as processing, no pass ever dies.
                                fail(session, job, error)
                                return
                        complete(session, job)
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
            logger.exception("tryon worker pass failed")
        await asyncio.sleep(POLL_SECONDS)
