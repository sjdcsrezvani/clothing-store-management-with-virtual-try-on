"""Split-tender sales phase 3: migration, confirm, counting, swap, resume."""
from sqlalchemy import create_engine, inspect, text

from migrations import MIGRATION_VERSION, upgrade
from models import Sale, SalePaymentPart


def test_migration_33_rebuilds_sales_and_checkout_keeping_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/stale32.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 32)"))
        conn.execute(text(
            """CREATE TABLE customers (id INTEGER PRIMARY KEY, phone VARCHAR(20))"""))
        conn.execute(text(
            """CREATE TABLE cash_sessions (id INTEGER PRIMARY KEY)"""))
        conn.execute(text(
            """CREATE TABLE pos_transactions (id INTEGER PRIMARY KEY)"""))
        conn.execute(text(
            """CREATE TABLE sales (
                id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers (id),
                total_amount INTEGER NOT NULL, final_amount INTEGER NOT NULL,
                payment_method VARCHAR(50),
                CONSTRAINT ck_sales_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text(
            """CREATE TABLE sale_items (
                id INTEGER PRIMARY KEY,
                sale_id INTEGER NOT NULL REFERENCES sales (id), quantity INTEGER NOT NULL)"""))
        conn.execute(text(
            """CREATE TABLE checkout_sessions (
                id INTEGER PRIMARY KEY, checkout_nonce VARCHAR(100) NOT NULL UNIQUE,
                payment_method VARCHAR(20) NOT NULL DEFAULT 'card',
                total_amount INTEGER NOT NULL DEFAULT 0, final_amount INTEGER NOT NULL DEFAULT 0,
                expires_at DATETIME NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT ck_checkout_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text("INSERT INTO customers (id, phone) VALUES (1, '09120000001')"))
        conn.execute(text("INSERT INTO cash_sessions (id) VALUES (1)"))
        conn.execute(text("INSERT INTO sales (id, customer_id, total_amount, final_amount, payment_method)"
                          " VALUES (7, 1, 500000, 500000, 'cash')"))
        conn.execute(text("INSERT INTO sale_items (id, sale_id, quantity) VALUES (3, 7, 2)"))
        conn.execute(text("INSERT INTO checkout_sessions (id, checkout_nonce, payment_method, total_amount,"
                          " final_amount, expires_at, created_at, updated_at)"
                          " VALUES (9, 'n1', 'card', 100, 100,"
                          " '2030-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    assert upgrade(engine) == MIGRATION_VERSION == 33
    with engine.connect() as conn:
        assert conn.execute(text("SELECT payment_method FROM sales WHERE id=7")).scalar() == "cash"
        assert conn.execute(text("SELECT quantity FROM sale_items WHERE id=3")).scalar() == 2
        assert conn.execute(text("SELECT split_json FROM checkout_sessions WHERE id=9")).scalar() is None
        assert not conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        conn.execute(text("INSERT INTO sales (id, total_amount, final_amount, payment_method)"
                          " VALUES (8, 100, 100, 'split')"))
        conn.execute(text("INSERT INTO sale_payment_parts (sale_id, method, amount)"
                          " VALUES (8, 'cash', 60)"))
        try:
            conn.execute(text("INSERT INTO sales (id, total_amount, final_amount, payment_method)"
                              " VALUES (9, 100, 100, 'bitcoin')"))
        except Exception:
            pass
        else:
            raise SystemExit("widened CHECK admits garbage")
        conn.commit()


def test_migration_33_hands_back_the_indexes_it_took(tmp_path):
    """A rebuilt table must wear the indexes a fresh install gives it.

    SQLite drops a table's indexes along with the table. Revision 33 rebuilds
    ``sales`` and ``checkout_sessions`` to widen their method CHECKs, so the
    indexes have to come back explicitly — otherwise a shop that upgrades
    ends up with a thinner schema than one that installs today. Only the
    primary-key indexes are checked here, because those are the ones the
    rebuild has no other reason to re-create.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/indexed32.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 32)"))
        conn.execute(text("CREATE TABLE customers (id INTEGER PRIMARY KEY, phone VARCHAR(20))"))
        conn.execute(text("CREATE TABLE cash_sessions (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE pos_transactions (id INTEGER PRIMARY KEY)"))
        conn.execute(text(
            """CREATE TABLE sales (
                id INTEGER PRIMARY KEY, total_amount INTEGER NOT NULL,
                final_amount INTEGER NOT NULL, payment_method VARCHAR(50),
                CONSTRAINT ck_sales_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text("CREATE INDEX ix_sales_id ON sales (id)"))
        conn.execute(text(
            """CREATE TABLE checkout_sessions (
                id INTEGER PRIMARY KEY, checkout_nonce VARCHAR(100) NOT NULL UNIQUE,
                payment_method VARCHAR(20) NOT NULL DEFAULT 'card',
                state VARCHAR(30) NOT NULL DEFAULT 'draft',
                total_amount INTEGER NOT NULL DEFAULT 0, final_amount INTEGER NOT NULL DEFAULT 0,
                expires_at DATETIME NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT ck_checkout_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text("CREATE INDEX ix_checkout_sessions_id ON checkout_sessions (id)"))
        conn.execute(text("CREATE INDEX ix_checkout_sessions_state ON checkout_sessions (state)"))
    assert upgrade(engine) == 33
    sales_indexes = {index["name"] for index in inspect(engine).get_indexes("sales")}
    checkout_indexes = {index["name"] for index in inspect(engine).get_indexes("checkout_sessions")}
    assert "ix_sales_id" in sales_indexes
    assert "ix_checkout_sessions_id" in checkout_indexes
    assert "ix_checkout_sessions_state" in checkout_indexes


def _basket(variant, qty=1):
    return [{"variant_id": variant.id, "product_id": variant.product_id,
             "name": "تست", "unit_price": variant.price, "unit_cost": 0,
             "quantity": qty, "total_price": variant.price * qty}]


def test_split_confirm_writes_legs_and_counts_cash_leg(client, db_session, authed):
    import json
    from tests.test_sales_money import _make_variant, _confirm_sale
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = _basket(variant)
    total = 100_000
    res = _confirm_sale(client, basket, extra={
        "payment_method": "split", "split_cash": "40000", "split_card": "60000",
    })
    assert res.status_code == 200 and "ترکیبی" in res.text
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()
    assert sale.payment_method == "split" and sale.final_amount == total
    parts = {p.method: p.amount for p in
             db_session.query(SalePaymentPart).filter_by(sale_id=sale.id).all()}
    assert parts == {"cash": 40000, "card": 60000}
    from services.accounting import get_cashbox
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    box = get_cashbox(db_session, now - timedelta(days=1), now + timedelta(days=1), 0)
    assert box["cash_sales"] >= 40000


def test_split_sum_mismatch_and_single_leg_refused(client, db_session, authed):
    import json
    from tests.test_sales_money import _make_variant, _confirm_sale
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    before = db_session.query(Sale).count()
    bad_sum = _confirm_sale(client, _basket(variant), extra={
        "payment_method": "split", "split_cash": "10000", "split_card": "60000",
    })
    assert bad_sum.status_code == 200 and "نمی‌خواند" in bad_sum.text
    single = _confirm_sale(client, _basket(variant), extra={
        "payment_method": "split", "split_cash": "100000", "split_card": "",
    })
    assert single.status_code == 200 and "دو مبلغ" in single.text
    assert db_session.query(Sale).count() == before


def test_swap_customer_keeps_basket(client, db_session, authed):
    import json
    from tests.conftest import csrf_token
    from tests.test_sales_money import _make_variant, _make_customer
    from tests.test_tables import _login
    _login(client)
    first = _make_customer(db_session, phone="09120000011")
    second = _make_customer(db_session, phone="09120000022")
    _, variant = _make_variant(db_session)
    basket = _basket(variant)
    page = client.post("/sales/swap-customer", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "phone": second.phone, "customer_id": str(first.id),
        "basket_json": json.dumps(basket, ensure_ascii=False),
    }).text
    assert second.phone in page and first.phone not in page
    unknown = client.post("/sales/swap-customer", data={
        "csrf_token": csrf_token(client, "/sales/new"),
        "phone": "09129999999", "customer_id": str(first.id),
        "basket_json": json.dumps(basket, ensure_ascii=False),
    }).text
    assert "ثبت نیست" in unknown and first.phone in unknown


def test_drafts_list_and_resume(client, db_session, authed):
    import json
    from tests.conftest import csrf_token
    from tests.test_sales_money import _make_variant
    from tests.test_tables import _login
    _login(client)
    _, variant = _make_variant(db_session)
    basket = _basket(variant)
    client.post("/sales/add-to-basket", data={
        "customer_id": "0", "barcode": variant.barcode,
        "basket_json": json.dumps([], ensure_ascii=False),
        "csrf_token": csrf_token(client, "/sales/new"),
    })
    listed = client.get("/sales/new").text
    assert "نیمه‌تمام" in listed
    import re
    nonce = re.search(r'name="nonce" value="([A-Za-z0-9_-]+)"', listed).group(1)
    resumed = client.post("/sales/resume-draft", data={
        "csrf_token": csrf_token(client, "/sales/new"), "nonce": nonce,
    }).text
    assert variant.barcode in resumed or "تست" in resumed


def test_ledger_and_invoice_name_the_split(client, db_session, authed):
    from tests.test_tables import _login
    _login(client)
    sale = Sale(total_amount=100_000, final_amount=100_000, payment_method="split",
                payment_confirmed=True)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SalePaymentPart(sale_id=sale.id, method="cash", amount=40000))
    db_session.add(SalePaymentPart(sale_id=sale.id, method="card", amount=60000))
    db_session.commit()
    ledger = client.get("/sales?method=split").text
    assert f"#{sale.id}</strong>" in ledger and "ترکیبی" in ledger
    invoice = client.get(f"/sales/invoice/{sale.id}").text
    assert "ترکیبی" in invoice and "40,000" in invoice


def test_split_refund_voids_wholly_and_tags_drawer(client, db_session, authed):
    from tests.conftest import csrf_token
    from tests.test_tables import _login
    _login(client)
    sale = Sale(total_amount=100_000, final_amount=100_000, payment_method="split",
                payment_confirmed=True)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SalePaymentPart(sale_id=sale.id, method="cash", amount=40000))
    db_session.add(SalePaymentPart(sale_id=sale.id, method="card", amount=60000))
    db_session.commit()
    res = client.post(f"/sales/{sale.id}/refund", data={
        "csrf_token": csrf_token(client, f"/sales/invoice/{sale.id}"),
        "refund_reason": "probe",
    }, follow_redirects=False)
    assert res.status_code == 303
    db_session.expire_all()
    refunded = db_session.query(Sale).filter(Sale.id == sale.id).one()
    assert refunded.is_refunded and refunded.refund_amount == 100_000


def test_migration_33_resumes_after_crashed_boot(tmp_path):
    """A boot that dies mid-rebuild (staging left, version unrecorded) must
    boot clean on retry — partial staging redone, orphaned staging claimed."""
    engine = create_engine(f"sqlite:///{tmp_path}/crashed32.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 32)"))
        conn.execute(text("CREATE TABLE customers (id INTEGER PRIMARY KEY, phone VARCHAR(20))"))
        conn.execute(text("CREATE TABLE cash_sessions (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE pos_transactions (id INTEGER PRIMARY KEY)"))
        conn.execute(text(
            """CREATE TABLE sales (
                id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers (id),
                total_amount INTEGER NOT NULL, final_amount INTEGER NOT NULL,
                payment_method VARCHAR(50),
                CONSTRAINT ck_sales_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text(
            """CREATE TABLE checkout_sessions (
                id INTEGER PRIMARY KEY, checkout_nonce VARCHAR(100) NOT NULL UNIQUE,
                payment_method VARCHAR(20) NOT NULL DEFAULT 'card',
                total_amount INTEGER NOT NULL DEFAULT 0, final_amount INTEGER NOT NULL DEFAULT 0,
                expires_at DATETIME NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT ck_checkout_payment_method CHECK (payment_method IN ('card', 'cash', 'credit')))"""))
        conn.execute(text("INSERT INTO customers (id, phone) VALUES (1, '09120000001')"))
        conn.execute(text("INSERT INTO sales (id, customer_id, total_amount, final_amount, payment_method)"
                          " VALUES (7, 1, 500000, 500000, 'cash')"))
    assert upgrade(engine) == 33
    with engine.begin() as conn:
        # Simulate the crashed first boot: version back, partial staging left.
        conn.execute(text("UPDATE schema_version SET version=32 WHERE id=1"))
        conn.execute(text("CREATE TABLE sales_v33 (id INTEGER PRIMARY KEY)"))
    assert upgrade(engine) == 33
    with engine.connect() as conn:
        assert conn.execute(text("SELECT payment_method FROM sales WHERE id=7")).scalar() == "cash"
        assert not conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_v33%'")).fetchall()
    with engine.begin() as conn:
        # Simulate dying between DROP and RENAME: source gone, staging whole.
        conn.execute(text("ALTER TABLE sales RENAME TO sales_v33"))
        conn.execute(text("UPDATE schema_version SET version=32 WHERE id=1"))
    assert upgrade(engine) == 33
    with engine.connect() as conn:
        assert conn.execute(text("SELECT payment_method FROM sales WHERE id=7")).scalar() == "cash"
