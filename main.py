import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET
from database import engine, Base, SessionLocal
from routers import customers, api, admin, products, sales, analytics, campaigns, accounting
from routers.clothes_images import admin_router as clothes_admin_router, api_router as clothes_api_router
from services.security import CSRFMiddleware
from services.store import get_store
from services.scheduler import scheduler_task
from migrations import upgrade


def _seed_legacy_stock_movements():
    """Create an opening-balance movement for pre-ledger inventory.

    Existing databases already contain the cached balance; seeding that balance
    exactly once prevents the new ledger from starting at zero and preserves
    future auditability without duplicating old purchase quantities.
    """
    from models import ProductVariant, StockMovement

    db = SessionLocal()
    try:
        for variant in db.query(ProductVariant).all():
            if db.query(StockMovement).filter(StockMovement.variant_id == variant.id).first():
                continue
            if (variant.stock_quantity or 0) > 0:
                db.add(StockMovement(
                    variant_id=variant.id,
                    quantity_delta=variant.stock_quantity,
                    movement_type="opening_stock",
                    unit_cost=variant.cost_price,
                    note="موجودی اولیه قبل از فعال‌سازی دفتر انبار",
                ))
        db.commit()
    finally:
        db.close()


def _migrate_unknown_customers():
    """One-time: detach sales from the legacy 'johndoe(unknown)' customer and
    delete the row. Anonymous sales now live with customer_id=NULL. Idempotent —
    no-op once the row is gone."""
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM customers WHERE phone='johndoe(unknown)'")).fetchone()
        if row:
            conn.execute(text("UPDATE sales SET customer_id=NULL WHERE customer_id=:cid"), {"cid": row[0]})
            conn.execute(text("DELETE FROM customers WHERE id=:cid"), {"cid": row[0]})


def _apply_missing_columns(migration_engine=None):
    """Additive migration: ALTER TABLE for columns that exist in models but not in the DB.
    SQLite-specific additive migration that scans each known table."""
    migration_engine = migration_engine or engine
    insp = inspect(migration_engine)
    table_to_cols = {
        "customers": [
            ("child_photo_path", "VARCHAR(500)"),
            ("total_debt", "INTEGER"),
            ("credit_limit", "INTEGER"),
        ],
        "product_variants": [
            ("reserved_quantity", "INTEGER DEFAULT 0"),
            ("fake_cost_price", "INTEGER"),
            ("tryon_details", "TEXT"),
        ],
        "sales": [
            ("credit_settled", "BOOLEAN"),
            ("credit_paid_amount", "INTEGER"),
            ("credit_surcharge", "INTEGER"),
        ],
        "purchases": [
            ("is_reversed", "BOOLEAN DEFAULT 0"),
            ("reversed_at", "DATETIME"),
        ],
        "purchase_items": [
            ("prev_cost_price", "INTEGER"),
        ],
        "checkout_sessions": [
            ("use_referrer_discount", "BOOLEAN DEFAULT 1"),
            ("custom_discount_amount", "INTEGER DEFAULT 0"),
            ("custom_discount_percent", "INTEGER DEFAULT 0"),
            ("referrer_code", "VARCHAR(50)"),
            ("referrer_phone", "VARCHAR(20)"),
        ],
        "pos_transactions": [
            ("provider_reference", "VARCHAR(100)"),
            ("terminal_transaction_number", "VARCHAR(100)"),
            ("retrieval_reference_number", "VARCHAR(100)"),
            ("masked_card", "VARCHAR(32)"),
            ("request_started_at", "DATETIME"),
            ("request_finished_at", "DATETIME"),
            ("last_retry_at", "DATETIME"),
            ("operator_user_id", "INTEGER"),
            ("resolution_type", "VARCHAR(40)"),
            ("resolution_evidence", "TEXT"),
        ],
        "admin_logs": [
            ("staff_user_id", "INTEGER"),
            ("target_type", "VARCHAR(50)"),
            ("target_id", "INTEGER"),
            ("ip_address", "VARCHAR(64)"),
            ("request_id", "VARCHAR(100)"),
            ("before_json", "TEXT"),
            ("after_json", "TEXT"),
        ],
    }
    with migration_engine.begin() as conn:
        for table, cols in table_to_cols.items():
            if not insp.has_table(table):
                continue
            existing = {row["name"] for row in insp.get_columns(table)}
            for col_name, col_type in cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    upgrade(engine)
    _apply_missing_columns()
    _migrate_unknown_customers()
    _seed_legacy_stock_movements()
    # Seed the admin password hash and migrate the legacy single-admin account.
    db = SessionLocal()
    try:
        from services.security import ensure_owner_account
        ensure_owner_account(db)
    finally:
        db.close()
    scheduler = asyncio.create_task(scheduler_task())
    yield
    scheduler.cancel()
    try:
        await scheduler
    except asyncio.CancelledError:
        pass


app = FastAPI(title="سیستم فروش", lifespan=lifespan)

# Middleware order matters: SessionMiddleware must run OUTSIDE CSRFMiddleware so
# the session (and its CSRF token) is available when CSRF validates. Starlette
# wraps in reverse order of add_middleware, so CSRF is added first.
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=12 * 3600,          # 12h admin session
    same_site="strict",
    https_only=False,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(customers.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(products.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(campaigns.router)
app.include_router(accounting.router)
app.include_router(clothes_admin_router)
app.include_router(clothes_api_router)
