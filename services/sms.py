import httpx
import logging
from sqlalchemy.orm import Session
from models import Settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.iranpayamak.com"


def get_sms_setting(db: Session, key: str) -> str:
    """Get SMS setting from database."""
    setting = db.query(Settings).filter(Settings.key == key).first()
    return setting.value if setting and setting.value else ""


def get_sms_config(db: Session) -> dict:
    """Get all SMS configuration from database."""
    return {
        "api_key": get_sms_setting(db, "farazsms_api_key"),
        "line_number": get_sms_setting(db, "farazsms_line_number"),
        "welcome_pattern": get_sms_setting(db, "sms_pattern_welcome"),
        "birthday_pattern": get_sms_setting(db, "sms_pattern_birthday"),
        "monthly_silver_pattern": get_sms_setting(db, "sms_pattern_monthly_silver"),
        "monthly_gold_diamond_pattern": get_sms_setting(db, "sms_pattern_monthly_gold_diamond"),
        "campaign_pattern": get_sms_setting(db, "sms_pattern_campaign"),
    }


async def send_pattern_sms(pattern_code: str, recipient: str, attributes: dict, db: Session) -> bool:
    """Send a pattern-based SMS."""
    config = get_sms_config(db)
    
    if not config["api_key"]:
        logger.warning(f"SMS skipped (no API key): pattern={pattern_code}, to={recipient}, attrs={attributes}")
        return False

    headers = {"Api-Key": config["api_key"], "Content-Type": "application/json"}
    payload = {
        "code": pattern_code,
        "attributes": attributes,
        "recipient": recipient,
        "line_number": config["line_number"],
        "number_format": "english",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/ws/v1/sms/pattern", json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info(f"SMS sent to {recipient}")
                return True
            else:
                logger.error(f"SMS failed: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        logger.error(f"SMS error: {e}")
        return False


# ========== Pattern 1: Welcome SMS ==========
# Keys: var1=first_name, var2=referral_code
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


# ========== Pattern 3: Monthly Report (Silver) ==========
# Keys: var1=first_name, var2=points, var3=active_referral_count, var4=tier
async def send_monthly_silver_sms(phone: str, first_name: str, points: int, active_referral_count: int, db: Session) -> bool:
    """Send monthly report to silver-tier customers."""
    config = get_sms_config(db)
    if not config["monthly_silver_pattern"]:
        return False
    
    return await send_pattern_sms(
        config["monthly_silver_pattern"],
        phone,
        {
            "var1": first_name or "مشتری",
            "var2": str(points),
            "var3": str(active_referral_count),
            "var4": "نقره‌ای",
        },
        db,
    )


# ========== Pattern 4: Monthly Report (Gold & Diamond) ==========
# Keys: var1=first_name, var2=points, var3=active_referral_count, var4=tier, var5=permanent_discount_percent
async def send_monthly_gold_diamond_sms(phone: str, first_name: str, points: int, active_referral_count: int, tier: str, permanent_discount: int, db: Session) -> bool:
    """Send monthly report to gold/diamond-tier customers."""
    config = get_sms_config(db)
    if not config["monthly_gold_diamond_pattern"]:
        return False
    
    tier_names = {"gold": "طلایی", "diamond": "الماس"}
    
    return await send_pattern_sms(
        config["monthly_gold_diamond_pattern"],
        phone,
        {
            "var1": first_name or "مشتری",
            "var2": str(points),
            "var3": str(active_referral_count),
            "var4": tier_names.get(tier, tier),
            "var5": str(permanent_discount),
        },
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
    """Get SMS account balance."""
    config = get_sms_config(db)
    if not config["api_key"]:
        return None
    headers = {"Api-Key": config["api_key"]}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/ws/v1/account/balance", headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("data", "نامشخص")
    except Exception as e:
        logger.error(f"Balance check error: {e}")
    return None
