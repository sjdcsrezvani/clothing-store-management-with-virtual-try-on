import asyncio
import logging
from datetime import datetime, timezone
from database import SessionLocal
from services.backup import create_backup
from services.checkout import expire_stale
from services.jobs import reclaim_stale, claim_next, process_one, complete, fail
from services.tier import (
    get_tier_config,
    get_customers_for_downgrade_check,
    check_tier_downgrade,
)

logger = logging.getLogger(__name__)


async def check_tier_downgrades():
    """Check for customers who should be downgraded due to inactivity."""
    db = SessionLocal()
    try:
        config = get_tier_config(db)

        if config["downgrade_months"] <= 0 or config["downgrade_amount"] <= 0:
            return

        customers = get_customers_for_downgrade_check(db)

        for customer in customers:
            was_downgraded = check_tier_downgrade(customer, config, db)
            if was_downgraded:
                logger.info("Customer tier downgraded")

        db.commit()

    except Exception as e:
        logger.error(f"Tier downgrade check error: {e}")
    finally:
        db.close()


async def scheduler_task():
    """Background task that runs periodically."""
    while True:
        try:
            now = datetime.now(timezone.utc)

            # Run tier downgrade check daily at 1 AM UTC
            if now.hour == 1 and now.minute < 5:
                logger.info("Running tier downgrade check")
                await check_tier_downgrades()

            # Daily SQLite backup at 2 AM UTC (keeps the last 30)
            if now.hour == 2 and now.minute < 5:
                logger.info("Running daily database backup")
                await asyncio.to_thread(create_backup)

            db = SessionLocal()
            try:
                expire_stale(db)
                reclaim_stale(db)
                for _ in range(10):
                    job = claim_next(db)
                    if not job:
                        break
                    try:
                        await process_one(db, job)
                        complete(db, job)
                    except Exception as error:
                        fail(db, job, error)
            finally:
                db.close()

            # Sleep for 5 minutes before checking again
            await asyncio.sleep(300)

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)


async def run_downgrade_check_now():
    """Manually trigger downgrade check."""
    await check_tier_downgrades()
