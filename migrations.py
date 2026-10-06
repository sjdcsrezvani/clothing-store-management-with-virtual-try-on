"""Versioned database migrations for the local store database."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

MIGRATION_VERSION = 33


def migration_status(engine) -> int:
    inspector = inspect(engine)
    if not inspector.has_table("schema_version"):
        return 0
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version FROM schema_version WHERE id=1")).scalar()
        return int(row or 0)


def backup_database(database_path: str | Path) -> Path:
    source = Path(database_path)
    if not source.exists():
        raise FileNotFoundError(source)
    target = source.with_name(
        f"{source.name}.before-migration-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(source, target)
    return target


def _ensure_version_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)"))


def _business_events_table_sql(table_name: str) -> str:
    return f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY,
            event_type VARCHAR(60) NOT NULL,
            aggregate_type VARCHAR(50) NOT NULL,
            aggregate_id INTEGER,
            idempotency_key VARCHAR(200) NOT NULL UNIQUE,
            actor_user_id INTEGER,
            request_id VARCHAR(100),
            payload TEXT NOT NULL DEFAULT '{{}}',
            schema_version INTEGER NOT NULL DEFAULT 1,
            occurred_at DATETIME NOT NULL,
            CONSTRAINT ck_business_events_aggregate_id CHECK (aggregate_id IS NULL OR aggregate_id > 0),
            CONSTRAINT ck_business_events_schema_version CHECK (schema_version > 0),
            FOREIGN KEY(actor_user_id) REFERENCES staff_users(id)
        )
    """


def _create_business_event_indexes(conn) -> None:
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_event_type ON business_events (event_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_aggregate_type ON business_events (aggregate_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_aggregate_id ON business_events (aggregate_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_actor_user_id ON business_events (actor_user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_request_id ON business_events (request_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_business_events_occurred_at ON business_events (occurred_at)"))


def _tag_print_batches_table_sql(table_name: str = "tag_print_batches") -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY,
            operator_user_id INTEGER NOT NULL,
            template_snapshot TEXT NOT NULL DEFAULT '{{}}',
            item_count INTEGER NOT NULL DEFAULT 0,
            total_quantity INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(operator_user_id) REFERENCES staff_users(id)
        )
    """


def _tag_print_batch_lines_table_sql(table_name: str = "tag_print_batch_lines") -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            variant_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            product_name VARCHAR(200) NOT NULL,
            barcode VARCHAR(50) NOT NULL,
            sku VARCHAR(50),
            size VARCHAR(20),
            color VARCHAR(50),
            unit_price INTEGER NOT NULL DEFAULT 0,
            is_reprint BOOLEAN NOT NULL DEFAULT 0,
            CONSTRAINT ck_tag_print_line_quantity_positive CHECK (quantity > 0),
            CONSTRAINT ck_tag_print_line_price_nonnegative CHECK (unit_price >= 0),
            FOREIGN KEY(batch_id) REFERENCES tag_print_batches(id),
            FOREIGN KEY(variant_id) REFERENCES product_variants(id)
        )
    """


def _create_tag_print_indexes(conn) -> None:
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batches_operator_user_id ON tag_print_batches (operator_user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batch_lines_batch_id ON tag_print_batch_lines (batch_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_print_batch_lines_variant_id ON tag_print_batch_lines (variant_id)"))


def _add_column_if_missing(conn, table_name: str, column_name: str, column_type: str) -> None:
    exists = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"
    ), {"table_name": table_name}).scalar()
    if not exists:
        return
    columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
    if column_name not in columns:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


_SALES_COLUMNS = (
    "id", "customer_id", "total_amount", "discount_amount", "discount_details",
    "final_amount", "payment_method", "credit_surcharge", "payment_confirmed",
    "credit_settled", "credit_paid_amount", "credit_due_date", "points_earned",
    "is_refunded", "refund_amount", "refund_reason", "refund_date",
    "cash_session_id", "created_at",
)

_CHECKOUT_COLUMNS = (
    "id", "checkout_nonce", "customer_id", "staff_user_id", "basket_json",
    "total_amount", "discount_amount", "credit_surcharge", "final_amount",
    "payment_method", "use_referrer_discount", "custom_discount_amount",
    "custom_discount_percent", "referrer_code", "referrer_phone",
    "campaign_code", "campaign_id", "state", "pos_transaction_id", "sale_id",
    "expires_at", "created_at", "updated_at",
)


def _table_columns(conn, table_name: str) -> list:
    return [row[1] for row in conn.execute(
        text(f"PRAGMA table_info({table_name})")).fetchall()]


def _rebuild_sales_for_split(conn) -> None:
    """Widen the sales method CHECK to admit 'split', keeping every row.

    Ids are copied verbatim, so the seven tables pointing at sales keep
    pointing at the same invoices.
    """
    present = [col for col in _SALES_COLUMNS if col in _table_columns(conn, "sales")]
    if not present:
        return
    checks = ", ".join(
        f"CONSTRAINT ck_sales_{name} CHECK ({expr})"
        for name, expr in [
            ("payment_method", "payment_method IN ('card', 'cash', 'credit', 'split')"),
            ("total_nonnegative", "total_amount >= 0"),
            ("discount_nonnegative", "discount_amount >= 0"),
            ("final_nonnegative", "final_amount >= 0"),
            ("credit_surcharge_nonnegative", "credit_surcharge >= 0"),
            ("credit_paid_amount_nonnegative", "credit_paid_amount >= 0"),
            ("refund_nonnegative", "refund_amount >= 0"),
        ])
    conn.execute(text(f"""
        CREATE TABLE sales_v33 (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers (id),
            total_amount INTEGER NOT NULL,
            discount_amount INTEGER,
            discount_details TEXT,
            final_amount INTEGER NOT NULL,
            payment_method VARCHAR(50),
            credit_surcharge INTEGER,
            payment_confirmed BOOLEAN,
            credit_settled BOOLEAN,
            credit_paid_amount INTEGER,
            credit_due_date DATETIME,
            points_earned INTEGER,
            is_refunded BOOLEAN,
            refund_amount INTEGER,
            refund_reason TEXT,
            refund_date DATETIME,
            cash_session_id INTEGER REFERENCES cash_sessions (id),
            created_at DATETIME,
            {checks}
        )
    """))
    columns = ", ".join(present)
    conn.execute(text(f"INSERT INTO sales_v33 ({columns}) SELECT {columns} FROM sales"))
    conn.execute(text("DROP TABLE sales"))
    conn.execute(text("ALTER TABLE sales_v33 RENAME TO sales"))


def _rebuild_checkout_for_split(conn) -> None:
    """Widen the checkout method CHECK and add the split-legs column.

    Drafts are transient, but a till left open across the upgrade must not
    lose its basket — ids and content copy over like the sales above.
    """
    present = [col for col in _CHECKOUT_COLUMNS if col in _table_columns(conn, "checkout_sessions")]
    if not present:
        return
    for index_name in ("ix_checkout_sessions_checkout_nonce", "ix_checkout_sessions_state"):
        conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
    conn.execute(text("""
        CREATE TABLE checkout_sessions_v33 (
            id INTEGER PRIMARY KEY,
            checkout_nonce VARCHAR(100) NOT NULL UNIQUE,
            customer_id INTEGER REFERENCES customers (id),
            staff_user_id INTEGER REFERENCES staff_users (id),
            basket_json TEXT,
            total_amount INTEGER NOT NULL DEFAULT 0,
            discount_amount INTEGER NOT NULL DEFAULT 0,
            credit_surcharge INTEGER NOT NULL DEFAULT 0,
            final_amount INTEGER NOT NULL DEFAULT 0,
            payment_method VARCHAR(20) NOT NULL DEFAULT 'card',
            use_referrer_discount BOOLEAN NOT NULL DEFAULT 1,
            custom_discount_amount INTEGER NOT NULL DEFAULT 0,
            custom_discount_percent INTEGER NOT NULL DEFAULT 0,
            referrer_code VARCHAR(50),
            referrer_phone VARCHAR(20),
            campaign_code VARCHAR(50),
            campaign_id INTEGER REFERENCES campaigns (id),
            state VARCHAR(30) NOT NULL DEFAULT 'draft',
            pos_transaction_id INTEGER REFERENCES pos_transactions (id),
            sale_id INTEGER REFERENCES sales (id),
            expires_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            split_json TEXT,
            CONSTRAINT ck_checkout_state CHECK (state IN ('draft', 'reserved', 'payment_pending', 'payment_approved', 'payment_cancelled', 'payment_declined', 'payment_uncertain', 'completed', 'refunded', 'expired')),
            CONSTRAINT ck_checkout_payment_method CHECK (payment_method IN ('card', 'cash', 'credit', 'split')),
            CONSTRAINT ck_checkout_total_nonnegative CHECK (total_amount >= 0),
            CONSTRAINT ck_checkout_discount_nonnegative CHECK (discount_amount >= 0),
            CONSTRAINT ck_checkout_surcharge_nonnegative CHECK (credit_surcharge >= 0),
            CONSTRAINT ck_checkout_final_nonnegative CHECK (final_amount >= 0),
            CONSTRAINT ck_checkout_discount_amount_nonnegative CHECK (custom_discount_amount >= 0),
            CONSTRAINT ck_checkout_discount_percent_valid CHECK (custom_discount_percent >= 0 AND custom_discount_percent <= 100)
        )
    """))
    columns = ", ".join(present)
    conn.execute(text(f"INSERT INTO checkout_sessions_v33 ({columns}) SELECT {columns} FROM checkout_sessions"))
    conn.execute(text("DROP TABLE checkout_sessions"))
    conn.execute(text("ALTER TABLE checkout_sessions_v33 RENAME TO checkout_sessions"))
    conn.execute(text("CREATE INDEX ix_checkout_sessions_checkout_nonce ON checkout_sessions (checkout_nonce)"))
    conn.execute(text("CREATE INDEX ix_checkout_sessions_state ON checkout_sessions (state)"))


def _rebuild_business_events(conn) -> None:
    if not table_exists:
        conn.execute(text(_business_events_table_sql("business_events")))
        _create_business_event_indexes(conn)
        return
    for index_name in (
        "ix_business_events_event_type",
        "ix_business_events_aggregate_type",
        "ix_business_events_aggregate_id",
        "ix_business_events_actor_user_id",
        "ix_business_events_request_id",
        "ix_business_events_occurred_at",
    ):
        conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
    conn.execute(text(_business_events_table_sql("business_events_v12")))
    conn.execute(text("""
        INSERT INTO business_events_v12 (
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        )
        SELECT
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        FROM business_events
    """))
    conn.execute(text("DROP TABLE business_events"))
    conn.execute(text("ALTER TABLE business_events_v12 RENAME TO business_events"))
    _create_business_event_indexes(conn)


def _rebuild_sms_messages(conn) -> None:
    """Rebuild ``sms_messages`` without the frozen CHECK on ``source``.

    A database created before the automatic triggers existed carries
    ``ck_sms_message_source`` listing the senders of that day, so the new
    «پس از خرید» and «پیگیری» rows were rejected outright at INSERT — the same
    trap revision 15 rebuilt ``business_events`` to escape. The column is
    validated in code instead (``services.sms_templates.log_message``).

    A fresh install has no such constraint and is left alone, and every other
    column and constraint is carried over untouched.
    """
    ddl = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sms_messages'"
    )).scalar()
    if not ddl or "ck_sms_message_source" not in ddl:
        return
    # The additive pass may not have run yet, so make sure every column this
    # rebuild copies actually exists before it copies it.
    for column_name, column_type in (
        ("delivery_state", "VARCHAR(20) DEFAULT ''"),
        ("claimed_at", "DATETIME"),
        ("sent_by_device_id", "INTEGER"),
        ("attempts", "INTEGER DEFAULT 0"),
        ("ref", "VARCHAR(60) DEFAULT ''"),
    ):
        _add_column_if_missing(conn, "sms_messages", column_name, column_type)
    conn.execute(text("""
        CREATE TABLE sms_messages_v17 (
            id INTEGER NOT NULL,
            template_id INTEGER,
            template_key VARCHAR(50),
            template_name VARCHAR(200),
            customer_id INTEGER,
            employee_id INTEGER,
            job_id INTEGER,
            phone VARCHAR(20) NOT NULL,
            body TEXT NOT NULL,
            status VARCHAR(20) NOT NULL,
            kind VARCHAR(20) NOT NULL,
            source VARCHAR(30) NOT NULL,
            error TEXT,
            ref VARCHAR(60) NOT NULL DEFAULT '',
            delivery_state VARCHAR(20) NOT NULL DEFAULT '',
            claimed_at DATETIME,
            sent_by_device_id INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            sent_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT ck_sms_message_status CHECK (status IN ('queued', 'sent', 'failed')),
            CONSTRAINT ck_sms_message_kind CHECK (kind IN ('marketing', 'transactional')),
            CONSTRAINT ck_sms_message_delivery_state
                CHECK (delivery_state IN ('', 'claimed', 'delivered', 'undelivered')),
            FOREIGN KEY(template_id) REFERENCES sms_templates (id),
            FOREIGN KEY(customer_id) REFERENCES customers (id),
            FOREIGN KEY(employee_id) REFERENCES staff_users (id),
            FOREIGN KEY(job_id) REFERENCES background_jobs (id),
            FOREIGN KEY(sent_by_device_id) REFERENCES sms_devices (id)
        )
    """))
    conn.execute(text("""
        INSERT INTO sms_messages_v17 (
            id, template_id, template_key, template_name, customer_id, employee_id,
            job_id, phone, body, status, kind, source, error, ref, delivery_state,
            claimed_at, sent_by_device_id, attempts, created_at, sent_at
        )
        SELECT
            id, template_id, template_key, template_name, customer_id, employee_id,
            job_id, phone, body, status, kind, source, error, ref, delivery_state,
            claimed_at, sent_by_device_id, attempts, created_at, sent_at
        FROM sms_messages
    """))
    conn.execute(text("DROP TABLE sms_messages"))
    conn.execute(text("ALTER TABLE sms_messages_v17 RENAME TO sms_messages"))
    for index_name, column_name in (
        ("ix_sms_messages_customer_id", "customer_id"),
        ("ix_sms_messages_job_id", "job_id"),
        ("ix_sms_messages_status", "status"),
        ("ix_sms_messages_source", "source"),
        ("ix_sms_messages_ref", "ref"),
        ("ix_sms_messages_created_at", "created_at"),
    ):
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON sms_messages ({column_name})"))


def _apply_revision(conn, version: int) -> None:
    if version == 33:
        # Split-tender sales: cash X plus card Y on one invoice. Two CHECK
        # widenings (sales, checkout_sessions) plus the legs table — purely
        # additive, every existing single-method row reads exactly as before.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sale_payment_parts (
                id INTEGER PRIMARY KEY,
                sale_id INTEGER NOT NULL REFERENCES sales (id),
                method VARCHAR(20) NOT NULL,
                amount INTEGER NOT NULL,
                pos_transaction_id INTEGER REFERENCES pos_transactions (id),
                created_at DATETIME,
                CONSTRAINT ck_sale_part_method CHECK (method IN ('cash', 'card')),
                CONSTRAINT ck_sale_part_amount_positive CHECK (amount > 0)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sale_payment_parts_sale_id "
            "ON sale_payment_parts (sale_id)"))
        _rebuild_sales_for_split(conn)
        _rebuild_checkout_for_split(conn)
        return

    if version == 32:
        # Contract-grade identity: gender, insurance flag and a company
        # position per staff, plus the positions directory itself. All NULL /
        # empty to start — existing staff read exactly as before.
        _add_column_if_missing(conn, "staff_users", "gender", "VARCHAR(10)")
        _add_column_if_missing(conn, "staff_users", "insured", "BOOLEAN")
        _add_column_if_missing(conn, "staff_users", "position_id", "INTEGER")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS job_positions (
                id INTEGER PRIMARY KEY,
                title VARCHAR(100) NOT NULL UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL
            )
        """))
        return

    if version == 31:
        # Per-invoice discount ceilings per person. NULL everywhere to start,
        # which the helper reads as "follow the role default" — purely
        # additive, every existing account behaves exactly as before.
        _add_column_if_missing(conn, "staff_users", "max_discount_amount", "INTEGER")
        _add_column_if_missing(conn, "staff_users", "max_discount_percent", "INTEGER")
        return

    if version == 30:
        # People intelligence: contract terms in months (NULL = open-ended or
        # legacy free date), structured emergency contact, and one marked day
        # per person. Purely additive — existing staff keep their free
        # contract dates until the owner edits them.
        _add_column_if_missing(conn, "staff_users", "contract_term_months", "INTEGER")
        _add_column_if_missing(conn, "staff_users", "emergency_name", "VARCHAR(100)")
        _add_column_if_missing(conn, "staff_users", "emergency_relation", "VARCHAR(50)")
        _add_column_if_missing(conn, "staff_users", "emergency_phone", "VARCHAR(30)")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY,
                staff_user_id INTEGER NOT NULL REFERENCES staff_users (id),
                day DATE NOT NULL,
                status VARCHAR(20) NOT NULL,
                note VARCHAR(200),
                recorded_by_user_id INTEGER REFERENCES staff_users (id),
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_attendance_staff_day UNIQUE (staff_user_id, day),
                CONSTRAINT ck_attendance_status CHECK (
                    status IN ('present', 'absent', 'annual_leave', 'sick_leave'))
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_attendance_staff ON attendance_records (staff_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_attendance_day ON attendance_records (day)"))
        return

    if version == 29:
        # Salary payments grow void flags and an itemized-lines table, and the
        # blanket staff+month unique becomes live-only so a voided month can
        # be re-paid. A table rebuild: the old unique is frozen into the
        # table definition, which SQLite cannot alter in place. Fresh installs
        # already carry the new shape and are left untouched. Nothing inbound
        # references salary_payments by FK (events point by name), so the
        # copy-drop-rename keeps every row and id.
        tables = {
            row[0] for row in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'")).all()
        }
        if "salary_payments" not in tables:
            return
        columns = {
            row[1] for row in conn.execute(
                text("PRAGMA table_info(salary_payments)")).all()
        }
        # Fresh installs already carry the new shape; only rebuild tables
        # that predate the void flags.
        if "is_voided" in columns:
            return
        conn.execute(text("DROP INDEX IF EXISTS ix_salary_payments_id"))
        conn.execute(text("DROP INDEX IF EXISTS ix_salary_payments_staff_user_id"))
        conn.execute(text("""
            CREATE TABLE salary_payments_v29 (
                id INTEGER NOT NULL PRIMARY KEY,
                staff_user_id INTEGER NOT NULL REFERENCES staff_users (id),
                period_key VARCHAR(20) NOT NULL,
                gross_amount INTEGER NOT NULL,
                deductions INTEGER NOT NULL,
                net_amount INTEGER NOT NULL,
                payment_method VARCHAR(20) NOT NULL,
                paid_at DATETIME NOT NULL,
                operator_user_id INTEGER NOT NULL REFERENCES staff_users (id),
                expense_id INTEGER NOT NULL REFERENCES expenses (id),
                cash_session_id INTEGER REFERENCES cash_sessions (id),
                note TEXT,
                is_voided BOOLEAN NOT NULL DEFAULT 0,
                void_reason TEXT,
                voided_at DATETIME,
                voided_by_user_id INTEGER REFERENCES staff_users (id),
                created_at DATETIME NOT NULL,
                CONSTRAINT ck_salary_gross_positive CHECK (gross_amount > 0),
                CONSTRAINT ck_salary_deductions_valid CHECK (deductions >= 0 AND deductions < gross_amount),
                CONSTRAINT ck_salary_net_positive CHECK (net_amount > 0),
                CONSTRAINT ck_salary_payment_method CHECK (payment_method IN ('cash', 'card')),
                UNIQUE (expense_id)
            )
        """))
        conn.execute(text("""
            INSERT INTO salary_payments_v29 (
                id, staff_user_id, period_key, gross_amount, deductions,
                net_amount, payment_method, paid_at, operator_user_id,
                expense_id, cash_session_id, note, is_voided, created_at
            )
            SELECT
                id, staff_user_id, period_key, gross_amount, deductions,
                net_amount, payment_method, paid_at, operator_user_id,
                expense_id, cash_session_id, note, 0, created_at
            FROM salary_payments
        """))
        conn.execute(text("DROP TABLE salary_payments"))
        conn.execute(text("ALTER TABLE salary_payments_v29 RENAME TO salary_payments"))
        conn.execute(text("CREATE INDEX ix_salary_payments_id ON salary_payments (id)"))
        conn.execute(text("CREATE INDEX ix_salary_payments_staff_user_id ON salary_payments (staff_user_id)"))
        conn.execute(text("""
            CREATE UNIQUE INDEX uq_salary_payments_staff_period_live
                ON salary_payments (staff_user_id, period_key) WHERE is_voided = 0
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS salary_payment_items (
                id INTEGER PRIMARY KEY,
                salary_payment_id INTEGER NOT NULL REFERENCES salary_payments (id),
                kind VARCHAR(20) NOT NULL,
                label VARCHAR(200),
                amount INTEGER NOT NULL,
                CONSTRAINT ck_salary_item_kind CHECK (kind IN ('base', 'advance', 'overtime', 'bonus', 'deduction')),
                CONSTRAINT ck_salary_item_amount_positive CHECK (amount > 0)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_salary_payment_items_payment ON salary_payment_items (salary_payment_id)"))
        # Every historical payment earns its base line, so old rows itemize too.
        conn.execute(text("""
            INSERT INTO salary_payment_items (salary_payment_id, kind, label, amount)
            SELECT id, 'base', 'حقوق پایه', gross_amount FROM salary_payments
        """))
        return

    if version == 28:
        # Per-person capability toggles on staff_users. All NULL to start,
        # which effective_cap reads as "follow the role default" — purely
        # additive, every existing account behaves exactly as before.
        for column in ("can_refund", "can_discount", "can_view_payroll", "can_reconcile_pos"):
            _add_column_if_missing(conn, "staff_users", column, "BOOLEAN")
        return

    if version == 27:
        # Junk category spellings — the literal strings "None", "null", "-"
        # and the like, typed or imported once — collapse to NULL, which
        # every reading already renders as «بدون دسته». Data repair, not a
        # schema change: filters keep matching stored values exactly.
        tables = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'")).all()}
        if "products" in tables:
            conn.execute(text("""
                UPDATE products
                   SET category = NULL
                 WHERE TRIM(category) IN ('None', 'none', 'NONE', 'null', 'Null', 'NULL',
                                          'nil', 'Nil', '—', '-', '–', '')
            """))
        if "expenses" in tables:
            conn.execute(text("""
                UPDATE expenses
                   SET category = NULL
                 WHERE TRIM(category) IN ('None', 'none', 'NONE', 'null', 'Null', 'NULL',
                                          'nil', 'Nil', '—', '-', '–', '')
            """))
        return

    if version == 26:
        # A bounced cheque stays flagged until resolved or re-issued.
        # Purely additive — every existing row starts unflagged.
        _add_column_if_missing(conn, "issued_checks", "needs_followup", "BOOLEAN DEFAULT 0")
        return

    if version == 25:
        # Monthly expense rules: a rule posts itself every month until
        # stopped, and auto-posted rows point back at their rule. Purely
        # additive — a database without rules behaves exactly as before, and
        # `create_all` already builds both for a fresh install.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS recurring_expenses (
                id INTEGER PRIMARY KEY,
                amount INTEGER NOT NULL,
                category VARCHAR(100),
                expense_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
                payment_method VARCHAR(20) NOT NULL DEFAULT 'cash',
                note TEXT,
                day_of_month INTEGER NOT NULL,
                active BOOLEAN NOT NULL DEFAULT 1,
                next_due DATETIME NOT NULL,
                created_by_user_id INTEGER REFERENCES staff_users (id),
                stopped_at DATETIME,
                created_at DATETIME NOT NULL
            )
        """))
        _add_column_if_missing(conn, "expenses", "recurring_rule_id", "INTEGER")
        return

    if version == 24:
        # Archiving retires a campaign without erasing it: the list hides it
        # and the send audience skips it, while redemptions stay put.
        # Purely additive — every existing row starts unarchived.
        _add_column_if_missing(conn, "campaigns", "is_archived", "INTEGER DEFAULT 0")
        return

    if version == 23:
        # When the invitation SMS actually left, beside when the assignment
        # was created: the model grew the column but no revision carried it,
        # so any database built before it 500s the customers page.
        # Purely additive — existing rows simply have no sent time yet.
        _add_column_if_missing(conn, "campaign_assignments", "invite_sent_at", "DATETIME")
        return

    if version == 22:
        # One arrival, one receipt: the purchase that took a variant in is
        # stamped on the variant itself. Purely additive — every existing row
        # starts unreceived and pickable.
        _add_column_if_missing(conn, "product_variants", "received_purchase_id", "INTEGER")
        return

    if version == 21:
        # A gallery per sellable variant: frames ordered first-is-primary.
        # Purely additive — existing rows keep their single image_path.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS variant_images (
                id INTEGER PRIMARY KEY,
                variant_id INTEGER NOT NULL REFERENCES product_variants(id),
                image_path VARCHAR(500) NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_variant_images_variant_id "
            "ON variant_images (variant_id)"
        ))
        return

    if version == 20:
        # Weight is weighed per sellable unit: sizes of one product rarely
        # share it, so the scale moves from the product to the variant.
        # Purely additive — old product-level weights stay where they are,
        # unread, and every variant starts weightless.
        _add_column_if_missing(conn, "product_variants", "weight_grams", "INTEGER")
        return

    if version == 19:
        # One drawer, one shift. Two managers clicking «باز کردن صندوق» in the
        # same instant used to leave two rows with status='open', and the
        # register then read whichever it found first — so the closing count of
        # one shift silently belonged to the other. The index makes that
        # impossible; the pass before it closes whatever a database already has.
        #
        # The duplicate is closed without a count rather than with a guessed
        # one: a count nobody performed is worse than no count, and the shift
        # statement says «بستهشده بدون شمارش» so the gap is visible instead of
        # being filled in.
        tables = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_sessions'"
        )).scalar()
        if not tables:
            # A database this new has no till to protect yet; `create_all` builds
            # the table with its index straight from the model.
            return
        conn.execute(text("""
            UPDATE cash_sessions
               SET status = 'abandoned', closed_at = CURRENT_TIMESTAMP
             WHERE status = 'open'
               AND id <> (SELECT MAX(id) FROM cash_sessions WHERE status = 'open')
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_cash_sessions_one_open
                ON cash_sessions (status) WHERE status = 'open'
        """))
        return

    if version == 18:
        # Which customer values a message was rendered from, so a history entry
        # can be replayed and audited rather than merely read. Purely additive:
        # an existing shop's rows keep their meaning, and an empty string says
        # plainly that they were sent before anything recorded it.
        _add_column_if_missing(conn, "sms_messages", "values_json", "TEXT DEFAULT ''")
        return

    if version == 17:
        _rebuild_sms_messages(conn)
        _add_column_if_missing(conn, "sms_templates", "trigger_key", "VARCHAR(30) DEFAULT ''")
        _add_column_if_missing(conn, "sms_templates", "trigger_days", "INTEGER DEFAULT 0")
        return

    if version == 16:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tag_templates (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                config_json TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_templates_name ON tag_templates (name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tag_templates_is_active ON tag_templates (is_active)"))
        _add_column_if_missing(conn, "products", "tag_template_id", "INTEGER")
        products_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'"
        )).scalar()
        if products_exists:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_tag_template_id ON products (tag_template_id)"))
        return

    if version == 15:
        # Rebuild business_events without the event-type CHECK constraint when a
        # stale copy is present. The constraint was frozen into the table at
        # creation time, so databases created before newer event types were
        # released silently rejected them during INSERT OR IGNORE. Event types
        # are validated in code instead. Fresh installs already lack the
        # constraint and are left untouched.
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar()
        if not table_exists:
            conn.execute(text(_business_events_table_sql("business_events")))
            _create_business_event_indexes(conn)
            return
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar() or ""
        if "ck_business_events_type" not in ddl:
            return
        for index_name in (
            "ix_business_events_event_type",
            "ix_business_events_aggregate_type",
            "ix_business_events_aggregate_id",
            "ix_business_events_actor_user_id",
            "ix_business_events_request_id",
            "ix_business_events_occurred_at",
        ):
            conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
        conn.execute(text(_business_events_table_sql("business_events_v15")))
        conn.execute(text("""
            INSERT INTO business_events_v15 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v15 RENAME TO business_events"))
        _create_business_event_indexes(conn)
        return

    if version == 13:
        conn.execute(text(_tag_print_batches_table_sql()))
        conn.execute(text(_tag_print_batch_lines_table_sql()))
        _add_column_if_missing(conn, "tag_print_batch_lines", "unit_price", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "tag_print_batch_lines", "is_reprint", "BOOLEAN NOT NULL DEFAULT 0")
        _create_tag_print_indexes(conn)
        return

    if version == 8:
        conn.execute(text(_business_events_table_sql("business_events").replace(
            "CREATE TABLE business_events", "CREATE TABLE IF NOT EXISTS business_events", 1
        )))
        _create_business_event_indexes(conn)
        return

    if version == 11:
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar()
        if not table_exists:
            conn.execute(text(_business_events_table_sql("business_events")))
            _create_business_event_indexes(conn)
            return
        for index_name in (
            "ix_business_events_event_type",
            "ix_business_events_aggregate_type",
            "ix_business_events_aggregate_id",
            "ix_business_events_actor_user_id",
            "ix_business_events_request_id",
            "ix_business_events_occurred_at",
        ):
            conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
        conn.execute(text(_business_events_table_sql("business_events_v11")))
        conn.execute(text("""
            INSERT INTO business_events_v11 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v11 RENAME TO business_events"))
        _create_business_event_indexes(conn)
        return

    if version == 10:
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
        )).scalar()
        if not table_exists:
            conn.execute(text(_business_events_table_sql("business_events")))
            _create_business_event_indexes(conn)
            return
        for index_name in (
            "ix_business_events_event_type",
            "ix_business_events_aggregate_type",
            "ix_business_events_aggregate_id",
            "ix_business_events_actor_user_id",
            "ix_business_events_request_id",
            "ix_business_events_occurred_at",
        ):
            conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
        conn.execute(text(_business_events_table_sql("business_events_v10")))
        conn.execute(text("""
            INSERT INTO business_events_v10 (
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            )
            SELECT
                id, event_type, aggregate_type, aggregate_id, idempotency_key,
                actor_user_id, request_id, payload, schema_version, occurred_at
            FROM business_events
        """))
        conn.execute(text("DROP TABLE business_events"))
        conn.execute(text("ALTER TABLE business_events_v10 RENAME TO business_events"))
        _create_business_event_indexes(conn)
        return

    if version != 9:
        return

    table_exists = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_events'"
    )).scalar()
    if not table_exists:
        conn.execute(text(_business_events_table_sql("business_events")))
        _create_business_event_indexes(conn)
        return

    for index_name in (
        "ix_business_events_event_type",
        "ix_business_events_aggregate_type",
        "ix_business_events_aggregate_id",
        "ix_business_events_actor_user_id",
        "ix_business_events_request_id",
        "ix_business_events_occurred_at",
    ):
        conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))

    conn.execute(text(_business_events_table_sql("business_events_v9")))
    conn.execute(text("""
        INSERT INTO business_events_v9 (
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        )
        SELECT
            id, event_type, aggregate_type, aggregate_id, idempotency_key,
            actor_user_id, request_id, payload, schema_version, occurred_at
        FROM business_events
    """))
    conn.execute(text("DROP TABLE business_events"))
    conn.execute(text("ALTER TABLE business_events_v9 RENAME TO business_events"))
    _create_business_event_indexes(conn)


def upgrade(engine, target: int = MIGRATION_VERSION) -> int:
    if target < 0 or target > MIGRATION_VERSION:
        raise ValueError(f"Unsupported migration target: {target}")
    _ensure_version_table(engine)
    current = migration_status(engine)
    if current > target:
        raise RuntimeError(f"Database version {current} is newer than requested version {target}")
    for version in range(current + 1, target + 1):
        if version == 33:
            _upgrade_with_fk_off(engine, version)
            continue
        with engine.begin() as conn:
            _apply_revision(conn, version)
            conn.execute(text("UPDATE schema_version SET version=:version WHERE id=1"), {"version": version})
    return migration_status(engine)


def _upgrade_with_fk_off(engine, version: int) -> None:
    """Run one revision with foreign keys disabled, then prove the graph whole.

    Revision 33 rebuilds the sales table, which seven tables point at — with
    enforcement on, dropping the parent fails outright. The pragma is set
    outside any transaction (inside one it is a silent no-op) on a dedicated
    connection, restored before the connection returns to the pool, and
    ``foreign_key_check`` must come back empty before anything commits.
    """
    # The pragma autobegins a transaction on its own; commit it away so the
    # work below gets exactly one transaction of its own. The prior setting
    # is restored afterwards — a plain engine that started OFF must not
    # discover enforcement as a migration side effect.
    with engine.connect() as conn:
        prior_fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.commit()
        try:
            with conn.begin():
                _apply_revision(conn, version)
                violations = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
                if violations:
                    raise RuntimeError(f"Revision {version} broke foreign keys: {violations[:5]}")
                conn.execute(text("UPDATE schema_version SET version=:version WHERE id=1"), {"version": version})
        finally:
            conn.execute(text(f"PRAGMA foreign_keys={'ON' if prior_fk else 'OFF'}"))
            conn.commit()


def downgrade(engine, target: int = 0) -> int:
    if target != 0:
        raise ValueError("Only downgrade to version 0 is supported")
    _ensure_version_table(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE schema_version SET version=0 WHERE id=1"))
    return 0
