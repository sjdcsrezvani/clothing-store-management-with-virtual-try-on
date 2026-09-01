from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from config import DEFAULT_REFERRER_DISCOUNT, DEFAULT_REFERRED_DISCOUNT
from database import get_db
from models import Customer, Referral, generate_referral_code, to_english_digits
from services._common import gregorian_to_jalali
from services.security import require_api_token
from services.sms import send_welcome_sms

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_token)])


class CustomerCreate(BaseModel):
    phone: str
    first_name: str = ""
    last_name: str = ""
    referrer_code: str = ""
    referrer_phone: str = ""


class CustomerResponse(BaseModel):
    id: int
    phone: str
    first_name: str | None
    last_name: str | None
    referral_code: str
    referred_discount: int
    has_used_referred_discount: bool
    referrer_discount: int

    class Config:
        from_attributes = True


@router.post("/customers", response_model=CustomerResponse)
async def api_create_customer(body: CustomerCreate, db: Session = Depends(get_db)):
    phone = to_english_digits(body.phone.strip())
    if not phone.startswith("09") or len(phone) != 11:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر")

    existing = db.query(Customer).filter(Customer.phone == phone).first()
    if existing:
        raise HTTPException(status_code=409, detail="این شماره قبلاً ثبت شده")

    referrer = None
    if body.referrer_code:
        referrer = db.query(Customer).filter(Customer.referral_code == to_english_digits(body.referrer_code).upper()).first()
    elif body.referrer_phone:
        referrer = db.query(Customer).filter(Customer.phone == to_english_digits(body.referrer_phone.strip())).first()

    code = generate_referral_code()
    while db.query(Customer).filter(Customer.referral_code == code).first():
        code = generate_referral_code()

    customer = Customer(
        phone=phone,
        first_name=body.first_name if body.first_name else None,
        last_name=body.last_name if body.last_name else None,
        referral_code=code,
        referred_by=referrer.id if referrer else None,
        referred_discount=DEFAULT_REFERRED_DISCOUNT if referrer else 0,
    )
    db.add(customer)
    db.flush()

    if referrer:
        referrer.referrer_discount += DEFAULT_REFERRER_DISCOUNT
        referral = Referral(
            referrer_id=referrer.id,
            referred_id=customer.id,
            referrer_discount=DEFAULT_REFERRER_DISCOUNT,
            referred_discount=DEFAULT_REFERRED_DISCOUNT,
        )
        db.add(referral)

    db.commit()
    await send_welcome_sms(phone, body.first_name, code, db)

    return customer


@router.get("/customers")
async def api_lookup_customer(phone: str = "", db: Session = Depends(get_db)):
    if not phone:
        raise HTTPException(status_code=400, detail="شماره موبایل الزامی است")

    phone = to_english_digits(phone.strip())
    customer = db.query(Customer).filter(Customer.phone == phone).first()
    if not customer:
        raise HTTPException(status_code=404, detail="مشتری یافت نشد")

    referrals = db.query(Referral).filter(Referral.referrer_id == customer.id).all()
    return {
        "customer": CustomerResponse.model_validate(customer),
        "referral_count": len(referrals),
        "referrals": [
            {
                "id": r.id,
                "referred_id": r.referred_id,
                "referrer_discount": r.referrer_discount,
                "referred_discount": r.referred_discount,
                "created_at": gregorian_to_jalali(r.created_at),
            }
            for r in referrals
        ],
    }


@router.get("/stats")
async def api_stats(db: Session = Depends(get_db)):
    total_customers = db.query(Customer).count()
    total_referrals = db.query(Referral).count()
    return {
        "total_customers": total_customers,
        "total_referrals": total_referrals,
    }
