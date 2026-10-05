"""Sales ledger phase 1: filters, per-page, refund badges, no emoji."""
from models import Sale
from tests.test_tables import _login


def _mix(db_session):
    rows = [
        Sale(total_amount=100_000, final_amount=100_000, payment_method="card",
             payment_confirmed=True),
        Sale(total_amount=200_000, final_amount=200_000, payment_method="cash",
             payment_confirmed=True, is_refunded=True, refund_reason="اشتباه"),
        Sale(total_amount=300_000, final_amount=300_000, payment_method="credit",
             payment_confirmed=True),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def test_method_and_refunded_filters_combine(client, db_session):
    _login(client)
    card, cash, credit = _mix(db_session)
    page = client.get("/sales?method=cash&refunded=yes").text
    assert f"#{cash.id}</strong>" in page
    assert f"#{card.id}</strong>" not in page
    assert f"#{credit.id}</strong>" not in page
    assert "ابطال شد" in page and 'title="اشتباه"' in page


def test_refunded_no_and_garbage_degrade(client, db_session):
    _login(client)
    card, cash, credit = _mix(db_session)
    page = client.get("/sales?refunded=no").text
    assert f"#{cash.id}</strong>" not in page
    assert f"#{card.id}</strong>" in page
    garbage = client.get("/sales?method=nope&refunded=maybe&per_page=7").text
    assert f"#{cash.id}</strong>" in garbage  # unknown values read as all/25


def test_ledger_has_no_emoji_and_names_its_columns(client, db_session):
    _login(client)
    _mix(db_session)
    page = client.get("/sales").text
    for emoji in ("📄", "👤", "🕐", "📦", "🏷️", "💰", "🌟", "💳", "🔧", "🧾", "🛒", "🔍", "📒", "💵"):
        assert emoji not in page
    for heading in ("شماره", "مشتری", "تاریخ", "وضعیت", "عملیات"):
        assert heading in page
    assert "sales-table" in page
    assert "فروش جدید" in page
