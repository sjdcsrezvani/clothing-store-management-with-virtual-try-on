"""Inventory ledger page: filters, running balances, actor trail, reconciliation."""
import re
from datetime import datetime, timezone

from models import BusinessEvent, Purchase, ProductVariant, Sale, StaffUser, StockMovement
from services.inventory import (
    ledger_mismatched_variants,
    ledger_missing_variants,
    ledger_snapshot,
    record_cost_adjustment,
    record_stock_adjustment,
    record_stock_movement,
)
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _make_variant

LEDGER_URL = "/admin/inventory-movements"


def _post(client, url, data=None):
    payload = dict(data or {})
    payload["csrf_token"] = csrf_token(client, LEDGER_URL)
    return client.post(url, data=payload, follow_redirects=False)


def _rows(page_text: str) -> str:
    """Only the table body, so filter/label text can't satisfy an assertion."""
    match = re.search(r"<tbody>(.*?)</tbody>", page_text, re.S)
    return match.group(1) if match else ""


def test_ledger_page_uses_the_catalog_design_language(client, db_session, authed):
    _make_variant(db_session, stock=5, name="دفتر تست")

    page = client.get(LEDGER_URL)

    assert page.status_code == 200
    # The hand-written pseudo-path became a real breadcrumb: section, then the
    # owning destination as a link, then this page.
    assert '<nav class="breadcrumb"' in page.text
    assert "کالا و انبار" in page.text
    assert 'href="/admin/products"' in page.text
    assert '<h1>' in page.text
    assert '<div class="page-heading product-page-heading">' in page.text
    # The nav belongs in the heading, not as a trailing nav at the bottom.
    heading = page.text.index('<div class="page-heading product-page-heading">')
    assert heading < page.text.index('class="admin-nav"') < page.text.index("<section")
    # Emoji headings and emoji movement types are gone.
    assert "<title>📚" not in page.text
    for emoji in ("📥", "📦", "🛒", "🛠️", "💵"):
        assert emoji not in page.text, f"emoji {emoji} still rendered"


def test_ledger_is_manager_only(client, db_session):
    cashier, password = _staff(db_session, "ledger-cashier", "cashier")
    _session_as(client, cashier, password)

    assert client.get(LEDGER_URL, follow_redirects=False).status_code == 403
    assert client.post(f"{LEDGER_URL}/reconcile", data={}, follow_redirects=False).status_code == 403


def test_balance_after_is_derived_from_the_ledger(client, db_session, authed):
    """Opening 5 → adjusted to 9 → sold 4 leaves 5/9/5, newest row first."""
    _, variant = _make_variant(db_session, stock=0, name="مانده دفتری")
    record_stock_movement(db_session, variant, 5, "opening_stock", note="موجودی اولیه")
    record_stock_adjustment(db_session, variant, 9, note="اصلاح دستی")
    record_stock_movement(db_session, variant, -4, "sale", note="فروش تست")
    db_session.commit()

    page = client.get(LEDGER_URL)

    balances = re.findall(r'movement-balance">(.*?)</td>', page.text, re.S)
    values = [int(re.search(r"<strong>(-?\d+)</strong>", block).group(1)) for block in balances]
    assert values == [5, 9, 5]
    # The ledger balances agree with the cached balance, so nothing is flagged.
    assert "نامطابق" not in _rows(page.text)
    assert variant.stock_quantity == 5


def test_mismatch_is_flagged_and_not_hidden(client, db_session, authed):
    """Pre-ledger stock and a drifted balance are both surfaced, never silently fixed."""
    _, legacy = _make_variant(db_session, stock=6, cost=1000, name="بدون سابقه")
    _, drift = _make_variant(db_session, stock=0, cost=1000, name="نامطابق تست")
    record_stock_movement(db_session, drift, 5, "opening_stock", note="موجودی اولیه")
    drift.stock_quantity = 2  # a balance that no longer matches its own ledger
    db_session.commit()

    assert [v.id for v in ledger_missing_variants(db_session)] == [legacy.id]
    assert [v.id for v, _ in ledger_mismatched_variants(db_session)] == [drift.id]

    page = client.get(LEDGER_URL)
    assert "تنوع با موجودی بدون سابقه" in page.text
    assert "تنوع نامطابق با دفتر" in page.text
    # The drifted variant's own movement row carries the warning.
    assert "نامطابق" in _rows(page.text)
    assert "اکنون: 2" in _rows(page.text)

    # Both flags open a listing of the offending variants, not the ledger table.
    listing = client.get(f"{LEDGER_URL}?reconcile=missing")
    assert listing.status_code == 200
    assert "بدون سابقه" in listing.text
    assert "حرکت‌های ثبت‌شده" not in listing.text

    mismatch = client.get(f"{LEDGER_URL}?reconcile=mismatch")
    assert "نامطابق تست" in mismatch.text
    assert "حرکت‌های ثبت‌شده" not in mismatch.text


def test_filters_narrow_the_ledger(client, db_session, authed):
    _, first = _make_variant(db_session, stock=0, name="تیشرت راه‌راه")
    _, second = _make_variant(db_session, stock=0, name="شلوار جین")
    first.barcode = "90002"
    record_stock_movement(db_session, first, 5, "opening_stock", note="ورود الف")
    record_stock_movement(db_session, first, -2, "sale", note="خروج الف")
    record_stock_movement(db_session, second, 3, "opening_stock", note="ورود ب")
    db_session.commit()

    # Direction
    incoming = _rows(client.get(f"{LEDGER_URL}?direction=in").text)
    assert "ورود الف" in incoming and "خروج الف" not in incoming and "ورود ب" in incoming
    outgoing = _rows(client.get(f"{LEDGER_URL}?direction=out").text)
    assert "خروج الف" in outgoing and "ورود الف" not in outgoing

    # Movement type
    sales_only = _rows(client.get(f"{LEDGER_URL}?movement_type=sale").text)
    assert "خروج الف" in sales_only and "ورود ب" not in sales_only

    # Product name search
    by_name = _rows(client.get(f"{LEDGER_URL}?q=شلوار").text)
    assert "ورود ب" in by_name and "ورود الف" not in by_name

    # Barcode search
    by_barcode = _rows(client.get(f"{LEDGER_URL}?q=90002").text)
    assert "ورود الف" in by_barcode and "ورود ب" not in by_barcode

    # Movement id search
    grouped = ledger_snapshot(db_session, [first.id, second.id])
    newest_id = max(grouped["by_movement"])
    by_id = _rows(client.get(f"{LEDGER_URL}?q=%23{newest_id}").text)
    assert len(re.findall(r'href="#movement-', by_id)) == 1

    # Date range that excludes everything in the past
    future = _rows(client.get(f"{LEDGER_URL}?start_date=2099-01-01").text)
    assert future.strip() == ""
    assert "حرکتی با این فیلترها پیدا نشد" in client.get(f"{LEDGER_URL}?start_date=2099-01-01").text

    # A type filter that matches nothing shows the filter empty state, not a blank page
    none = client.get(f"{LEDGER_URL}?movement_type=cost_adjustment")
    assert "حرکتی با این فیلترها پیدا نشد" in none.text


def test_pagination_reports_the_real_total(client, db_session, authed):
    """The page used to silently cap at 200 rows with no count."""
    _, variant = _make_variant(db_session, stock=0, name="صفحه‌بندی")
    for _ in range(30):
        record_stock_movement(db_session, variant, 1, "adjustment", note="اصلاح تست")
    db_session.commit()

    first = client.get(LEDGER_URL)
    assert "صفحه 1 از 2" in first.text
    assert "از 30 حرکت" in first.text
    assert "نمایش 1–25" in first.text

    second = client.get(f"{LEDGER_URL}?page=2")
    assert second.status_code == 200
    assert "نمایش 26–30 از 30 حرکت" in second.text
    assert len(re.findall(r'href="#movement-', second.text)) == 5


def test_actor_trail_and_filter(client, db_session, authed):
    manager = db_session.query(StaffUser).filter(StaffUser.username == "owner").first()
    manager.full_name = "مدیر تست"
    _, variant = _make_variant(db_session, stock=4, name="کاربر تست")
    record_stock_movement(
        db_session, variant, 4, "opening_stock", note="موجودی اولیه", actor_user_id=manager.id,
    )
    db_session.commit()

    page = client.get(LEDGER_URL)
    assert "مدیر تست" in _rows(page.text)

    mine = client.get(f"{LEDGER_URL}?actor={manager.id}")
    assert "موجودی اولیه" in _rows(mine.text)

    someone_else = client.get(f"{LEDGER_URL}?actor=999999")
    assert "حرکتی با این فیلترها پیدا نشد" in someone_else.text


def test_cost_adjustment_shows_before_and_after(client, db_session, authed):
    _, variant = _make_variant(db_session, cost=42_000, stock=3, name="بهای تست")
    record_cost_adjustment(
        db_session, variant, 42_000, 46_500, note="به‌روزرسانی بهای تمام‌شده از خرید #1",
    )
    db_session.commit()

    page = client.get(LEDGER_URL)

    assert "42,000 → 46,500" in page.text
    # A cost row moves no quantity, so it must not look like a stock change.
    assert "movement-delta is-zero" in page.text


def test_references_link_to_their_documents(client, db_session, authed):
    sale = Sale(total_amount=1_000, final_amount=1_000, payment_method="cash")
    purchase = Purchase(total_cost=5_000, note="فاکتور تست")
    db_session.add_all([sale, purchase])
    db_session.flush()
    _, variant = _make_variant(db_session, stock=1, name="مرجع تست")
    record_stock_movement(db_session, variant, -1, "sale", sale_id=sale.id, note="فروش مرجع")
    record_stock_movement(
        db_session, variant, 1, "purchase", purchase_id=purchase.id, note="خرید مرجع",
    )
    db_session.commit()

    page = client.get(LEDGER_URL)

    assert f'href="/sales/invoice/{sale.id}"' in page.text
    assert f'href="/admin/purchases/{purchase.id}"' in page.text
    # Rows written by the old purchase flow are marked so they read as history.
    assert "badge-legacy" in _rows(page.text)


def test_reconcile_records_opening_stock_without_touching_stock(client, db_session, authed):
    """The repair is opt-in, explains pre-ledger stock, and never adds units."""
    _, variant = _make_variant(db_session, stock=6, cost=1_000, name="ثبت اولیه")
    assert db_session.query(StockMovement).count() == 0

    response = _post(client, f"{LEDGER_URL}/reconcile")

    assert response.status_code == 303
    db_session.expire_all()
    refreshed = db_session.query(ProductVariant).filter(ProductVariant.id == variant.id).one()
    movement = db_session.query(StockMovement).filter(StockMovement.variant_id == variant.id).one()
    assert movement.quantity_delta == 6
    assert movement.movement_type == "opening_stock"
    assert movement.unit_cost == 1_000
    assert movement.purchase_id is None
    assert refreshed.stock_quantity == 6  # the units were already on the shelf

    # The movement is auditable like every other one.
    event = db_session.query(BusinessEvent).filter(
        BusinessEvent.idempotency_key == f"stock-movement:{movement.id}"
    ).one()
    assert event.actor_user_id is not None
    assert ledger_missing_variants(db_session) == []
    assert ledger_snapshot(db_session, [variant.id])["totals"][variant.id] == 6

    # Running it again changes nothing.
    assert _post(client, f"{LEDGER_URL}/reconcile").status_code == 303
    db_session.expire_all()
    assert db_session.query(StockMovement).filter(StockMovement.variant_id == variant.id).count() == 1
    assert db_session.query(ProductVariant).filter(ProductVariant.id == variant.id).one().stock_quantity == 6


def test_reconcile_only_explains_stock_the_ledger_never_saw(client, db_session, authed):
    """A variant that already has history keeps the history it has."""
    _, known = _make_variant(db_session, stock=0, name="سابقه‌دار")
    _, unknown_variant = _make_variant(db_session, stock=3, name="بی‌سابقه")
    record_stock_movement(db_session, known, 2, "opening_stock", note="موجودی اولیه")
    db_session.commit()

    _post(client, f"{LEDGER_URL}/reconcile")
    db_session.expire_all()

    known_movements = db_session.query(StockMovement).filter(
        StockMovement.variant_id == known.id
    ).all()
    assert len(known_movements) == 1
    assert known_movements[0].note == "موجودی اولیه"

    opening = db_session.query(StockMovement).filter(
        StockMovement.variant_id == unknown_variant.id
    ).one()
    assert opening.quantity_delta == 3
    assert opening.created_at is not None
