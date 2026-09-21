import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import inspect, text
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET
from database import engine, Base, SessionLocal
from routers import customers, api, admin, products, sales, analytics, campaigns, accounting, sms
from routers.clothes_images import admin_router as clothes_admin_router, api_router as clothes_api_router
from routers.sms_device import gateway_app as _sms_gateway_app  # noqa: F401  (supervised on :8101 by desktop_entry)
from services.security import CSRFMiddleware, PERMISSION_DENIED_DETAIL
from services.store import get_store
from services.scheduler import scheduler_task
from services.templating import templates
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


def _backfill_buys_for():
    """One-time: pin each existing customer's «for whom» from the data on file.

    Rows added before the choice existed have a NULL `buys_for` and fall back to
    the store's target. Backfilling the ones that actually hold a child profile
    to 'child' (and ones holding only their own birthday to 'self') freezes the
    behaviour they already have, so changing the store default later can never
    silently move an existing customer's birthday discount. Rows with neither
    birthday are left NULL — nothing can move, because there is nothing to move.
    Idempotent: it only ever touches NULL rows.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE customers SET buys_for='child' "
            "WHERE buys_for IS NULL AND (child_birthday IS NOT NULL OR child_name IS NOT NULL)"
        ))
        conn.execute(text(
            "UPDATE customers SET buys_for='self' "
            "WHERE buys_for IS NULL AND birth_month_day IS NOT NULL "
            "AND child_birthday IS NULL AND child_name IS NULL"
        ))


def _apply_missing_columns(migration_engine=None):
    """Additive migration: ALTER TABLE for columns that exist in models but not in the DB.
    SQLite-specific additive migration that scans each known table."""
    migration_engine = migration_engine or engine
    insp = inspect(migration_engine)
    table_to_cols = {
        "staff_users": [
            ("full_name", "VARCHAR(200)"),
            ("employee_code", "VARCHAR(50)"),
            ("national_id", "VARCHAR(30)"),
            ("phone", "VARCHAR(30)"),
            ("email", "VARCHAR(150)"),
            ("job_title", "VARCHAR(100)"),
            ("employment_type", "VARCHAR(20) DEFAULT 'full_time'"),
            ("hire_date", "DATETIME"),
            ("birth_date", "DATETIME"),
            ("contract_end_date", "DATETIME"),
            ("education", "VARCHAR(200)"),
            ("work_schedule", "VARCHAR(200)"),
            ("salary_payment_day", "INTEGER"),
            ("address", "TEXT"),
            ("emergency_contact", "VARCHAR(200)"),
            ("bank_account", "VARCHAR(80)"),
            ("iban", "VARCHAR(40)"),
            ("salary_amount", "INTEGER DEFAULT 0"),
            ("notes", "TEXT"),
        ],
        "salary_payments": [
            ("staff_user_id", "INTEGER"),
            ("period_key", "VARCHAR(20)"),
            ("gross_amount", "INTEGER"),
            ("deductions", "INTEGER DEFAULT 0"),
            ("net_amount", "INTEGER"),
            ("payment_method", "VARCHAR(20) DEFAULT 'cash'"),
            ("paid_at", "DATETIME"),
            ("operator_user_id", "INTEGER"),
            ("expense_id", "INTEGER"),
            ("cash_session_id", "INTEGER"),
            ("note", "TEXT"),
            ("created_at", "DATETIME"),
        ],
        "customers": [
            ("child_photo_path", "VARCHAR(500)"),
            ("total_debt", "INTEGER"),
            ("credit_limit", "INTEGER"),
            # Additive: existing rows keep their meaning. SMS consent and the
            # archive flag default to the behaviour they already had (opted in,
            # not archived) rather than to NULL.
            ("birth_month_day", "VARCHAR(5)"),
            ("birth_year", "INTEGER"),
            ("notes", "TEXT"),
            ("tags", "VARCHAR(200) DEFAULT ''"),
            ("sms_opt_in", "INTEGER DEFAULT 1"),
            ("is_archived", "INTEGER DEFAULT 0"),
            ("child_birth_year", "INTEGER"),
            # Whose clothes the customer buys. Added as NULL (never chosen) and
            # then backfilled below from the data each row already has, so no
            # existing profile changes the birthday its discount uses.
            ("buys_for", "VARCHAR(8)"),
        ],
        "products": [
            ("base_sku", "VARCHAR(50)"),
            ("base_barcode", "VARCHAR(50)"),
            ("weight_grams", "INTEGER"),
            ("garment_type", "VARCHAR(80)"),
            ("gender", "VARCHAR(30)"),
            ("material", "VARCHAR(120)"),
            ("season", "VARCHAR(50)"),
            ("collection", "VARCHAR(100)"),
            ("care_instructions", "TEXT"),
            ("supplier_id", "INTEGER"),
            ("default_reorder_point", "INTEGER DEFAULT 0"),
            ("default_reorder_quantity", "INTEGER DEFAULT 0"),
            ("tag_template_id", "INTEGER"),
        ],
        "product_variants": [
            ("reserved_quantity", "INTEGER DEFAULT 0"),
            ("fake_cost_price", "INTEGER"),
            ("tryon_details", "TEXT"),
            ("reorder_point", "INTEGER DEFAULT 0"),
            ("reorder_quantity", "INTEGER DEFAULT 0"),
            ("storage_location", "VARCHAR(100)"),
            ("size_system", "VARCHAR(30)"),
            ("color_code", "VARCHAR(30)"),
        ],
        "sales": [
            ("credit_settled", "BOOLEAN"),
            ("credit_paid_amount", "INTEGER"),
            ("credit_surcharge", "INTEGER"),
            ("cash_session_id", "INTEGER"),
            # سررسید a نسیه invoice is due. Left NULL on existing rows on
            # purpose: they keep being aged by their own date, so no debtor
            # changes bucket just because the column appeared.
            ("credit_due_date", "DATETIME"),
        ],
        "purchases": [
            # Existing invoices are already final, so the added column defaults
            # to 0 and only new drafts are marked.
            ("is_draft", "BOOLEAN NOT NULL DEFAULT 0"),
            ("is_reversed", "BOOLEAN DEFAULT 0"),
            ("reversed_at", "DATETIME"),
            ("amount_paid", "INTEGER DEFAULT 0"),
            ("due_date", "DATETIME"),
            ("purchase_date", "DATETIME"),
            ("extra_cost", "INTEGER DEFAULT 0"),
            ("extra_cost_in_landed", "BOOLEAN DEFAULT 1"),
        ],
        "purchase_items": [
            ("prev_cost_price", "INTEGER"),
            ("landed_unit_cost", "INTEGER"),
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
        "expenses": [
            ("expense_type", "VARCHAR(20) NOT NULL DEFAULT 'one_time'"),
            ("reversed_at", "DATETIME"),
            ("reversal_id", "INTEGER"),
            ("payment_method", "VARCHAR(20) DEFAULT 'cash'"),
            ("cash_session_id", "INTEGER"),
        ],
        "supplier_payments": [
            ("cash_session_id", "INTEGER"),
            ("note", "TEXT"),
            ("method", "VARCHAR(20) DEFAULT 'cash'"),
            ("reversed_at", "DATETIME"),
            ("reversal_id", "INTEGER"),
        ],
        "payments": [
            ("received_by_id", "INTEGER"),
            ("cash_session_id", "INTEGER"),
            ("reversed_at", "DATETIME"),
            ("reversal_id", "INTEGER"),
        ],
        "refunds": [
            ("cash_session_id", "INTEGER"),
        ],
        "background_jobs": [
            ("job_type", "VARCHAR(40)"),
            ("payload", "TEXT"),
            ("status", "VARCHAR(20) DEFAULT 'pending'"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("next_retry_at", "DATETIME"),
            ("error_message", "TEXT"),
            ("locked_at", "DATETIME"),
            ("completed_at", "DATETIME"),
            ("result_path", "VARCHAR(500)"),
            ("result_url", "VARCHAR(500)"),
            ("created_at", "DATETIME"),
        ],
        "issued_checks": [
            ("supplier_id", "INTEGER"),
            ("provider_name", "VARCHAR(200)"),
            ("check_number", "VARCHAR(100)"),
            ("amount_rials", "INTEGER"),
            ("issue_at", "DATETIME"),
            ("due_at", "DATETIME"),
            ("bank_name", "VARCHAR(120)"),
            ("account_reference", "VARCHAR(120)"),
            ("note", "TEXT"),
            ("reminder_days", "TEXT DEFAULT '[14,7,3]'"),
            ("status", "VARCHAR(20) DEFAULT 'issued'"),
            ("paid_at", "DATETIME"),
            ("operator_user_id", "INTEGER"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
        ],
        "check_reminders": [
            ("check_id", "INTEGER"),
            ("days_before", "INTEGER"),
            ("remind_at", "DATETIME"),
            ("status", "VARCHAR(20) DEFAULT 'pending'"),
            ("triggered_at", "DATETIME"),
            ("dismissed_at", "DATETIME"),
        ],
        "tag_print_batches": [
            ("operator_user_id", "INTEGER"),
            ("template_snapshot", "TEXT DEFAULT '{}'"),
            ("item_count", "INTEGER DEFAULT 0"),
            ("total_quantity", "INTEGER DEFAULT 0"),
            ("created_at", "DATETIME"),
        ],
        "tag_print_batch_lines": [
            ("batch_id", "INTEGER"),
            ("variant_id", "INTEGER"),
            ("quantity", "INTEGER DEFAULT 1"),
            ("product_name", "VARCHAR(200)"),
            ("barcode", "VARCHAR(50)"),
            ("sku", "VARCHAR(50)"),
            ("size", "VARCHAR(20)"),
            ("color", "VARCHAR(50)"),
            ("unit_price", "INTEGER DEFAULT 0"),
            ("is_reprint", "BOOLEAN DEFAULT 0"),
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
        "campaigns": [
            ("is_reusable", "BOOLEAN DEFAULT 0"),
        ],
        "checkout_sessions": [
            ("campaign_code", "VARCHAR(50)"),
            ("campaign_id", "INTEGER"),
        ],
        "sms_messages": [
            # The gateway journey. ``delivery_state`` starts as '' (nothing
            # known beyond the queue) so existing rows keep their meaning.
            ("delivery_state", "VARCHAR(20) DEFAULT ''"),
            ("claimed_at", "DATETIME"),
            ("sent_by_device_id", "INTEGER"),
            ("attempts", "INTEGER DEFAULT 0"),
            # Which event an automatic message belongs to; '' for everything
            # sent by hand, so a shop's existing history keeps its meaning.
            ("ref", "VARCHAR(60) DEFAULT ''"),
            # The customer values the body was rendered from, as a JSON list.
            # '' on the rows that predate it, so an old message is never made to
            # look as if it was built out of nothing.
            ("values_json", "TEXT DEFAULT ''"),
        ],
        "sms_templates": [
            # Empty trigger = hand-sent, which is what every existing template
            # is. A shop upgrades into exactly the behaviour it already had.
            ("trigger_key", "VARCHAR(30) DEFAULT ''"),
            ("trigger_days", "INTEGER DEFAULT 0"),
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
    _backfill_buys_for()
    from services.operations import validate_production_config
    configuration_errors = validate_production_config()
    if configuration_errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(configuration_errors))
    _migrate_unknown_customers()
    _seed_legacy_stock_movements()
    # Seed the admin password hash and migrate the legacy single-admin account.
    db = SessionLocal()
    try:
        from services.security import ensure_owner_account
        ensure_owner_account(db)
        from services.events import backfill_legacy_events
        backfill_legacy_events(db)
        # Built-in SMS templates mirror the legacy settings rows, so seeding them
        # at boot changes nothing a shop already had configured.
        from services.sms_templates import ensure_seeded as seed_sms_templates
        seed_sms_templates(db)
        db.commit()
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

# ── a refusal on the shop's own pages is a page ───────────────────────────────
# There was no exception handler in the app at all, so an owner-only page opened
# by a manager — or a bookmark to a page that has moved — answered with FastAPI's
# raw {"detail": …} in the browser, which tells the reader nothing about what
# happened or what to do next. The API keeps its JSON contract, because the phone
# and any script depend on it; only the pages under /admin and /sales are turned
# into pages.

_PAGE_ERRORS = {
    403: ("این بخش برای شما باز نیست",
          "این صفحه برای سطحی بالاتر از حساب شما باز می‌شود. اگر فکر می‌کنید باید به آن دسترسی "
          "داشته باشید، از مالک فروشگاه بخواهید سطح حساب شما را تغییر دهد."),
    404: ("این صفحه پیدا نشد",
          "ممکن است نشانی را اشتباه وارد کرده باشید یا این صفحه جابه‌جا شده باشد. "
          "از منوی کنار می‌توانید به بخش‌های دیگر بروید."),
}


# Registered on Starlette's class rather than FastAPI's: a URL that matches no
# route is raised by the router as the former, and an owner-only route raises the
# latter (which subclasses it), so one handler covers both.
@app.exception_handler(StarletteHTTPException)
async def app_error_page(request: Request, exc: StarletteHTTPException):
    if exc.status_code in _PAGE_ERRORS and request.url.path.startswith(("/admin", "/sales")):
        title, message = _PAGE_ERRORS[exc.status_code]
        # The framework's «Not Found» and the app's generic refusal are not
        # sentences to show a shopkeeper: the page already says both, in Persian
        # and in full. Anything else the route chose to say is still printed.
        quiet = {"", "not found", PERMISSION_DENIED_DETAIL.strip().lower()}
        detail = "" if str(exc.detail).strip().lower() in quiet else exc.detail
        return templates.TemplateResponse(
            request, "admin/error.html",
            {"status_code": exc.status_code, "error_title": title,
             "error_message": message, "detail": detail},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# The other raw-JSON leak: request validation. A typed URL like
# /admin/sms/templates/abc/edit never reaches the route — FastAPI refuses the
# path parameter before any handler runs and answers with its default
# {"detail": […]} wall of field locations, in English. The page rule above only
# sees exceptions a route raises, so validation needs its own handler, turning
# the whole refusal into the shop's own 404 page under /admin and /sales.
# /api keeps its JSON contract — the phone and any script depend on it.
@app.exception_handler(RequestValidationError)
async def app_validation_error_page(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(("/admin", "/sales")):
        title, message = _PAGE_ERRORS[404]
        return templates.TemplateResponse(
            request, "admin/error.html",
            {"status_code": 404, "error_title": title,
             "error_message": message, "detail": ""},
            status_code=404,
        )
    return JSONResponse({"detail": exc.errors()}, status_code=422)


app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(customers.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(products.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(campaigns.router)
app.include_router(accounting.router)
app.include_router(sms.router)
app.include_router(clothes_admin_router)
app.include_router(clothes_api_router)

# The device gateway is *also* mounted here so tests and the browser preview can
# exercise it through one server; in production desktop_entry additionally runs
# it as a real second listener on :8101, which is the port the phone pairs to.
app.mount("/gateway", _sms_gateway_app)
