import string
import random
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, CheckConstraint
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


class StaffUser(Base):
    """An authenticated store staff member with an explicit role."""
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="cashier", index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('cashier', 'manager', 'owner')", name="ck_staff_users_role"),
    )


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
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Single image for list views; variants can have their own images.
    image_path = Column(String(500), nullable=True)

    # Relationships
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")
    images = relationship("ProductImage", back_populates="product", order_by="ProductImage.sort_order", cascade="all, delete-orphan")
    sale_items = relationship("SaleItem", back_populates="product")

    @property
    def total_stock(self):
        return sum(v.stock_quantity for v in self.variants if v.is_active)

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
        CheckConstraint("reserved_quantity >= 0", name="ck_variants_reserved_nonnegative"),
        CheckConstraint("demand_count >= 0", name="ck_variants_demand_nonnegative"),
    )

    product = relationship("Product", back_populates="variants")
    sale_items = relationship("SaleItem", back_populates="variant")
    stock_movements = relationship("StockMovement", back_populates="variant", order_by="StockMovement.created_at", cascade="all, delete-orphan")

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
    points_earned = Column(Integer, default=0)
    is_refunded = Column(Boolean, default=False)
    refund_amount = Column(Integer, default=0)
    refund_reason = Column(Text, nullable=True)
    refund_date = Column(DateTime, nullable=True)
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    reconciled_at = Column(DateTime, nullable=True)

    customer = relationship("Customer", backref="pos_transactions")
    sale = relationship("Sale", back_populates="pos_transaction")
    checkout_session = relationship("CheckoutSession", foreign_keys="CheckoutSession.pos_transaction_id", back_populates="pos_transaction", uselist=False)


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


class Purchase(Base):
    """A stock purchase from a supplier. Items update variant stock + cost."""
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    total_cost = Column(Integer, default=0)
    note = Column(Text, nullable=True)
    is_reversed = Column(Boolean, default=False, nullable=False)
    reversed_at = Column(DateTime, nullable=True)
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

    purchase = relationship("Purchase", back_populates="items")
    variant = relationship("ProductVariant")
    product = relationship("Product")


class Expense(Base):
    """A shop expense (rent, utilities, wages…) — cash leaving the business."""
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Integer, nullable=False)
    category = Column(String(100), nullable=True)
    __table_args__ = (CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Payment(Base):
    """A customer payment toward their نسیه debt."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    method = Column(String(20), default="cash")
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint("method IN ('cash', 'card')", name="ck_payments_method"),
    )  # cash / card
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer")
    sale = relationship("Sale")


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
