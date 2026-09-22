"""Ledger tables (Phase 4): one system, sortable money and dates.

Sorting answers unknown keys with the default order — a hand-typed
?sort=anything is a list, never an error.
"""
from datetime import datetime, timezone
from pathlib import Path

from models import Sale
from services.sorting import parse_sort, toggle_direction
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]


def _login(client):
    token = csrf_token(client)
    response = client.post(
        "/admin/login",
        data={"username": "owner", "password": "test-admin-pass", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_sort_helper_defaults_and_toggles():
    assert parse_sort({}, {"date": "desc", "amount": "desc"}, "date") == ("date", "desc")
    assert parse_sort({"sort": "amount"}, {"date": "desc", "amount": "desc"}, "date") == ("amount", "desc")
    assert parse_sort({"sort": "nope", "dir": "sideways"}, {"date": "desc"}, "date") == ("date", "desc")
    assert toggle_direction("date", "desc", "date", "desc") == "asc"
    assert toggle_direction("date", "asc", "date", "desc") == "desc"
    assert toggle_direction("date", "desc", "amount", "desc") == "desc"


def _two_sales(db_session):
    cheap = Sale(total_amount=100_000, final_amount=100_000, payment_method="card",
                 payment_confirmed=True, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    pricey = Sale(total_amount=900_000, final_amount=900_000, payment_method="card",
                  payment_confirmed=True, created_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    db_session.add_all([cheap, pricey])
    db_session.commit()
    return cheap, pricey


def _ids_in_order(html, first_id, second_id):
    return html.index(f"#{first_id}</strong>") < html.index(f"#{second_id}</strong>")


def test_sales_sort_by_amount_ascending(client, db_session):
    _login(client)
    cheap, pricey = _two_sales(db_session)
    html = client.get("/sales?sort=amount&dir=asc").text
    assert _ids_in_order(html, cheap.id, pricey.id)
    assert 'aria-sort="ascending"' in html


def test_sales_default_is_newest_first_and_unknown_sorts_fall_back(client, db_session):
    _login(client)
    cheap, pricey = _two_sales(db_session)
    default = client.get("/sales").text
    assert _ids_in_order(default, pricey.id, cheap.id)
    fallback = client.get("/sales?sort=banana&dir=sideways").text
    assert _ids_in_order(fallback, pricey.id, cheap.id)
    assert 'sort=amount' in default  # the header links are on the page


def test_purchases_sort_links_carry_filters(client, db_session):
    """Structure is a template fact (the table hides on an empty shop);
    the route must still answer unknown sorts with the default list."""
    source = (ROOT / "templates" / "admin" / "purchases.html").read_text()
    assert "sort=date" in source and "sort=total" in source
    assert "sort_base_qs" in source and 'aria-sort=' in source
    _login(client)
    resp = client.get("/admin/purchases?sort=nope&dir=nope")
    assert resp.status_code == 200


def test_expenses_unknown_sort_falls_back(client, db_session):
    source = (ROOT / "templates" / "admin" / "expenses.html").read_text()
    assert "sort=date" in source and "sort=amount" in source
    assert 'aria-sort=' in source
    _login(client)
    resp = client.get("/admin/expenses?sort=nope&dir=nope")
    assert resp.status_code == 200


def test_data_table_everywhere_matrix_nowhere():
    import re
    for name in ("sales", "purchases", "expenses", "sms_history", "customers",
                 "credit", "suppliers", "checks", "logs", "events",
                 "campaigns", "birthdays", "backups", "staff",
                 "cashbox", "tier_up", "tier_downgrades"):
        source = (ROOT / "templates" / "admin" / f"{name}.html").read_text()
        if "<table" in source:
            assert "data-table" in source, name
    matrix = (ROOT / "templates" / "admin" / "analytics.html").read_text()
    assert 'class="matrix"' in matrix and "data-table matrix" not in matrix


def test_empty_states_share_one_shape():
    for name, heading in (
        ("sales", "فروشی ثبت نشده است"),
        ("suppliers", "هنوز تأمین‌کننده‌ای ثبت نشده است"),
        ("accounting", "هزینه‌ای در این بازه ثبت نشده است"),
        ("tier_up", "هیچ مشتری برای ارسال پیامک ارتقا وجود ندارد"),
    ):
        source = (ROOT / "templates" / "admin" / f"{name}.html").read_text()
        assert '<div class="empty-state">' in source, name
        assert "<h3>" in source, name
        assert heading in source, name


def test_analytics_pill_keeps_deep_links():
    source = (ROOT / "templates" / "admin" / "analytics.html").read_text()
    assert "tabs-sliding" in source
    assert "tabs-pill" in source
    assert 'aria-current="page"' in source
    assert "tab={{ value }}&period={{ period }}" in source
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".tabs-pill" in style and ".tab-link" in style
