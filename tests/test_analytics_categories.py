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
    RefundLine,
    Sale,
    SaleItem,
    StaffUser,
)
from services.analytics import (
    _net_sale_lines,
    get_category_age_sales,
    get_category_size_matrix,
    get_daily_revenue,
    get_group_type_sales,
    get_margin_by_category,
    get_returns_by_band,
    get_revenue_by_category,
    get_sales_by_size_system,
    get_sizing_coverage,
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


def _refund(db, sale, amount, now, *, item=None, quantity=0):
    operator = StaffUser(username=f"refund-{sale.id}", password_hash="x", role="owner")
    db.add(operator)
    db.flush()
    refund = Refund(
        sale_id=sale.id, operator_user_id=operator.id,
        total_amount=amount, reason="سایز", created_at=now,
    )
    db.add(refund)
    db.flush()
    if item is not None:
        db.add(RefundLine(
            refund_id=refund.id, sale_item_id=item.id,
            quantity=quantity, amount=amount,
        ))
    db.commit()
    return refund


def _sale_item(db, sale):
    return db.query(SaleItem).filter(SaleItem.sale_id == sale.id).first()


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


def test_group_and_type_sales_keep_unclassified_rows(db_session):
    now, start, end = _window()
    girl = _product(db_session, "پیراهن دخترانه", category="پیراهن",
                    gender="girl", size_type="upper")
    boy = _product(db_session, "شلوار پسرانه", category="شلوار",
                   gender="boy", size_type="lower")
    unknown = _product(db_session, "بدون دسته", category=None, system=None,
                       size_type=None, gender=None)
    v_girl = _variant(db_session, girl, "6-7", barcode="GT-GIRL")
    v_boy = _variant(db_session, boy, "6-7", barcode="GT-BOY")
    v_unknown = _variant(db_session, unknown, "??", barcode="GT-NONE")
    _sell(db_session, now, (girl, v_girl), (girl, v_girl))
    _sell(db_session, now, (boy, v_boy))
    _sell(db_session, now, (unknown, v_unknown))

    data = get_group_type_sales(db_session, start, end)
    groups = {row["key"]: row for row in data["group"]}
    types = {row["key"]: row for row in data["type"]}

    assert (groups["girl"]["units"], groups["girl"]["revenue"]) == (2, 1_000_000)
    assert groups["none"]["label"] == "نامشخص"
    assert groups["none"]["units"] == 1
    assert (types["upper"]["units"], types["upper"]["revenue"]) == (2, 1_000_000)
    assert types["none"]["units"] == 1

    filtered = get_group_type_sales(
        db_session, start, end, {"groups": ["girl"], "types": ["upper"]})
    assert [row["key"] for row in filtered["group"]] == ["girl"]
    assert [row["key"] for row in filtered["type"]] == ["upper"]

    only_none = get_group_type_sales(db_session, start, end, {"categories": ["none"]})
    assert [row["key"] for row in only_none["group"]] == ["none"]


def test_category_age_sales_stack_the_four_bands_and_unknown(db_session):
    now, start, end = _window()
    kid = _product(db_session, "پیراهن بچگانه", category="پیراهن")
    adult = _product(db_session, "پیراهن بزرگسال", category="پیراهن",
                     system="adult", gender="woman")
    baby = _product(db_session, "شلوار نوزاد", category="شلوار")
    teen = _product(db_session, "شلوار نوجوان", category="شلوار")
    unknown = _product(db_session, "بدون دسته", category=None, system=None)
    v_kid = _variant(db_session, kid, "6-7", barcode="CA-KID")
    v_adult = _variant(db_session, adult, "38", barcode="CA-ADULT")
    v_baby = _variant(db_session, baby, "12m", barcode="CA-BABY")
    v_teen = _variant(db_session, teen, "12", barcode="CA-TEEN")
    v_unknown = _variant(db_session, unknown, "??", barcode="CA-NONE")
    _sell(db_session, now, (kid, v_kid), (kid, v_kid))
    _sell(db_session, now, (adult, v_adult))
    _sell(db_session, now, (baby, v_baby))
    _sell(db_session, now, (teen, v_teen))
    _sell(db_session, now, (unknown, v_unknown))

    data = get_category_age_sales(db_session, start, end)
    assert data["categories"] == ["پیراهن", "شلوار", "بدون دسته"]
    series = {row["key"]: row for row in data["series"]}
    assert series["child"]["revenue"] == [1_000_000, 0, 0]
    assert series["adult"]["revenue"] == [500_000, 0, 0]
    assert series["baby"]["revenue"] == [0, 500_000, 0]
    assert series["teen"]["revenue"] == [0, 500_000, 0]
    assert series["none"]["revenue"] == [0, 0, 500_000]
    assert data["totals"]["revenue"] == 3_000_000

    child_only = get_category_age_sales(db_session, start, end, {"ages": ["child"]})
    assert child_only["categories"] == ["پیراهن"]
    assert {row["key"]: row["revenue"] for row in child_only["series"]}["child"] == [1_000_000]


def test_category_size_matrix_merges_tag_spellings_and_sorts_by_band(db_session):
    now, start, end = _window()
    age = _product(db_session, "پیراهن سنی", category="پیراهن", system="age")
    iran = _product(db_session, "پیراهن ایرانی", category="پیراهن", system="iran")
    adult = _product(db_session, "پیراهن بزرگسال", category="پیراهن",
                     system="adult", gender="woman")
    unknown = _product(db_session, "تیشرت بدون معادل", category="تیشرت",
                       system=None, size_type=None)
    v_age = _variant(db_session, age, "6-7", barcode="CS-AGE")
    v_iran = _variant(db_session, iran, "50", barcode="CS-IRAN")
    v_adult = _variant(db_session, adult, "38", barcode="CS-ADULT")
    v_unknown = _variant(db_session, unknown, "??", barcode="CS-NONE")
    _sell(db_session, now, (age, v_age))
    _sell(db_session, now, (iran, v_iran))
    _sell(db_session, now, (adult, v_adult))
    _sell(db_session, now, (unknown, v_unknown))

    matrix = get_category_size_matrix(db_session, start, end)
    assert [row["label"] for row in matrix["sizes"]] == ["6-7 سال", "M", "نامشخص"]
    rows = {row["category"]: row for row in matrix["rows"]}
    assert rows["پیراهن"]["cells"][0]["qty"] == 2
    assert rows["پیراهن"]["cells"][1]["qty"] == 1
    assert rows["تیشرت"]["cells"][2]["qty"] == 1

    only_category = get_category_size_matrix(
        db_session, start, end, {"categories": ["پیراهن"]})
    assert [row["category"] for row in only_category["rows"]] == ["پیراهن"]
    assert {row["label"] for row in only_category["sizes"]} == {"6-7 سال", "M"}


def test_returns_by_band_counts_partial_refund_lines(db_session):
    now, start, end = _window()
    product = _product(db_session, "پیراهن", category="پیراهن", gender="girl")
    variant = _variant(db_session, product, "6-7", barcode="RB-ONE")
    sale = _sell(db_session, now, (product, variant))
    item = _sale_item(db_session, sale)
    item.quantity = 4
    item.total_price = 2_000_000
    sale.total_amount = 2_000_000
    sale.final_amount = 2_000_000
    db_session.commit()
    _refund(db_session, sale, 1_000_000, now, item=item, quantity=2)

    rows = get_returns_by_band(db_session, start, end)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "6-7 سال"
    assert (row["returned_units"], row["sold_units"]) == (2, 4)
    assert row["rate"] == 50.0
    assert row["amount"] == 1_000_000


def test_system_mix_counts_sold_units_and_unknown(db_session):
    now, start, end = _window()
    age = _product(db_session, "پیراهن سنی", category="پیراهن", system="age")
    adult = _product(db_session, "پیراهن بزرگسال", category="پیراهن",
                     system="adult", gender="woman")
    unknown = _product(db_session, "بدون سیستم", category="تیشرت", system=None)
    v_age = _variant(db_session, age, "6-7", barcode="SM-AGE")
    v_adult = _variant(db_session, adult, "38", barcode="SM-ADULT")
    v_unknown = _variant(db_session, unknown, "??", barcode="SM-NONE")
    _sell(db_session, now, (age, v_age), (age, v_age))
    _sell(db_session, now, (adult, v_adult))
    _sell(db_session, now, (unknown, v_unknown))

    rows = {row["key"]: row for row in get_sales_by_size_system(db_session, start, end)}
    assert rows["age"]["units"] == 2
    assert rows["age"]["label"] == "سن (18m/3T/8)"
    assert rows["adult"]["units"] == 1
    assert rows["none"]["units"] == 1
    assert rows["none"]["label"] == "نامشخص"

    age_only = get_sales_by_size_system(db_session, start, end, {"systems": ["age"]})
    assert [row["key"] for row in age_only] == ["age"]


def test_coverage_counts_active_products_and_variants(db_session):
    no_system = _product(db_session, "بدون سیستم", category="تیشرت", system=None,
                         size_type=None)
    partial = _product(db_session, "ناقص", category="پیراهن", system="age")
    complete = _product(db_session, "کامل", category="شلوار", system="age")
    _variant(db_session, no_system, "??", stock=4, barcode="CV-N1")
    _variant(db_session, no_system, "??", stock=4, barcode="CV-N2")
    _variant(db_session, partial, "6-7", stock=3, barcode="CV-P1")
    _variant(db_session, partial, "??", stock=2, barcode="CV-P2")
    _variant(db_session, complete, "8", stock=1, barcode="CV-C1")
    db_session.commit()

    coverage = get_sizing_coverage(db_session)
    assert coverage["products_total"] == 3
    assert coverage["products_with_system"] == 2
    assert coverage["products_without_system"] == 1
    assert coverage["variants_total"] == 5
    assert coverage["variants_with_band"] == 2
    assert coverage["variants_without_band"] == 3
    assert coverage["band_pct"] == 40.0
    assert coverage["system_pct"] == 66.7
    assert [row["name"] for row in coverage["missing"]] == ["بدون سیستم", "ناقص"]
    assert coverage["missing"][0]["reason"] == "بدون سیستم"
    assert coverage["missing"][0]["stock_units"] == 8
    assert coverage["missing"][1]["reason"] == "تنوع بدون باند"
