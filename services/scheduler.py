import asyncio
import logging
from datetime import datetime, timezone
from database import SessionLocal
from models import Customer, Settings, Campaign
from services.tier import (
    get_tier_config,
    get_customers_for_birthday_check,
    check_tier_downgrade,
)
from services.sms import (
    send_birthday_sms,
    send_monthly_silver_sms,
    send_monthly_gold_diamond_sms,
)

logger = logging.getLogger(__name__)


async def check_birthday_reminders():
    """Check for upcoming birthdays and send SMS reminders (7 days before)."""
    db = SessionLocal()
    try:
        config = get_tier_config(db)
        days_before = config["birthday_sms_days_before"]
        
        eligible_customers = get_customers_for_birthday_check(db, days_before)
        
        for customer, days_until in eligible_customers:
            # Check if we already sent a reminder for this birthday
            log_key = f"birthday_sms_{customer.id}_{datetime.now(timezone.utc).year}_{customer.child_birthday}"
            already_sent = db.query(Settings).filter(Settings.key == log_key).first()
            
            if already_sent:
                continue
            
            # Send birthday SMS (same pattern for gold and diamond)
            success = await send_birthday_sms(
                customer.phone,
                customer.first_name or "",
                customer.child_name or "",
                db,
            )
            
            if success:
                db.add(Settings(key=log_key, value="sent"))
                db.commit()
                logger.info(f"Birthday SMS sent to {customer.phone} for {customer.child_name}")
            else:
                logger.warning(f"Failed to send birthday SMS to {customer.phone}")
                
    except Exception as e:
        logger.error(f"Birthday check error: {e}")
    finally:
        db.close()


async def send_monthly_reports():
    """Send monthly report SMS to all customers on the last day of each month."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Only send on the last day of the month
        if now.day < 28:
            return
        
        # Check if we already sent this month
        month_key = f"monthly_report_{now.year}_{now.month}"
        already_sent = db.query(Settings).filter(Settings.key == month_key).first()
        if already_sent:
            return
        
        customers = db.query(Customer).filter(
            Customer.first_name.isnot(None),
            Customer.first_name != ""
        ).all()
        
        sent_count = 0
        for customer in customers:
            if customer.tier == "silver":
                success = await send_monthly_silver_sms(
                    customer.phone,
                    customer.first_name or "",
                    customer.total_points,
                    customer.active_referral_count,
                    db,
                )
            else:
                # Gold or Diamond
                tier_percent = 5 if customer.tier == "gold" else 10
                success = await send_monthly_gold_diamond_sms(
                    customer.phone,
                    customer.first_name or "",
                    customer.total_points,
                    customer.active_referral_count,
                    customer.tier,
                    tier_percent,
                    db,
                )
            
            if success:
                sent_count += 1
        
        # Log that we sent monthly reports
        db.add(Settings(key=month_key, value=str(sent_count)))
        db.commit()
        logger.info(f"Monthly reports sent to {sent_count} customers")
                
    except Exception as e:
        logger.error(f"Monthly report error: {e}")
    finally:
        db.close()


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
                logger.info(f"Customer {customer.phone} downgraded to {customer.tier}")
        
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
            
            # Run birthday check at midnight UTC
            if now.hour == 0 and now.minute < 5:
                logger.info("Running birthday reminder check")
                await check_birthday_reminders()
            
            # Run monthly reports on last day of month at 2 AM UTC
            if now.hour == 2 and now.minute < 5:
                logger.info("Running monthly reports")
                await send_monthly_reports()
            
            # Run tier downgrade check daily at 1 AM UTC
            if now.hour == 1 and now.minute < 5:
                logger.info("Running tier downgrade check")
                await check_tier_downgrades()
            
            # Sleep for 5 minutes before checking again
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)


async def run_birthday_check_now():
    """Manually trigger birthday check."""
    await check_birthday_reminders()


async def run_downgrade_check_now():
    """Manually trigger downgrade check."""
    await check_tier_downgrades()


async def run_monthly_reports_now():
    """Manually trigger monthly reports."""
    await send_monthly_reports()
