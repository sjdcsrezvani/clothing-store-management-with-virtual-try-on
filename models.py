import string
import random
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base


def generate_referral_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


def generate_barcode():
    return ''.join(random.choices(string.digits, k=12))


def to_english_digits(s: str) -> str:
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    result = s
    for i in range(10):
        result = result.replace(persian[i], str(i)).replace(arabic[i], str(i))
    return result


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(15), unique=True, index=True, nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    referral_code = Column(String(10), unique=True, index=True, nullable=False)
    referred_by = Column(Integer, ForeignKey("customers.id"), nullable=True)
    referred_discount = Column(Integer, default=0)
    has_used_referred_discount = Column(Boolean, default=False)
    referrer_discount = Column(Integer, default=0)
    active_referral_count = Column(Integer, default=0)
    monthly_referral_count = Column(Integer, default=0)
    monthly_referral_year = Column(Integer, default=0)
    monthly_referral_month = Column(Integer, default=0)
    child_name = Column(String(100), nullable=True)
    child_birthday = Column(String(5), nullable=True)
    total_points = Column(Integer, default=0)
    tier = Column(String(20), default="silver")
    total_purchases = Column(Integer, default=0)
    total_spent = Column(Integer, default=0)
    last_purchase_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_admin = Column(Boolean, default=False)

    referrer = relationship("Customer", remote_side=[id], backref="referrals_made")
    sales = relationship("Sale", back_populates="customer")

    @property
    def full_name(self):
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or "—"


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    referred_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    referrer_discount = Column(Integer, default=0)
    referred_discount = Column(Integer, default=0)

    referrer = relationship("Customer", foreign_keys=[referrer_id])
    referred = relationship("Customer", foreign_keys=[referred_id])


class Settings(Base):
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=True)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    barcode = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    size = Column(String(20), nullable=True)
    color = Column(String(50), nullable=True)
    price = Column(Integer, nullable=False)
    cost_price = Column(Integer, nullable=False, default=0)
    stock_quantity = Column(Integer, default=0)
    image_path = Column(String(500), nullable=True)
    category = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    brand = Column(String(100), nullable=True)
    sku = Column(String(50), nullable=True)
    weight_grams = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    sale_items = relationship("SaleItem", back_populates="product")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    total_amount = Column(Integer, nullable=False)
    discount_amount = Column(Integer, default=0)
    discount_details = Column(Text, nullable=True)
    final_amount = Column(Integer, nullable=False)
    payment_method = Column(String(50), default="card")
    payment_confirmed = Column(Boolean, default=False)
    points_earned = Column(Integer, default=0)
    is_refunded = Column(Boolean, default=False)
    refund_amount = Column(Integer, default=0)
    refund_reason = Column(Text, nullable=True)
    refund_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    campaigns = relationship("SaleCampaign", back_populates="sale")


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer, nullable=False)
    unit_cost = Column(Integer, default=0)
    total_price = Column(Integer, nullable=False)

    sale = relationship("Sale", back_populates="items")
    product = relationship("Product", back_populates="sale_items")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    discount_percent = Column(Integer, nullable=False)
    min_purchase = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sale_campaigns = relationship("SaleCampaign", back_populates="campaign")


class SaleCampaign(Base):
    __tablename__ = "sale_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    discount_amount = Column(Integer, default=0)

    sale = relationship("Sale", back_populates="campaigns")
    campaign = relationship("Campaign", back_populates="sale_campaigns")
