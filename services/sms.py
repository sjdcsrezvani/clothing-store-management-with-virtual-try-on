import httpx
import logging
from sqlalchemy.orm import Session
from models import Settings
from services.jobs import enqueue
from config import SMS_GATEWAY_URL, SMS_API_KEY, SMS_DEVICE_ID

logger = logging.getLogger(__name__)


def get_sms_setting(db: Session, key: str) -> str:
    """Get SMS setting from database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    return setting.value if setting and setting.value else ""


def get_sms_config(db: Session) -> dict:
    """Get all SMS configuration from database."""
    return {
        "api_key": get_sms_setting(db, "sms_api_key") or SMS_API_KEY,
        "device_id": get_sms_setting(db, "sms_device_id") or SMS_DEVICE_ID,
        "welcome_pattern": get_sms_setting(db, "sms_pattern_welcome"),
        "birthday_pattern": get_sms_setting(db, "sms_pattern_birthday"),
        "tier_up_gold_pattern": get_sms_setting(db, "sms_pattern_tier_up_gold"),
        "tier_up_diamond_pattern": get_sms_setting(db, "sms_pattern_tier_up_diamond"),
        "campaign_pattern": get_sms_setting(db, "sms_pattern_campaign"),
    }


def _render(template: str, attributes: dict) -> str:
    """Fill %varN% / {varN} placeholders in a pattern template to build the message string."""
    for key, value in attributes.items():
        template = template.replace(f"%{key}%", str(value)).replace(f"{{{key}}}", str(value))
    return template


async def send_sms(message: str, recipient: str, db: Session) -> bool:
    """Send a plain-text SMS through the self-hosted gateway."""
    config = get_sms_config(db)

    if not config["api_key"] or not config["device_id"]:
        logger.warning("SMS skipped because gateway configuration is incomplete")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SMS_GATEWAY_URL}/api/v1/sms/send",
                headers={"X-API-Key": config["api_key"]},
                json={
                    "device_id": int(config["device_id"]),
                    "to_number": recipient,
                    "message": message,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("SMS sent")
                return True
            else:
                logger.error("SMS failed with status %s", resp.status_code)
                return False
    except Exception as e:
        logger.error(f"SMS error: {e}")
        return False


async def send_pattern_sms(pattern: str, recipient: str, attributes: dict, db: Session) -> bool:
    """Render a pattern template into a message string and send it."""
    message = _render(pattern, attributes)
    return await send_sms(message, recipient, db)


# ========== Pattern 1: Welcome SMS ==========
# Keys: var1=first_name, var2=referral_code
async def queue_sms(pattern: str, recipient: str, attributes: dict, db: Session):
    if not pattern:
        return None
    job = enqueue(db, "sms", {"pattern": pattern, "recipient": recipient, "attributes": attributes})
    db.commit()
    return job


async def queue_welcome_sms(phone: str, first_name: str, referral_code: str, db: Session):
    config = get_sms_config(db)
    if not config["welcome_pattern"]:
        return None
    job = enqueue(db, "sms", {"pattern": config["welcome_pattern"], "recipient": phone, "attributes": {"var1": first_name or "مشتری", "var2": referral_code}})
    db.commit()
    return job


async def send_welcome_sms(phone: str, first_name: str, referral_code: str, db: Session) -> bool:
    """Send welcome SMS after checkout completion."""
    config = get_sms_config(db)
    if not config["welcome_pattern"]:
        return False

    return await send_pattern_sms(
        config["welcome_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": referral_code},
        db,
    )


# ========== Pattern 2: Birthday SMS (Gold & Diamond) ==========
# Keys: var1=first_name, var2=child_name
async def send_birthday_sms(phone: str, first_name: str, child_name: str, db: Session) -> bool:
    """Send birthday SMS 7 days before child's birthday."""
    config = get_sms_config(db)
    if not config["birthday_pattern"]:
        return False

    return await send_pattern_sms(
        config["birthday_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": child_name or "فرزند شما"},
        db,
    )


# ========== Pattern 3: Tier-up (Silver → Gold) ==========
# Keys: var1=first_name, var2=points
async def send_tier_up_gold_sms(phone: str, first_name: str, points: int, db: Session) -> bool:
    """Send tier-up SMS to a customer who reached gold."""
    config = get_sms_config(db)
    if not config["tier_up_gold_pattern"]:
        return False

    return await send_pattern_sms(
        config["tier_up_gold_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": str(points)},
        db,
    )


# ========== Pattern 4: Tier-up (Gold → Diamond) ==========
# Keys: var1=first_name, var2=points
async def send_tier_up_diamond_sms(phone: str, first_name: str, points: int, db: Session) -> bool:
    """Send tier-up SMS to a customer who reached diamond."""
    config = get_sms_config(db)
    if not config["tier_up_diamond_pattern"]:
        return False

    return await send_pattern_sms(
        config["tier_up_diamond_pattern"],
        phone,
        {"var1": first_name or "مشتری", "var2": str(points)},
        db,
    )


# ========== Pattern 5: Campaign SMS (Diamond only) ==========
# Keys: var1=first_name, var2=campaign_name, var3=campaign_code, var4=campaign_discount_percent
async def send_campaign_sms(phone: str, first_name: str, campaign_name: str, campaign_code: str, campaign_discount: int, db: Session) -> bool:
    """Send campaign SMS to diamond customers."""
    config = get_sms_config(db)
    if not config["campaign_pattern"]:
        return False

    return await send_pattern_sms(
        config["campaign_pattern"],
        phone,
        {
            "var1": first_name or "مشتری",
            "var2": campaign_name,
            "var3": campaign_code,
            "var4": str(campaign_discount),
        },
        db,
    )


async def get_balance(db: Session) -> str | None:
    """Get SMS device status."""
    config = get_sms_config(db)
    if not config["api_key"]:
        return None
    headers = {"X-API-Key": config["api_key"]}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SMS_GATEWAY_URL}/api/v1/admin/devices", headers=headers, timeout=10)
            if resp.status_code == 200:
                for device in resp.json():
                    if str(device.get("id")) == str(config["device_id"]):
                        status = device.get("status", "نامشخص")
                        return {"online": "آنلاین", "offline": "آفلاین", "never_connected": "هرگز متصل نشده"}.get(status, status)
                return "نامشخص"
    except Exception as e:
        logger.error(f"Device status check error: {e}")
    return None
