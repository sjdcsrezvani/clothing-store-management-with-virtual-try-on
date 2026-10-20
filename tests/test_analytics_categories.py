"""The category/size tab reads products through one band and one sales line.

Age is derived from the band, never from the raw tag. The enriched net line is
the single source for the new category, type, group and system readings.
"""
from datetime import datetime, timedelta, timezone

import pytest

from models import (
    Product,
    ProductVariant,
    Refund,
    Sale,
    SaleItem,
    StaffUser,
)
from services.analytics import (
    _net_sale_lines,
    get_daily_revenue,
    get_margin_by_category,
    get_revenue_by_category,
)
from services.sizing import Band, band_age_group, resolve_band


def _product(db, name, *, category="پیراهن", system="age", size_type="upper",
             gender=None):
    product = Product(
        name=name, category=category, size_system=system,
        size_type=size_type, gender=gender, is_active=True,
    )
    db.add(product)
    db.flush()
    return product


def _variant(db, product, size, *, stock=5, barcode):
    band = resolve_band(product.size_system, size, product.size_type, product.gender)
    variant = ProductVariant(
        product_id=product.id, size=size, color="مشکی", price=500_000,
        cost_price=200_000, stock_quantity=stock, barcode=barcode,
        band_min_cm=band.height_min if band else None,
        band_max_cm=band.height_max if band else None,
        band_min_months=band.months_min if band else None,
        band_max_months=band.months_max if band else None,
        band_min_adult=band.adult_min if band else None,
        band_max_adult=band.adult_max if band else None,
    )
    db.add(variant)
    db.flush()
    return variant


def _sell(db, now, *pairs, discount=0):
    total = 500_000 * len(pairs)
    sale = Sale(
        total_amount=total, discount_amount=discount,
        final_amount=total - discount, payment_method="cash",
        payment_confirmed=True, created_at=now,
    )
    db.add(sale)
    db.flush()
    for product, variant in pairs:
        db.add(SaleItem(
            sale_id=sale.id, product_id=product.id, variant_id=variant.id,
            quantity=1, unit_price=500_000, unit_cost=200_000,
            total_price=500_000,
        ))
    db.commit()
    return sale


def _refund(db, sale, amount, now):
    operator = StaffUser(username=f"refund-{sale.id}", password_hash="x", role="owner")
    db.add(operator)
    db.flush()
    refund = Refund(
        sale_id=sale.id, operator_user_id=operator.id,
        total_amount=amount, reason="سایز", created_at=now,
    )
    db.add(refund)
    db.commit()
    return refund


def _window():
    now = datetime.now(timezone.utc)
    return now, now - timedelta(days=1), now + timedelta(days=1)


@pytest.mark.parametrize("months, expected", [
    (35, "baby"),
    (36, "child"),
    (95, "child"),
    (96, "teen"),
    (191, "teen"),
    (192, "adult"),
])
def test_age_group_follows_the_band_boundaries(months, expected):
    band = Band(None, None, months, months)
    assert band_age_group(band) == expected


def test_age_group_reads_adult_bands_and_missing_bands():
    assert band_age_group(Band(None, None, None, None, 38, 38)) == "adult"
    assert band_age_group(None) is None
    # A band that straddles 0–2 and 3–7 uses its middle, not a boundary guess.
    assert band_age_group(Band(None, None, 24, 36)) == "baby"
    assert band_age_group(Band(None, None, 84, 96)) == "child"


def test_net_lines_carry_product_and_band_dimensions(db_session):
    now, start, end = _window()
    kid = _product(db_session, "پیراهن بچگانه", category="پیراهن", gender="girl")
    adult = _product(db_session, "پیراهن بزرگسال", category="پیراهن", system="adult",
                     gender="woman")
    lower = _product(db_session, "شلوار", category="شلوار", size_type="lower",
                     gender="boy")
    unknown = _product(db_session, "بدون معادل", category="تیشرت")
    v_kid = _variant(db_session, kid, "6-7", barcode="CAT-KID")
    v_adult = _variant(db_session, adult, "38", barcode="CAT-ADULT")
    v_lower = _variant(db_session, lower, "6-7", barcode="CAT-LOWER")
    v_unknown = _variant(db_session, unknown, "??", barcode="CAT-UNKNOWN")
    sale = _sell(db_session, now, (kid, v_kid), (adult, v_adult),
                 (lower, v_lower), (unknown, v_unknown))
    _refund(db_session, sale, 200_000, now)

    item_lines, refund_lines, _ = _net_sale_lines(db_session, start, end)
    by_variant = {line["variant_id"]: line for line in item_lines}

    kid_line = by_variant[v_kid.id]
    assert kid_line["category"] == "پیراهن"
    assert kid_line["gender"] == "girl"
    assert kid_line["size_type"] == "upper"
    assert kid_line["size_system"] == "age"
    assert kid_line["age_group"] == "child"

    assert by_variant[v_adult.id]["age_group"] == "adult"
    assert by_variant[v_adult.id]["size_system"] == "adult"
    assert by_variant[v_lower.id]["size_type"] == "lower"
    assert by_variant[v_lower.id]["gender"] == "boy"
    assert by_variant[v_unknown.id]["age_group"] is None
    assert by_variant[v_unknown.id]["size_system"] == "age"

    assert refund_lines
    for line in refund_lines:
        assert line["variant_id"] in by_variant
        assert line["category"] in {"پیراهن", "شلوار", "تیشرت"}
        assert "gender" in line and "size_type" in line
        assert "size_system" in line and "age_group" in line


def test_dimensions_do_not_change_the_net_totals(db_session):
    now, start, end = _window()
    product = _product(db_session, "پیراهن", category="پیراهن", gender="girl")
    variant = _variant(db_session, product, "6-7", barcode="CAT-TOTAL")
    sale = _sell(db_session, now, (product, variant), discount=100_000)
    _refund(db_session, sale, 150_000, now)

    item_lines, refund_lines, _ = _net_sale_lines(db_session, start, end)
    expected = sum(line["net"] for line in item_lines) - sum(
        line["amount"] for line in refund_lines)

    daily = get_daily_revenue(db_session, start, end)
    categories = get_revenue_by_category(db_session, start, end)
    margins = get_margin_by_category(db_session, start, end)

    assert sum(row["revenue"] for row in daily) == expected
    assert sum(row["revenue"] for row in categories) == expected
    assert sum(row["revenue"] for row in margins) == expected
