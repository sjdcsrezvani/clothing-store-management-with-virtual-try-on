import string
import random
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, CheckConstraint, UniqueConstraint, Index, text, event as sqlalchemy_event
from sqlalchemy.orm import relationship
from database import Base


def generate_referral_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


def generate_barcode():
    """Generate the five-digit code printed on product tags."""
    return ''.join(random.choices(string.digits, k=5))


def to_english_digits(s: str) -> str:
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    result = s
    for i in range(10):
        result = result.replace(persian[i], str(i)).replace(arabic[i], str(i))
    return result


def to_persian_digits(s) -> str:
    """English digits to Persian, for figures the owner reads in messages."""
    persian = "۰۱۲۳۴۵۶۷۸۹"
    return "".join(persian[int(ch)] if ch.isdigit() else ch for ch in str(s))


class StaffUser(Base):
    """An authenticated store staff member with an explicit role."""
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="cashier", index=True)
    full_name = Column(String(200), nullable=True)
    employee_code = Column(String(50), nullable=True, index=True)
    national_id = Column(String(30), nullable=True, index=True)
    phone = Column(String(30), nullable=True)
    email = Column(String(150), nullable=True)
    job_title = Column(String(100), nullable=True)
    employment_type = Column(String(20), nullable=False, default="full_time")
    hire_date = Column(DateTime, nullable=True)
    birth_date = Column(DateTime, nullable=True)
    contract_end_date = Column(DateTime, nullable=True)
    education = Column(String(200), nullable=True)
    work_schedule = Column(String(200), nullable=True)
    salary_payment_day = Column(Integer, nullable=True)
    address = Column(Text, nullable=True)
    emergency_contact = Column(String(200), nullable=True)
    bank_account = Column(String(80), nullable=True)
    iban = Column(String(40), nullable=True)
    salary_amount = Column(Integer, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('cashier', 'manager', 'owner')", name="ck_staff_users_role"),
        CheckConstraint("employment_type IN ('full_time', 'part_time', 'contractor')", name="ck_staff_users_employment_type"),
        CheckConstraint("salary_amount >= 0", name="ck_staff_users_salary_nonnegative"),
        CheckConstraint("salary_payment_day IS NULL OR (salary_payment_day >= 1 AND salary_payment_day <= 31)", name="ck_staff_users_salary_day_valid"),
    )

    salary_payments = relationship("SalaryPayment", foreign_keys="SalaryPayment.staff_user_id", back_populates="staff_user", order_by="SalaryPayment.paid_at.desc()")


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
    # The customer's own birthday — the field every kind of clothing shop can
    # use. Same shape as the child's below: Persian MM-DD plus the Persian year,
    # so a birthday can be matched every year and an age can be shown.
    birth_month_day = Column(String(5), nullable=True)
    birth_year = Column(Integer, nullable=True)
    # Internal staff note (never shown to the customer).
    notes = Column(Text, nullable=True)
    # Comma-separated labels from a fixed palette (VIP / wholesale / follow-up /
    # blocked). Stored wrapped by the query helper so "vip" can't match a longer
    # label. A join table would need its own management screen for little gain.
    tags = Column(String(200), default="")
    # Marketing SMS consent (campaign blasts and birthday wishes). NULL and 1 both
    # mean "yes" — only an explicit 0 opts out. Transactional SMS is not gated.
    sms_opt_in = Column(Boolean, default=True)
    # Archived customers keep their history but leave the list, the KPIs and the
    # marketing sends; they can be restored at any time.
    is_archived = Column(Boolean, default=False)
    # Whose clothes this customer buys: 'self' or 'child'. This is the
    # customer's own choice, not the store's — the counter asks once at signup
    # and it decides which birthday the discount and the wish use, so a
    # self-buyer in a children's shop is still wished on their own birthday.
    # NULL means never chosen: those rows follow the store's default target.
    buys_for = Column(String(8), nullable=True)
    # Children's-shop module: these are only collected and shown when the store
    # enables child profiles.
    child_name = Column(String(100), nullable=True)
    child_birthday = Column(String(5), nullable=True)
    child_birth_year = Column(Integer, nullable=True)
    child_photo_path = Column(String(500), nullable=True)
    total_points = Column(Integer, default=0)
    tier = Column(String(20), default="silver")
    total_purchases = Column(Integer, default=0)
    total_spent = Column(Integer, default=0)
    # Outstanding credit-sales debt (نسیه) the customer still owes.
    total_debt = Column(Integer, default=0)
    # Per-customer credit limit (سقف اعتبار): >0 caps نسیه debt; NULL/0 falls
    # back to the store-wide `default_credit_limit` setting (0 = unlimited).
    credit_limit = Column(Integer, nullable=True)
    last_purchase_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    referrer = relationship("Customer", remote_side=[id], backref="referrals_made")
    sales = relationship("Sale", back_populates="customer")

    @property
    def full_name(self):
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or "—"

    @property
    def display_name(self):
        """Name to greet in a message — never empty, unlike full_name's dash."""
        return self.full_name if self.full_name != "—" else "مشتری"


class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    product_ids = Column(String(500), nullable=True)
    image_path = Column(String(500), nullable=False)
    prompt_used = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer", backref="generated_images")


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


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String(40), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    result_path = Column(String(500), nullable=True)
    result_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_background_jobs_status"),
        CheckConstraint("retry_count >= 0", name="ck_background_jobs_retry_count"),
    )


class Settings(Base):
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=True)


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    image_path = Column(String(500), nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="images")


class VariantImage(Base):
    """Gallery frames for one sellable variant. The first frame (lowest
    sort_order) is the primary; the legacy ``ProductVariant.image_path``
    stays as the fallback so rows from before the gallery never go imageless."""

    __tablename__ = "variant_images"

    id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False, index=True)
    image_path = Column(String(500), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    variant = relationship("ProductVariant", back_populates="images")


class TagTemplate(Base):
    """Reusable product-level tag layout."""
    __tablename__ = "tag_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    config_json = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    products = relationship("Product", back_populates="tag_template")


class Product(Base):
    """Base product - the template (e.g., 'Nike T-Shirt')"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    brand = Column(String(100), nullable=True)
    base_sku = Column(String(50), nullable=True)
    base_barcode = Column(String(50), nullable=True)
    weight_grams = Column(Integer, nullable=True)
    garment_type = Column(String(80), nullable=True)
    gender = Column(String(30), nullable=True)
    material = Column(String(120), nullable=True)
    season = Column(String(50), nullable=True)
    collection = Column(String(100), nullable=True)
    care_instructions = Column(Text, nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    default_reorder_point = Column(Integer, nullable=False, default=0)
    default_reorder_quantity = Column(Integer, nullable=False, default=0)
    # Optional product-level tag layout; variants inherit this template.
    tag_template_id = Column(Integer, ForeignKey("tag_templates.id"), nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Single image for list views; variants can have their own images.
    image_path = Column(String(500), nullable=True)

    # Relationships
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")
    images = relationship("ProductImage", back_populates="product", order_by="ProductImage.sort_order", cascade="all, delete-orphan")
    supplier = relationship("Supplier", foreign_keys=[supplier_id])
    tag_template = relationship("TagTemplate", back_populates="products", foreign_keys=[tag_template_id])
    sale_items = relationship("SaleItem", back_populates="product")

    @property
    def total_stock(self):
        return sum(v.stock_quantity for v in self.variants if v.is_active)

    @property
    def total_reserved(self):
        return sum(v.reserved_quantity or 0 for v in self.variants if v.is_active)

    @property
    def available_stock(self):
        return sum(v.available_quantity for v in self.variants if v.is_active)

    @property
    def needs_reorder(self):
        return any(v.needs_reorder for v in self.variants if v.is_active)

    @property
    def price_range(self):
        prices = [v.price for v in self.variants if v.is_active]
        if not prices:
            return None
        return f"{min(prices):,} - {max(prices):,}"


class ProductVariant(Base):
    """Sellable variant - unique barcode, price, stock per size/color combo"""
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    # Size/Color - null means product has no variant (simple product)
    size = Column(String(20), nullable=True)
    color = Column(String(50), nullable=True)

    # Price can differ per variant
    price = Column(Integer, nullable=False)
    cost_price = Column(Integer, default=0)
    # Optional display-only cost price; sales and analytics always use cost_price.
    fake_cost_price = Column(Integer, nullable=True)
    # Free-text garment details (two-piece, sleeve length, etc.) appended to the try-on prompt.
    tryon_details = Column(Text, nullable=True)

    # Unique barcode per variant
    barcode = Column(String(50), unique=True, index=True, nullable=False)

    # Stock tracked per variant
    stock_quantity = Column(Integer, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)

    # Manual demand counter: how many customers asked for this variant
    # while it was out of stock. +1 by owner; reset by owner after restocking.
    demand_count = Column(Integer, default=0)
    reorder_point = Column(Integer, nullable=False, default=0)
    reorder_quantity = Column(Integer, nullable=False, default=0)
    # Weighed per sellable unit: sizes of one product rarely share a weight,
    # so the scale lives on the variant, not the product.
    weight_grams = Column(Integer, nullable=True)
    storage_location = Column(String(100), nullable=True)
    size_system = Column(String(30), nullable=True)
    color_code = Column(String(30), nullable=True)

    # Variant-specific SKU (optional)
    sku = Column(String(50), nullable=True)

    # Variant-specific image (e.g., different color photo)
    image_path = Column(String(500), nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_variants_price_nonnegative"),
        CheckConstraint("cost_price >= 0", name="ck_variants_cost_nonnegative"),
        CheckConstraint("stock_quantity >= 0", name="ck_variants_stock_nonnegative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_variants_reserved_nonnegative"),
        CheckConstraint("demand_count >= 0", name="ck_variants_demand_nonnegative"),
        CheckConstraint("reorder_point >= 0", name="ck_variants_reorder_point_nonnegative"),
        CheckConstraint("reorder_quantity >= 0", name="ck_variants_reorder_quantity_nonnegative"),
    )

    product = relationship("Product", back_populates="variants")
    sale_items = relationship("SaleItem", back_populates="variant")
    stock_movements = relationship("StockMovement", back_populates="variant", order_by="StockMovement.created_at", cascade="all, delete-orphan")
    images = relationship("VariantImage", back_populates="variant", order_by="VariantImage.sort_order", cascade="all, delete-orphan")

    @property
    def available_quantity(self):
        return max(0, (self.stock_quantity or 0) - (self.reserved_quantity or 0))

    @property
    def needs_reorder(self):
        return self.available_quantity <= (self.reorder_point or 0)

    @property
    def display_name(self):
        parts = [self.product.name if self.product else ""]
        if self.size:
            parts.append(f"سایز {self.size}")
        if self.color:
            parts.append(self.color)
        return " - ".join(p for p in parts if p)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("payment_method IN ('card', 'cash', 'credit')", name="ck_sales_payment_method"),
        CheckConstraint("total_amount >= 0", name="ck_sales_total_nonnegative"),
        CheckConstraint("discount_amount >= 0", name="ck_sales_discount_nonnegative"),
        CheckConstraint("final_amount >= 0", name="ck_sales_final_nonnegative"),
        CheckConstraint("credit_surcharge >= 0", name="ck_sales_credit_surcharge_nonnegative"),
        CheckConstraint("credit_paid_amount >= 0", name="ck_sales_credit_paid_nonnegative"),
        CheckConstraint("refund_amount >= 0", name="ck_sales_refund_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    total_amount = Column(Integer, nullable=False)
    discount_amount = Column(Integer, default=0)
    discount_details = Column(Text, nullable=True)
    final_amount = Column(Integer, nullable=False)
    payment_method = Column(String(50), default="card")  # card / cash / credit
    credit_surcharge = Column(Integer, default=0)  # نسیه surcharge added to final_amount
    payment_confirmed = Column(Boolean, default=False)
    # Credit-sale (نسیه) tracking: how much of final_amount has been paid back.
    credit_settled = Column(Boolean, default=False)
    credit_paid_amount = Column(Integer, default=0)
    # The date this نسیه invoice is due (سررسید). NULL means the shop agreed no
    # date — those invoices keep ageing by their own date, exactly as the credit
    # page did before this column existed, so nothing about them moves.
    credit_due_date = Column(DateTime, nullable=True)
    points_earned = Column(Integer, default=0)
    is_refunded = Column(Boolean, default=False)
    refund_amount = Column(Integer, default=0)
    refund_reason = Column(Text, nullable=True)
    refund_date = Column(DateTime, nullable=True)
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    campaigns = relationship("SaleCampaign", back_populates="sale")
    pos_transaction = relationship("POSTransaction", back_populates="sale", uselist=False)


class POSTransaction(Base):
    """Durable record of a terminal attempt and its local reconciliation state."""
    __tablename__ = "pos_transactions"

    id = Column(Integer, primary_key=True, index=True)
    # The checkout nonce is the client-generated idempotency key for one basket.
    checkout_nonce = Column(String(100), unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="created", index=True)
    __table_args__ = (
        CheckConstraint("status IN ('created', 'sent', 'approved', 'cancelled', 'declined', 'uncertain', 'linked_to_sale')", name="ck_pos_status"),
        CheckConstraint("amount > 0", name="ck_pos_amount_positive"),
        CheckConstraint("port > 0 AND port <= 65535", name="ck_pos_port_valid"),
        CheckConstraint("resolution_type IS NULL OR resolution_type IN ('confirmed_paid', 'confirmed_cancelled', 'reversed_externally', 'duplicate', 'terminal_error', 'provider_investigation')", name="ck_pos_resolution_type"),
    )
    response_code = Column(String(20), nullable=True)
    response_label = Column(String(200), nullable=True)
    response_text = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    basket_snapshot = Column(Text, nullable=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True, unique=True)
    approval_token_hash = Column(String(64), nullable=True)
    reconciled = Column(Boolean, default=False, nullable=False)
    reconciliation_note = Column(Text, nullable=True)
    provider_reference = Column(String(100), nullable=True)
    terminal_transaction_number = Column(String(100), nullable=True)
    retrieval_reference_number = Column(String(100), nullable=True)
    masked_card = Column(String(32), nullable=True)
    request_started_at = Column(DateTime, nullable=True)
    request_finished_at = Column(DateTime, nullable=True)
    last_retry_at = Column(DateTime, nullable=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    resolution_type = Column(String(40), nullable=True)
    resolution_evidence = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    reconciled_at = Column(DateTime, nullable=True)

    customer = relationship("Customer", backref="pos_transactions")
    sale = relationship("Sale", back_populates="pos_transaction")
    checkout_session = relationship("CheckoutSession", foreign_keys="CheckoutSession.pos_transaction_id", back_populates="pos_transaction", uselist=False)
    operator_user = relationship("StaffUser", foreign_keys=[operator_user_id])


class StockMovement(Base):
    """Append-only inventory ledger entry.

    ``quantity_delta`` is positive for stock coming in and negative for stock
    leaving. The ProductVariant balance remains a cached value for fast reads,
    while this table is the audit trail and source for safe reversals.
    """
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False, index=True)
    quantity_delta = Column(Integer, nullable=False)
    movement_type = Column(String(40), nullable=False, index=True)
    unit_cost = Column(Integer, nullable=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("movement_type IN ('opening_stock', 'purchase', 'purchase_reversal', 'sale', 'sale_refund', 'adjustment', 'cost_adjustment')", name="ck_stock_movement_type"),
    )

    variant = relationship("ProductVariant", back_populates="stock_movements")


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)  # nullable for legacy data
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer, nullable=False)
    # Price at time of sale
    unit_cost = Column(Integer, default=0)
    total_price = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sale_items_unit_price_nonnegative"),
        CheckConstraint("unit_cost >= 0", name="ck_sale_items_unit_cost_nonnegative"),
        CheckConstraint("total_price >= 0", name="ck_sale_items_total_nonnegative"),
    )

    sale = relationship("Sale", back_populates="items")
    product = relationship("Product", back_populates="sale_items")
    variant = relationship("ProductVariant", back_populates="sale_items")


class Supplier(Base):
    """Wholesale supplier the shop buys stock from."""
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(15), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    purchases = relationship("Purchase", back_populates="supplier")
    checks = relationship("CheckRecord", back_populates="supplier")


class CheckRecord(Base):
    """A check issued by the store to a supplier or other provider."""
    __tablename__ = "issued_checks"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    provider_name = Column(String(200), nullable=False)
    check_number = Column(String(100), nullable=True, index=True)
    amount_rials = Column(Integer, nullable=False)
    issue_at = Column(DateTime, nullable=False)
    due_at = Column(DateTime, nullable=False, index=True)
    bank_name = Column(String(120), nullable=True)
    account_reference = Column(String(120), nullable=True)
    note = Column(Text, nullable=True)
    reminder_days = Column(Text, nullable=False, default="[14,7,3]")
    status = Column(String(20), nullable=False, default="issued", index=True)
    paid_at = Column(DateTime, nullable=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("amount_rials > 0", name="ck_issued_checks_amount_positive"),
        CheckConstraint("status IN ('issued', 'paid', 'cancelled', 'bounced')", name="ck_issued_checks_status"),
    )

    supplier = relationship("Supplier", back_populates="checks")
    operator = relationship("StaffUser")
    reminders = relationship("CheckReminder", back_populates="check", cascade="all, delete-orphan", order_by="CheckReminder.days_before.desc()")


class CheckReminder(Base):
    """Durable reminder/alarm for one issued check."""
    __tablename__ = "check_reminders"

    id = Column(Integer, primary_key=True, index=True)
    check_id = Column(Integer, ForeignKey("issued_checks.id"), nullable=False, index=True)
    days_before = Column(Integer, nullable=False)
    remind_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    triggered_at = Column(DateTime, nullable=True)
    dismissed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("days_before > 0", name="ck_check_reminders_days_positive"),
        CheckConstraint("status IN ('pending', 'triggered', 'dismissed')", name="ck_check_reminders_status"),
    )

    check = relationship("CheckRecord", back_populates="reminders")


class Purchase(Base):
    """An invoice from a supplier. Lines carry the cost, never the quantities.

    A purchase is assembled as a **draft** first: the supplier, the invoice
    details and the products it covers. Nothing economic happens until it is
    finalised — no cost basis movement, no supplier debt, no cash movement.
    """
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    total_cost = Column(Integer, default=0)
    note = Column(Text, nullable=True)
    # Draft invoices are still being assembled and are invisible to the
    # ledgers; finalising applies the cost basis and the payable exactly once.
    is_draft = Column(Boolean, nullable=False, default=False)
    is_reversed = Column(Boolean, default=False, nullable=False)
    reversed_at = Column(DateTime, nullable=True)
    amount_paid = Column(Integer, nullable=True)
    due_date = Column(DateTime, nullable=True)
    # Economic (invoice) date: reports and P&L periods key off this, while
    # created_at stays the moment the stock physically entered the shop.
    purchase_date = Column(DateTime, nullable=True)
    # Shipping / other charges the wholesaler billed on top of the item lines.
    extra_cost = Column(Integer, default=0, nullable=False)
    # Whether extra_cost is spread into the variants' cost basis (landed cost).
    extra_cost_in_landed = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    """One line of a purchase: a variant bought at a unit cost."""
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    unit_cost = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_purchase_items_quantity_nonnegative"),
        CheckConstraint("unit_cost >= 0", name="ck_purchase_items_cost_nonnegative"),
    )
    # Cost basis of the variant BEFORE this purchase was applied, so deleting
    # the purchase can restore it (stock alone is not enough to undo a purchase).
    prev_cost_price = Column(Integer, nullable=True)
    # Cost basis this line actually applied (unit cost plus its share of the
    # shipping), so reversing a purchase can restore the previous value exactly.
    landed_unit_cost = Column(Integer, nullable=True)

    purchase = relationship("Purchase", back_populates="items")
    variant = relationship("ProductVariant")
    product = relationship("Product")


class Refund(Base):
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False, index=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    total_amount = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    original_payment_reference = Column(String(120), nullable=True)
    pos_reversal_reference = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    sale = relationship("Sale")
    lines = relationship("RefundLine", cascade="all, delete-orphan")

class RefundLine(Base):
    __tablename__ = "refund_lines"
    id = Column(Integer, primary_key=True)
    refund_id = Column(Integer, ForeignKey("refunds.id"), nullable=False)
    sale_item_id = Column(Integer, ForeignKey("sale_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Integer, nullable=False)

class PaymentReversal(Base):
    __tablename__ = "payment_reversals"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False, unique=True)
    amount = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

class FinancialEntry(Base):
    __tablename__ = "financial_entries"
    id = Column(Integer, primary_key=True)
    entry_type = Column(String(40), nullable=False)
    amount = Column(Integer, nullable=False)
    refund_id = Column(Integer, ForeignKey("refunds.id"), nullable=True)
    payment_reversal_id = Column(Integer, ForeignKey("payment_reversals.id"), nullable=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=True)
    supplier_payment_id = Column(Integer, ForeignKey("supplier_payments.id"), nullable=True)
    cash_session_entry_id = Column(Integer, ForeignKey("cash_session_entries.id"), nullable=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    reference = Column(String(120), nullable=True)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

class CashSession(Base):
    """One shift of the cash drawer: opened with a count, closed with a count.

    ``cashier_user_id`` is **who opened the drawer** (whatever their role) and
    ``manager_user_id`` is **who closed it** — the field names predate the till
    belonging to the person at the counter, and the labels the panel prints say
    «بازکننده» and «بستننده» rather than reading the column name back at the
    reader. ``variance`` is ``counted − expected``, frozen when the shift closes
    so a later correction cannot silently rewrite a recorded difference.

    ``status`` is ``open``, ``closed``, or ``abandoned`` — the last written by
    the migration that enforced one open drawer, for a duplicate it had to close
    without a count.
    """
    __tablename__ = "cash_sessions"
    id = Column(Integer, primary_key=True)
    cashier_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    opening_balance = Column(Integer, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    expected_closing_balance = Column(Integer, nullable=True)
    counted_closing_balance = Column(Integer, nullable=True)
    manager_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    variance = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    entries = relationship("CashSessionEntry", back_populates="cash_session", cascade="all, delete-orphan")
    # One drawer, one shift. Two managers clicking «باز کردن صندوق» at the same
    # moment used to leave two open sessions and the register then read whichever
    # it found first; the database refuses the second one outright. Mirrored by
    # migration 19 for databases that already exist.
    __table_args__ = (
        Index("ux_cash_sessions_one_open", "status", unique=True,
              sqlite_where=text("status = 'open'")),
    )

class CashSessionEntry(Base):
    """Cash taken out of the drawer mid-shift, before the count.

    A bank deposit or a small cash purchase leaves the till without being a
    business expense, so it is its own record: it lowers what should be in the
    drawer without touching profit and loss. ``reversed_at`` undoes one that was
    entered wrongly, and only while its shift is still open — a closed shift's
    variance is frozen.
    """
    __tablename__ = "cash_session_entries"
    id = Column(Integer, primary_key=True)
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=False, index=True)
    entry_type = Column(String(30), nullable=False, default="withdrawal")
    amount = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    reversed_at = Column(DateTime, nullable=True)
    __table_args__ = (
        CheckConstraint("entry_type IN ('withdrawal')", name="ck_cash_session_entry_type"),
        CheckConstraint("amount > 0", name="ck_cash_session_entry_amount_positive"),
    )
    cash_session = relationship("CashSession", back_populates="entries")

class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    due_date = Column(DateTime, nullable=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    reversed_at = Column(DateTime, nullable=True)
    reversal_id = Column(Integer, nullable=True)
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True)
    note = Column(Text, nullable=True)
    method = Column(String(20), nullable=False, default="cash")
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_supplier_payments_amount_positive"),
        CheckConstraint("method IN ('cash', 'card')", name="ck_supplier_payments_method"),
    )

class Expense(Base):
    """A shop expense (rent, utilities, wages…) — cash leaving the business."""
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Integer, nullable=False)
    category = Column(String(100), nullable=True)
    expense_type = Column(String(20), nullable=False, default="one_time")
    payment_method = Column(String(20), nullable=False, default="cash")
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True)
    reversed_at = Column(DateTime, nullable=True)
    reversal_id = Column(Integer, nullable=True)
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("expense_type IN ('one_time', 'monthly')", name="ck_expenses_type"),
        CheckConstraint("payment_method IN ('cash', 'card')", name="ck_expenses_payment_method"),
    )
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SalaryPayment(Base):
    """A monthly wage payment linked to its expense and cash movement."""
    __tablename__ = "salary_payments"

    id = Column(Integer, primary_key=True, index=True)
    staff_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False, index=True)
    period_key = Column(String(20), nullable=False)
    gross_amount = Column(Integer, nullable=False)
    deductions = Column(Integer, nullable=False, default=0)
    net_amount = Column(Integer, nullable=False)
    payment_method = Column(String(20), nullable=False, default="cash")
    paid_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False, unique=True)
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("staff_user_id", "period_key", name="uq_salary_payments_staff_period"),
        CheckConstraint("gross_amount > 0", name="ck_salary_gross_positive"),
        CheckConstraint("deductions >= 0 AND deductions < gross_amount", name="ck_salary_deductions_valid"),
        CheckConstraint("net_amount > 0", name="ck_salary_net_positive"),
        CheckConstraint("payment_method IN ('cash', 'card')", name="ck_salary_payment_method"),
    )

    staff_user = relationship("StaffUser", foreign_keys=[staff_user_id], back_populates="salary_payments")
    operator = relationship("StaffUser", foreign_keys=[operator_user_id])
    expense = relationship("Expense")
    cash_session = relationship("CashSession")


class Payment(Base):
    """A customer payment toward their نسیه debt."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    cash_session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True)
    # Who took the money. The CreditPaymentRecorded event already names the
    # operator; this puts the same fact on the row so the receipt history can
    # show it without replaying the journal.
    received_by_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    method = Column(String(20), default="cash")
    reversed_at = Column(DateTime, nullable=True)
    reversal_id = Column(Integer, nullable=True)
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint("method IN ('cash', 'card')", name="ck_payments_method"),
    )  # cash / card
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer")
    sale = relationship("Sale")
    received_by = relationship("StaffUser", foreign_keys=[received_by_id])


class AdminLog(Base):
    """Audit trail of authenticated staff actions."""
    __tablename__ = "admin_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50), nullable=False)
    detail = Column(Text, nullable=True)
    staff_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True, index=True)
    target_type = Column(String(50), nullable=True)
    target_id = Column(Integer, nullable=True)
    ip_address = Column(String(64), nullable=True)
    request_id = Column(String(100), nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    staff_user = relationship("StaffUser")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    discount_percent = Column(Integer, nullable=False)
    min_purchase = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    # A reusable campaign is a standing promo: it applies to every qualifying
    # invoice while it is live. The default (once) burns on the first invoice,
    # which is what most SMS promos mean.
    is_reusable = Column(Boolean, default=False)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sale_campaigns = relationship("SaleCampaign", back_populates="campaign")
    assignments = relationship("CampaignAssignment", back_populates="campaign",
                               cascade="all, delete-orphan")


class SaleCampaign(Base):
    __tablename__ = "sale_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    discount_amount = Column(Integer, default=0)

    sale = relationship("Sale", back_populates="campaigns")
    campaign = relationship("Campaign", back_populates="sale_campaigns")


class CampaignAssignment(Base):
    """A customer holding a campaign.

    This is the row that makes a campaign visible: the customers page shows the
    badge from it, the counter sees whose discount to give, and the campaign
    page knows who was invited and who actually came. States:
    'invited' → on the send list, discount offered at the counter but not yet
    used; 'used' → redeemed on ``sale_id``; 'removed' → deliberately taken off.
    ``is_reusable`` campaigns are not burned by a redemption.
    """
    __tablename__ = "campaign_assignments"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="invited")
    # How the customer got it: 'sms' (the campaign blast), 'manual' (added from
    # the profile or the campaign page) or 'checkout' (a code applied at the
    # counter, their first evidence of the campaign).
    source = Column(String(20), nullable=False, default="manual")
    invited_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    # When the campaign SMS was actually queued for this customer. This is the
    # re-send guard: a blast skips anyone who already carries it, so a
    # double-click can never message the same person twice.
    invite_sent_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    used_count = Column(Integer, nullable=False, default=0)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "customer_id", name="uq_campaign_assignment"),
        CheckConstraint("status IN ('invited', 'used', 'removed')", name="ck_campaign_assignment_status"),
    )

    campaign = relationship("Campaign", back_populates="assignments")
    customer = relationship("Customer")
    sale = relationship("Sale")


# ── پیامک: templates and the log of everything the shop has sent ─────────────

SMS_CATEGORIES = ("welcome", "birthday", "tier_up", "campaign", "credit_reminder", "custom")
SMS_MESSAGE_STATUSES = ("queued", "sent", "failed")

# The journey of one message through the on-premise gateway: the store queues
# it, the phone claims it, the phone reports the send, and (where the carrier
# confirms it) the delivery note arrives last. None of these replace ``status``
# — they ride on it, so the existing three-state log keeps its meaning.
SMS_DELIVERY_STATES = ("", "claimed", "delivered", "undelivered")


class SmsDevice(Base):
    """The one phone that sends the shop's messages.

    The APK polls :8101 with the key below, claims waiting messages, reports the
    send, and later the delivery. ``api_key`` is stored **hashed** — the raw
    value is shown exactly once, in the pairing QR on the پیامک page, and the
    phone keeps its own copy in its SharedPreferences.
    """
    __tablename__ = "sms_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, default="گوشی فروشگاه")
    phone = Column(String(20), nullable=True)
    api_key_hash = Column(String(255), nullable=False, default="")
    status = Column(String(20), nullable=False, default="never_connected")
    battery_level = Column(Integer, nullable=True)
    signal_strength = Column(Integer, nullable=True)
    app_version = Column(String(40), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('online', 'offline', 'never_connected')",
                        name="ck_sms_device_status"),
    )
SMS_MESSAGE_KINDS = ("marketing", "transactional")
SMS_MESSAGE_SOURCES = ("manual", "welcome", "birthday", "tier_up", "campaign",
                       "credit_reminder", "test")


class SmsTemplate(Base):
    """One message the shop can send, and who fills its blanks.

    The six built-in templates mirror the legacy ``settings`` rows
    (``sms_pattern_*``) through ``setting_key``: saving one writes the effective
    body back to that row, so the code that already reads those keys keeps
    working untouched and «غیرفعال» simply mirrors an empty pattern — exactly
    what an empty pattern has always meant here.

    ``variables`` is JSON: ``[{"token", "label", "sample", "field"}]`` where
    ``field`` optionally names a real customer field used to fill the token for
    a given recipient.
    """
    __tablename__ = "sms_templates"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(30), nullable=False, default="custom")
    body = Column(Text, nullable=False, default="")
    variables = Column(Text, nullable=False, default="[]")
    # Where the owner last wrote this text, for the built-ins only.
    setting_key = Column(String(50), nullable=True)
    # What fires this message *without a click*. Empty means hand-sent, which is
    # where every template starts: the built-ins already have their own senders
    # and a custom one has to be opted in on the editor before it leaves alone.
    # The vocabulary lives in ``services.sms_templates.TRIGGERS``; ``trigger_days``
    # is the wait the «چند روز است خرید نکرده» trigger uses.
    trigger_key = Column(String(30), nullable=False, default="")
    trigger_days = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('welcome', 'birthday', 'tier_up', 'campaign', 'credit_reminder', 'custom')",
                        name="ck_sms_template_category"),
    )


class SmsMessage(Base):
    """What was sent, to whom, and what the queue did with it.

    The body is rendered **when the message is queued**, not when it is read, so
    editing a template later can never rewrite the history of what a customer
    actually received.
    """
    __tablename__ = "sms_messages"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("sms_templates.id"), nullable=True)
    template_key = Column(String(50), nullable=True)
    template_name = Column(String(200), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    employee_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("background_jobs.id"), nullable=True, index=True)
    phone = Column(String(20), nullable=False)
    body = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="queued", index=True)
    kind = Column(String(20), nullable=False, default="transactional")
    source = Column(String(30), nullable=False, default="manual", index=True)
    error = Column(Text, nullable=True)
    # What this message was about, for an automatic send: ``sale:12`` for a
    # purchase trigger, ``purchase:2026-09-01`` for a follow-up. It is the guard
    # that keeps a trigger from messaging the same person about the same thing
    # twice, and it is visible in the history like everything else.
    ref = Column(String(60), nullable=False, default="", index=True)
    # Which customer values the body was rendered *from*, as the shop's own
    # labels rather than the template's placeholder names:
    # ``[{"token": "var1", "label": "نام مشتری", "value": "سارا"}]``.
    #
    # The frozen body says what went out; this says what it was built out of, so
    # an old message can be read, replayed and audited years later. It carries
    # its own labels because the template may be edited or deleted afterwards —
    # the same reason the body is frozen in the first place. ``""`` means the row
    # predates this column; ``"[]"`` means it was recorded and there was nothing
    # to record (the text used no placeholders).
    values_json = Column(Text, nullable=False, default="")
    # Gateway journey columns. ``claimed_at`` is when the phone took the
    # message; ``delivery_state`` is the carrier's verdict where it exists.
    # ``attempts`` counts how many times a claim was handed out, so a phone
    # that died mid-send cannot wedge a message forever.
    delivery_state = Column(String(20), nullable=False, default="")
    claimed_at = Column(DateTime, nullable=True)
    sent_by_device_id = Column(Integer, ForeignKey("sms_devices.id"), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    sent_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'sent', 'failed')", name="ck_sms_message_status"),
        CheckConstraint("kind IN ('marketing', 'transactional')", name="ck_sms_message_kind"),
        # There is deliberately no CHECK on ``source``. It was frozen into the
        # table at creation time, so a database created before a new sender
        # existed *silently rejected* its rows — the same trap revision 15 had to
        # rebuild ``business_events`` to escape. The vocabulary is
        # ``services.sms_templates.SOURCE_LABELS`` and every writer goes through
        # ``log_message``, which refuses a source it does not recognise.
        CheckConstraint("delivery_state IN ('', 'claimed', 'delivered', 'undelivered')",
                        name="ck_sms_message_delivery_state"),
    )

    template = relationship("SmsTemplate")
    customer = relationship("Customer")
    device = relationship("SmsDevice")


# ── Checkout concurrency: server-owned drafts, reservations, state history ────

CHECKOUT_STATES = (
    "draft",
    "reserved",
    "payment_pending",
    "payment_approved",
    "payment_cancelled",
    "payment_declined",
    "payment_uncertain",
    "completed",
    "refunded",
    "expired",
)

# Valid forward transitions. Each state maps to the set of states it may move to.
CHECKOUT_TRANSITIONS = {
    "draft": {"reserved", "payment_pending", "expired"},
    "reserved": {"payment_pending", "completed", "draft", "expired"},
    "payment_pending": {"payment_approved", "payment_cancelled",
                        "payment_declined", "payment_uncertain", "expired"},
    "payment_approved": {"completed", "expired"},
    "payment_cancelled": {"draft", "expired"},
    "payment_declined": {"draft", "expired"},
    "payment_uncertain": {"payment_declined", "payment_cancelled", "expired"},
    "completed": {"refunded"},
    "refunded": set(),
    "expired": set(),
}


class CheckoutSession(Base):
    """Server-owned checkout draft. The basket, customer, discounts, and final
    amount are computed here so the browser can never submit a mismatched
    amount to the terminal and later confirm a different basket."""
    __tablename__ = "checkout_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # The nonce is the idempotency key shared with the POS terminal request.
    checkout_nonce = Column(String(100), unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    staff_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True)
    basket_json = Column(Text, nullable=True)
    total_amount = Column(Integer, nullable=False, default=0)
    discount_amount = Column(Integer, nullable=False, default=0)
    credit_surcharge = Column(Integer, nullable=False, default=0)
    final_amount = Column(Integer, nullable=False, default=0)
    payment_method = Column(String(20), nullable=False, default="card")
    use_referrer_discount = Column(Boolean, nullable=False, default=True)
    custom_discount_amount = Column(Integer, nullable=False, default=0)
    custom_discount_percent = Column(Integer, nullable=False, default=0)
    referrer_code = Column(String(50), nullable=True)
    referrer_phone = Column(String(20), nullable=True)
    # The campaign code typed at the counter. Kept on the server-owned draft so
    # ``finalize_basket`` recomputes the very same discount the cashier saw.
    campaign_code = Column(String(50), nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    state = Column(String(30), nullable=False, default="draft", index=True)
    pos_transaction_id = Column(Integer, ForeignKey("pos_transactions.id"), nullable=True, unique=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint(f"state IN {CHECKOUT_STATES!r}", name="ck_checkout_state"),
        CheckConstraint("total_amount >= 0", name="ck_checkout_total_nonnegative"),
        CheckConstraint("discount_amount >= 0", name="ck_checkout_discount_nonnegative"),
        CheckConstraint("credit_surcharge >= 0", name="ck_checkout_surcharge_nonnegative"),
        CheckConstraint("final_amount >= 0", name="ck_checkout_final_nonnegative"),
        CheckConstraint("custom_discount_amount >= 0", name="ck_checkout_discount_amount_nonnegative"),
        CheckConstraint("custom_discount_percent >= 0 AND custom_discount_percent <= 100", name="ck_checkout_discount_percent_valid"),
        CheckConstraint("payment_method IN ('card', 'cash', 'credit')", name="ck_checkout_payment_method"),
    )

    customer = relationship("Customer")
    staff_user = relationship("StaffUser")
    pos_transaction = relationship("POSTransaction", foreign_keys=[pos_transaction_id], uselist=False, back_populates="checkout_session")
    sale = relationship("Sale", foreign_keys=[sale_id], uselist=False)
    history = relationship("CheckoutEvent", back_populates="checkout",
                           order_by="CheckoutEvent.id", cascade="all, delete-orphan")


class StockReservation(Base):
    """Temporary hold on variant stock for an active checkout. Expires after
    RESERVATION_TIMEOUT_MINUTES so a cashier cannot hold the last item
    indefinitely while another checkout attempts to sell it."""
    __tablename__ = "stock_reservations"

    id = Column(Integer, primary_key=True, index=True)
    checkout_session_id = Column(Integer, ForeignKey("checkout_sessions.id"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    session_id = Column(String(100), nullable=True)
    state = Column(String(20), nullable=False, default="active", index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    released_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        CheckConstraint("state IN ('active', 'consumed', 'released')", name="ck_reservation_state"),
    )

    checkout_session = relationship("CheckoutSession")
    variant = relationship("ProductVariant")


class CheckoutEvent(Base):
    """Append-only state-history row for a checkout session."""
    __tablename__ = "checkout_events"

    id = Column(Integer, primary_key=True, index=True)
    checkout_session_id = Column(Integer, ForeignKey("checkout_sessions.id"), nullable=False, index=True)
    from_state = Column(String(30), nullable=True)
    to_state = Column(String(30), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    checkout = relationship("CheckoutSession", back_populates="history")


BUSINESS_EVENT_TYPES = (
    "CheckoutCreated",
    "StockReserved",
    "StockReleased",
    "StockReservationConsumed",
    "StockDecremented",
    "StockReceived",
    "StockReturned",
    "StockAdjusted",
    "StockCostAdjusted",
    "PaymentRequested",
    "PaymentApproved",
    "PaymentCancelled",
    "PaymentDeclined",
    "PaymentUncertain",
    "POSReconciled",
    "CheckoutExpired",
    "SaleCompleted",
    "RefundIssued",
    "CheckoutRefunded",
    "TagBatchPrinted",
    "CampaignRedeemed",
    "CreditSaleIssued",
    "CreditPaymentRecorded",
    "PaymentReversed",
    "ExpenseRecorded",
    "ExpenseReversed",
    "PurchaseRecorded",
    "PurchaseReversed",
    "SupplierPaymentRecorded",
    "SalaryPaid",
    "LoyaltyUpdated",
    "CashSessionOpened",
    "CashSessionClosed",
    "CashSessionEntryRecorded",
    "CashSessionEntryReversed",
    "CheckIssued",
    "CheckPaid",
    "CheckCancelled",
    "CheckBounced",
    "CheckReminderTriggered",
    "DatabaseReset",
)


class BusinessEvent(Base):
    """Append-only record of a committed retail business action."""
    __tablename__ = "business_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(60), nullable=False, index=True)
    aggregate_type = Column(String(50), nullable=False, index=True)
    aggregate_id = Column(Integer, nullable=True, index=True)
    idempotency_key = Column(String(200), nullable=False, unique=True, index=True)
    actor_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=True, index=True)
    request_id = Column(String(100), nullable=True, index=True)
    payload = Column(Text, nullable=False, default="{}")
    schema_version = Column(Integer, nullable=False, default=1)
    occurred_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Event types are validated in code (append_event) rather than as a DB CHECK
    # constraint: the constraint was baked into the table at creation time and
    # silently rejected event types added by later releases.
    __table_args__ = (
        CheckConstraint("aggregate_id IS NULL OR aggregate_id > 0", name="ck_business_events_aggregate_id"),
        CheckConstraint("schema_version > 0", name="ck_business_events_schema_version"),
    )

    actor_user = relationship("StaffUser")


@sqlalchemy_event.listens_for(BusinessEvent, "before_update")
def _reject_business_event_update(mapper, connection, target):
    raise ValueError("Business events are append-only")


@sqlalchemy_event.listens_for(BusinessEvent, "before_delete")
def _reject_business_event_delete(mapper, connection, target):
    raise ValueError("Business events are append-only")


class TagPrintBatch(Base):
    """Auditable record of one physical tag-print operation."""
    __tablename__ = "tag_print_batches"

    id = Column(Integer, primary_key=True, index=True)
    operator_user_id = Column(Integer, ForeignKey("staff_users.id"), nullable=False, index=True)
    template_snapshot = Column(Text, nullable=False, default="{}")
    item_count = Column(Integer, nullable=False, default=0)
    total_quantity = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    operator = relationship("StaffUser")
    lines = relationship("TagPrintBatchLine", back_populates="batch", cascade="all, delete-orphan", order_by="TagPrintBatchLine.id")


class TagPrintBatchLine(Base):
    """Snapshot of each variant and quantity in a printed tag batch."""
    __tablename__ = "tag_print_batch_lines"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("tag_print_batches.id"), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    product_name = Column(String(200), nullable=False)
    barcode = Column(String(50), nullable=False)
    sku = Column(String(50), nullable=True)
    size = Column(String(20), nullable=True)
    color = Column(String(50), nullable=True)
    unit_price = Column(Integer, nullable=False, default=0)
    is_reprint = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_tag_print_line_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_tag_print_line_price_nonnegative"),
    )

    batch = relationship("TagPrintBatch", back_populates="lines")
    variant = relationship("ProductVariant")
