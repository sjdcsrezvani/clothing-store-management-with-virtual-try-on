"""حساب نسیه: one page for debtors, ageing, invoices and collection.

The page and the old وصول مطالبات dashboard are the same screen now, so these
tests pin the things that made the merge worth doing: ageing measured from the
سررسید a نسیه invoice actually carries, an invoice-level view, receipts that can
close one invoice, reversal reasons that reach the immutable ledger, and a
statement a customer can be handed.
"""
import itertools
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import Customer, Payment, Product, ProductVariant, Sale, Settings
from services.accounting import (
    AGE_BUCKET_LABELS,
    age_bucket,
    build_debt_rows,
    credit_due_date_for,
    credit_terms_days,
    list_debts,
    sale_remaining,
)
from tests.conftest import csrf_token
from tests.test_sales_money import _confirm_sale, _make_customer

ROOT = Path(__file__).resolve().parents[1]
CREDIT_HTML = (ROOT / "templates" / "admin" / "credit.html").read_text()
CUSTOMER_HTML = (ROOT / "templates" / "admin" / "credit_customer.html").read_text()
STATEMENT_HTML = (ROOT / "templates" / "admin" / "credit_statement.html").read_text()
BASE_HTML = (ROOT / "templates" / "base.html").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()


# ── helpers ──────────────────────────────────────────────────────────────────

# `due_days` uses this sentinel so "keep whatever the checkout wrote" is distinct
# from "clear it": an invoice with no سررسید is a state these tests cover.
_KEEP = object()
_sequence = itertools.count(1)


def _post(client, url, data, authed=None):
    data = dict(data)
    data["csrf_token"] = csrf_token(client, "/admin/")
    return client.post(url, data=data, follow_redirects=False)


def _variant(db, price, stock=10):
    """A fresh product/variant per call: barcodes are unique, so nothing collides."""
    product = Product(name=f"کالای نسیه {next(_sequence)}")
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id, price=price, cost_price=max(1, price // 2),
        stock_quantity=stock, barcode=f"CR-{next(_sequence)}",
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def _credit_sale(client, db, customer, *, amount, stock=10, days_ago=0, due_days=_KEEP):
    """A real نسیه sale through the checkout, optionally aged or re-dated.

    The sale carries its own variant priced at `amount`, because the checkout
    recomputes prices from the database — a basket price that disagrees with the
    variant is ignored, so one variant at 100k would make every "120k" invoice
    a 100k one.
    """
    variant = _variant(db, amount, stock)
    _confirm_sale(
        client,
        [{"variant_id": variant.id, "product_id": variant.product_id,
          "unit_price": amount, "quantity": 1, "total_price": amount}],
        customer_id=customer.id, extra={"payment_method": "credit"},
    )
    sale = db.query(Sale).filter(Sale.customer_id == customer.id) \
        .order_by(Sale.id.desc()).first()
    assert sale is not None, "the نسیه sale was not recorded"
    if days_ago:
        sale.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    if due_days is not _KEEP:
        sale.credit_due_date = None if due_days is None else (
            datetime.now(timezone.utc) + timedelta(days=due_days)
        )
    db.commit()
    db.expire_all()
    return sale


def _debtor(client, db, *, amount=200_000, phone=None, name="بدهکار"):
    customer = _make_customer(
        db, phone=phone or f"0912{next(_sequence):07d}", first_name=name,
    )
    sale = _credit_sale(client, db, customer, amount=amount, stock=20)
    return customer, sale


# ── presentation contract ────────────────────────────────────────────────────

def test_page_uses_the_catalog_design_language():
    assert 'class="page-heading product-page-heading"' in CREDIT_HTML
    assert 'class="eyebrow"' in CREDIT_HTML
    assert 'class="admin-nav"' in CREDIT_HTML
    # The nav lives in the heading, not at the foot of the page.
    assert CREDIT_HTML.index("admin-nav") < CREDIT_HTML.index("</div>\n\n{% if msg")
    assert "📒 حساب نسیه مشتریان" not in CREDIT_HTML  # the old emoji heading
    assert 'style="' not in CREDIT_HTML


def test_page_table_buckets_and_real_empty_states():
    assert 'class="table-scroll"' in CREDIT_HTML
    assert CREDIT_HTML.count('scope="col"') >= 12
    assert 'class="empty-state"' in CREDIT_HTML
    # No debt at all and an over-filtered list are different situations.
    assert "هیچ بدهی نسیه‌ای وجود ندارد" in CREDIT_HTML
    assert "بدهکاری با این فیلترها پیدا نشد" in CREDIT_HTML
    assert "هیچ فاکتور نسیه بازی وجود ندارد" in CREDIT_HTML
    assert 'class="product-pagination"' in CREDIT_HTML
    # Ageing is the KPI strip, and every bucket filters the table it counts.
    for key in AGE_BUCKET_LABELS:
        assert f'?bucket={key}' in CREDIT_HTML, key
    assert 'class="search-form credit-filter-form"' in CREDIT_HTML


def test_customer_page_is_the_admin_layout_not_the_checkout_one():
    assert 'class="page-heading product-page-heading"' in CUSTOMER_HTML
    assert 'class="profile-layout"' in CUSTOMER_HTML
    assert "checkout-layout" not in CUSTOMER_HTML  # the counter's layout, not here
    assert 'class="table-scroll"' in CUSTOMER_HTML
    assert 'style="' not in CUSTOMER_HTML
    for hook in ("ثبت دریافت", "فاکتورهای نسیه باز", "تاریخچه دریافت‌ها", "سقف اعتبار"):
        assert hook in CUSTOMER_HTML, hook


def test_every_receipt_can_be_reversed_with_a_reason():
    assert 'name="reason"' in CUSTOMER_HTML
    assert 'action="/admin/payments/{{ p.id }}/reverse"' in CUSTOMER_HTML
    # Who took the money is on the page, not only in the event journal.
    assert "دریافت‌کننده" in CUSTOMER_HTML
    assert "p.received_by" in CUSTOMER_HTML


def test_statement_is_a_printable_document():
    assert 'class="card credit-statement"' in STATEMENT_HTML
    assert "window.print()" in STATEMENT_HTML
    assert 'class="statement-table' in STATEMENT_HTML or "statement-table" in STATEMENT_HTML
    assert "مانده از قبل" in STATEMENT_HTML
    for hook in ("بدهکار (فاکتور)", "بستانکار (دریافت)", "مانده"):
        assert hook in STATEMENT_HTML, hook
    assert "screen-only" in STATEMENT_HTML


def test_collections_leaves_the_sidebar_now_that_it_is_one_page():
    assert "/admin/collections" not in BASE_HTML
    assert "وصول مطالبات" not in BASE_HTML
    # …and the old name is gone from the dashboard too, not just the sidebar.
    dashboard = (ROOT / "templates" / "admin" / "dashboard.html").read_text()
    assert "وصول مطالبات" not in dashboard
    assert '/admin/credit' in dashboard


def test_credit_styles_come_from_theme_tokens():
    section = STYLE_CSS[STYLE_CSS.index("/* ===================== نسیه (credit)"):]
    section = section[:section.index("/* High-contrast: state must not depend")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section), section[:200]
    for token in ("var(--rule)", "var(--card)", "var(--ink-soft)",
                  "var(--surface-soft)", "var(--mint)", "var(--sunshine)",
                  "var(--persimmon)", "var(--candy)", "var(--candy-dark)"):
        assert token in section, token
    # Overdue and the ageing strip must not rest on colour alone.
    assert 'html[data-theme-mode="high-contrast"] .bucket-card' in STYLE_CSS
    assert "@media print" in STYLE_CSS


def test_heading_nav_cannot_be_squeezed_into_two_rows():
    """The heading's description used to starve the nav until its three buttons
    wrapped 2 + 1 — «داشبورد» dropping to a second line under the other two.
    """
    # A long description shrinks; the nav keeps its own width and stays on a row.
    assert ".page-heading > div { min-width: 0; }" in STYLE_CSS
    assert ".page-heading .admin-nav { flex: 0 0 auto; }" in STYLE_CSS
    # …and short labels share one width, so the set reads as a set.
    assert ".admin-nav > .btn { min-width: 7.5rem; }" in STYLE_CSS
    # A card holding a wide table may be narrower than the table; otherwise the
    # table stretched the whole page instead of scrolling inside its container.
    assert ".profile-main > *, .profile-side > * { min-width: 0; }" in STYLE_CSS
    assert ".sr-only" in STYLE_CSS and "inset: 0;" in STYLE_CSS
    # Buttons reserve the same border box whether they draw one or not, so a
    # primary next to a ghost is not 4px shorter.
    assert "border: 2px solid transparent;" in STYLE_CSS


# ── سررسید (due dates) ───────────────────────────────────────────────────────

def test_a_credit_sale_gets_the_stores_payment_term_as_its_due_date(client, db_session):
    customer, sale = _debtor(client, db_session)
    assert credit_terms_days(db_session) == 30
    assert sale.credit_due_date is not None
    due = sale.credit_due_date.replace(tzinfo=timezone.utc)
    assert abs((due - (datetime.now(timezone.utc) + timedelta(days=30))).total_seconds()) < 120


def test_zero_terms_means_no_due_date_at_all(client, db_session):
    db_session.add(Settings(key="credit_terms_days", value="0"))
    db_session.commit()

    _, sale = _debtor(client, db_session)
    assert credit_due_date_for(db_session) is None
    assert sale.credit_due_date is None


def test_ageing_counts_from_the_due_date_not_the_invoice_date(client, db_session):
    """A young invoice that is already late must not read as «جاری»."""
    customer = _make_customer(db_session)
    fresh = _credit_sale(client, db_session, customer, amount=100_000, days_ago=10, due_days=-20)
    legacy = _credit_sale(client, db_session, customer, amount=100_000, days_ago=10, due_days=None)

    now = datetime.now(timezone.utc)
    assert fresh.created_at.replace(tzinfo=timezone.utc) < now - timedelta(days=9)
    assert age_bucket(fresh, now) == "1_30"      # 20 days past its due date
    assert age_bucket(legacy, now) == "current"  # no term: the old ≤30-day rule


def test_due_soon_is_its_own_signal_before_anything_is_late(client, db_session):
    customer = _make_customer(db_session)
    _credit_sale(client, db_session, customer, amount=100_000, due_days=3)

    listing = list_debts(db_session)
    row = listing["rows"][0]
    assert row["overdue_amount"] == 0
    assert row["due_soon_amount"] == 100_000
    assert listing["overview"]["due_soon_customers"] == 1


def test_the_page_shows_the_due_date_and_flags_the_late_ones(client, db_session, authed):
    customer, sale = _debtor(client, db_session, phone="09121110001", name="دیرکرددار")
    sale.credit_due_date = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()

    page = client.get("/admin/credit")
    assert page.status_code == 200
    assert "سررسید گذشته" in page.text
    assert "۳۱–۶۰ روز دیرکرد" in page.text

    # …and the invoice view names the invoice itself, not just the customer.
    invoices = client.get("/admin/credit?view=invoices")
    assert f"#{sale.id}" in invoices.text
    assert "فاکتورهای باز" in invoices.text


def test_assigning_due_dates_is_opt_in_and_only_touches_open_invoices(client, db_session, authed):
    db_session.add(Settings(key="credit_terms_days", value="15"))
    db_session.commit()
    customer = _make_customer(db_session, phone="09121110002")
    open_sale = _credit_sale(client, db_session, customer, amount=100_000, due_days=None)
    settled = _credit_sale(client, db_session, customer, amount=100_000, days_ago=3, due_days=None)
    settled.credit_settled = True
    settled.credit_paid_amount = settled.final_amount
    db_session.commit()

    page = client.get("/admin/credit")
    # Amounts and day counts render as ASCII digits app-wide (`fmt`).
    assert "ثبت سررسید 15 روزه برای فاکتورهای باز" in page.text

    response = _post(client, "/admin/credit/assign-due-dates", {}, authed)
    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.get(Sale, open_sale.id).credit_due_date is not None
    assert db_session.get(Sale, settled.id).credit_due_date is None  # already closed


# ── receiving money ──────────────────────────────────────────────────────────

def test_receiving_one_invoice_closes_that_one_and_not_the_oldest(client, db_session, authed):
    """Paying invoice #2 must not quietly settle invoice #1."""
    customer = _make_customer(db_session, phone="09121110003")
    first = _credit_sale(client, db_session, customer, amount=100_000)
    second = _credit_sale(client, db_session, customer, amount=120_000)

    response = _post(client, "/admin/credit/pay", {
        "customer_id": customer.id, "sale_id": second.id,
        "amount": "120000", "method": "cash", "note": "تسویه فاکتور دوم",
    }, authed)
    assert response.status_code == 303
    db_session.expire_all()

    assert db_session.get(Sale, second.id).credit_settled is True
    assert db_session.get(Sale, first.id).credit_settled is False
    payment = db_session.query(Payment).order_by(Payment.id.desc()).first()
    assert payment.sale_id == second.id  # the allocation is recorded, not implied
    assert db_session.get(Customer, customer.id).total_debt == 100_000


def test_over_paying_a_single_invoice_is_refused_outright(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110004")
    sale = _credit_sale(client, db_session, customer, amount=100_000)

    response = _post(client, "/admin/credit/pay", {
        "customer_id": customer.id, "sale_id": sale.id, "amount": "250000", "method": "cash",
    }, authed)
    assert "location" in response.headers
    assert "err=" in response.headers["location"]
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.get(Customer, customer.id).total_debt == 100_000


def test_the_round_payment_form_still_settles_oldest_first(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110005")
    first = _credit_sale(client, db_session, customer, amount=100_000)
    second = _credit_sale(client, db_session, customer, amount=120_000)

    _post(client, "/admin/credit/pay", {
        "customer_id": customer.id, "amount": "150000", "method": "card",
    }, authed)
    db_session.expire_all()
    assert db_session.get(Sale, first.id).credit_settled is True
    assert db_session.get(Sale, second.id).credit_paid_amount == 50_000
    assert db_session.get(Customer, customer.id).total_debt == 70_000


def test_a_receipt_records_who_took_the_money(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110006")
    _credit_sale(client, db_session, customer, amount=100_000)

    _post(client, "/admin/credit/pay", {
        "customer_id": customer.id, "amount": "100000", "method": "cash",
    }, authed)
    db_session.expire_all()
    payment = db_session.query(Payment).order_by(Payment.id.desc()).first()
    assert payment.received_by_id is not None

    page = client.get(f"/admin/credit/{customer.id}")
    assert "دریافت‌کننده" in page.text
    assert "Test Cashier" in page.text or "owner" in page.text


# ── reversals ────────────────────────────────────────────────────────────────

def test_a_reversal_without_a_reason_is_refused(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110007")
    _credit_sale(client, db_session, customer, amount=100_000)
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "100000",
                                        "method": "cash"}, authed)
    db_session.expire_all()
    payment = db_session.query(Payment).order_by(Payment.id.desc()).first()

    response = _post(client, f"/admin/payments/{payment.id}/reverse", {}, authed)
    assert "err=" in response.headers["location"]
    db_session.expire_all()
    assert db_session.get(Payment, payment.id).reversed_at is None
    # Refused means untouched: the receipt stays applied, so the debt stays 0.
    assert db_session.get(Customer, customer.id).total_debt == 0


def test_the_reason_reaches_the_immutable_ledger(client, db_session, authed):
    from models import PaymentReversal

    customer = _make_customer(db_session, phone="09121110008")
    _credit_sale(client, db_session, customer, amount=100_000)
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "100000",
                                        "method": "cash"}, authed)
    db_session.expire_all()
    payment = db_session.query(Payment).order_by(Payment.id.desc()).first()

    response = _post(client, f"/admin/payments/{payment.id}/reverse",
                     {"reason": "مشتری پشیمان شد"}, authed)
    assert response.status_code == 303
    db_session.expire_all()
    reversal = db_session.query(PaymentReversal).filter(
        PaymentReversal.payment_id == payment.id
    ).one()
    assert reversal.reason == "مشتری پشیمان شد"
    assert "Payment reversal" not in reversal.reason
    assert db_session.get(Customer, customer.id).total_debt == 100_000


def test_the_old_delete_path_still_reverses(client, db_session, authed):
    """Existing links and clients keep working; the audit row says where it came from."""
    from models import PaymentReversal

    customer = _make_customer(db_session, phone="09121110009")
    _credit_sale(client, db_session, customer, amount=100_000)
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "100000",
                                        "method": "cash"}, authed)
    db_session.expire_all()
    payment = db_session.query(Payment).order_by(Payment.id.desc()).first()

    response = _post(client, f"/admin/payments/{payment.id}/delete", {}, authed)
    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.get(Payment, payment.id).reversed_at is not None
    reversal = db_session.query(PaymentReversal).filter(
        PaymentReversal.payment_id == payment.id
    ).one()
    assert reversal.reason == "برگشت دریافت (مسیر قدیمی)"


def test_an_invoice_tagged_receipt_keeps_its_invoice_across_a_rebuild(client, db_session, authed):
    """Reversing one receipt re-allocates the rest, but honours their invoices."""
    customer = _make_customer(db_session, phone="09121110010")
    first = _credit_sale(client, db_session, customer, amount=100_000)
    second = _credit_sale(client, db_session, customer, amount=100_000)

    # Pay the second invoice deliberately, then the first with a round amount.
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "sale_id": second.id,
                                        "amount": "100000", "method": "cash"}, authed)
    _post(client, "/admin/credit/pay", {"customer_id": customer.id,
                                        "amount": "100000", "method": "cash"}, authed)
    db_session.expire_all()
    tagged = db_session.query(Payment).filter(Payment.sale_id == second.id).one()

    _post(client, f"/admin/payments/{tagged.id}/reverse", {"reason": "اشتباه ثبت شد"}, authed)
    db_session.expire_all()
    # The remaining (untagged) receipt now covers the oldest invoice…
    assert db_session.get(Sale, first.id).credit_settled is True
    assert db_session.get(Sale, second.id).credit_settled is False
    assert db_session.get(Customer, customer.id).total_debt == 100_000


# ── reminders ────────────────────────────────────────────────────────────────

def test_reminder_is_queued_with_the_stored_pattern_and_marked(client, db_session, authed):
    from models import Settings as SettingsModel

    db_session.add(SettingsModel(key="sms_pattern_credit_reminder",
                                 value="سلام {var1}، بدهی شما {var2} تومان است. سررسید: {var3}"))
    db_session.commit()
    customer, sale = _debtor(client, db_session, phone="09121110011", name="یادآوری")
    sale.credit_due_date = datetime.now(timezone.utc) - timedelta(days=5)
    db_session.commit()

    page = client.get(f"/admin/credit/{customer.id}")
    assert "یادآوری پیامکی" in page.text

    response = _post(client, f"/admin/credit/{customer.id}/remind", {}, authed)
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]
    assert db_session.query(SettingsModel).filter(
        SettingsModel.key == f"credit_reminder_{customer.id}"
    ).first() is not None


def test_a_second_reminder_inside_the_cool_down_is_refused(client, db_session, authed):
    db_session.add(Settings(key="sms_pattern_credit_reminder", value="بدهی {var2}"))
    db_session.add(Settings(key="credit_reminder_min_hours", value="24"))
    db_session.commit()
    customer, _ = _debtor(client, db_session, phone="09121110012", name="سردی")

    first = _post(client, f"/admin/credit/{customer.id}/remind", {}, authed)
    assert "msg=" in first.headers["location"]
    second = _post(client, f"/admin/credit/{customer.id}/remind", {}, authed)
    assert "err=" in second.headers["location"]

    page = client.get(f"/admin/credit/{customer.id}")
    assert "disabled" in page.text  # the button says why it cannot be pressed


def test_the_bulk_reminder_respects_the_cap(client, db_session, authed):
    db_session.add(Settings(key="sms_pattern_credit_reminder", value="بدهی {var2}"))
    db_session.add(Settings(key="campaign_sms_limit", value="1"))
    db_session.commit()
    for index in range(3):
        customer, sale = _debtor(client, db_session, phone=f"0912112010{index}",
                                 name=f"سررسید{index}")
        sale.credit_due_date = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.commit()

    page = client.get("/admin/credit")
    assert "ارسال یادآوری برای سررسیدگذشته‌ها" in page.text

    response = _post(client, "/admin/credit/remind-overdue", {}, authed)
    assert response.status_code == 303
    marks = db_session.query(Settings).filter(
        Settings.key.like("credit_reminder_%")
    ).count()
    assert marks == 1  # capped, like a campaign


def test_no_pattern_means_no_reminder_button(client, db_session, authed):
    customer, _ = _debtor(client, db_session, phone="09121110013", name="بی‌الگو")
    page = client.get(f"/admin/credit/{customer.id}")
    assert "یادآوری پیامکی" not in page.text
    assert "تنظیمات" in page.text


# ── statement ────────────────────────────────────────────────────────────────

def test_statement_balances_and_prints(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110014", first_name="صورت‌حساب")
    _credit_sale(client, db_session, customer, amount=100_000)
    _credit_sale(client, db_session, customer, amount=250_000)
    _post(client, "/admin/credit/pay", {"customer_id": customer.id, "amount": "120000",
                                        "method": "cash", "note": "قسط"}, authed)

    page = client.get(f"/admin/credit/{customer.id}/statement")
    assert page.status_code == 200
    assert "صورت‌حساب" in page.text
    assert "قسط" in page.text
    # Debits − credits must equal what the page closes on: 350k − 120k.
    # Amounts render through `fmt`, i.e. ASCII digits and commas app-wide.
    assert "230,000" in page.text
    assert "350,000" in page.text
    assert "120,000" in page.text


def test_statement_range_keeps_an_opening_balance(client, db_session, authed):
    customer = _make_customer(db_session, phone="09121110015", first_name="بازه")
    older = _credit_sale(client, db_session, customer, amount=100_000)
    older.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    _credit_sale(client, db_session, customer, amount=150_000)
    db_session.commit()

    today = datetime.now(timezone.utc)
    start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    page = client.get(f"/admin/credit/{customer.id}/statement?start_date={start}")
    assert page.status_code == 200
    assert "مانده از قبل" in page.text


def test_statement_requires_the_manager(client, db_session):
    customer = _make_customer(db_session, phone="09121110016")
    response = client.get(f"/admin/credit/{customer.id}/statement", follow_redirects=False)
    assert response.status_code in (303, 401, 403)


# ── the list itself ──────────────────────────────────────────────────────────

def test_filters_sort_and_pagination_share_one_rule(client, db_session, authed):
    for index in range(3):
        customer, sale = _debtor(client, db_session, phone=f"0912113010{index}",
                                 name=f"بدهکار{index}", amount=100_000 * (index + 1))
        sale.credit_due_date = datetime.now(timezone.utc) - timedelta(days=10 * (index + 1))
    db_session.commit()

    biggest = client.get("/admin/credit?sort=debt")
    assert "بدهکار2" in biggest.text

    latest = client.get("/admin/credit?sort=lateness")
    assert latest.status_code == 200

    overdue = client.get("/admin/credit?status=overdue")
    assert "بدهکار0" in overdue.text

    empty = client.get("/admin/credit?search=ناموجود‌آمیز")
    assert "بدهکاری با این فیلترها پیدا نشد" in empty.text


def test_the_page_costs_a_fixed_number_of_queries_per_debtor(client, db_session, authed):
    """The old page ran four queries per debtor; this one does not grow like that."""
    from sqlalchemy import event

    for index in range(12):
        _debtor(client, db_session, phone=f"091211401{index:02d}", name=f"انبوه{index}")
    db_session.commit()

    counted = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", _count)
    try:
        rows = build_debt_rows(db_session)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _count)

    assert len(rows) == 12
    assert len(counted) <= 6, counted
