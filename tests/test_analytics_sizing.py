"""Size analytics read one band.

Two tag spellings for the same size are one row, and every size figure speaks
the shop's chosen unit. The sales charts, the matrix, stock, turnover and dead
stock all read through the same stored band as the product form.
"""
from datetime import datetime, timedelta, timezone

from models import Product, ProductVariant, Sale, SaleItem, Settings
from services.analytics import (
    get_color_size_matrix,
    get_dead_stock,
    get_returns,
    get_stock_by_band,
    get_top_selling_variants,
    get_turnover,
    get_variant_stats,
)
from services.sizing import resolve_band


def _product(db, name, system, gender=None):
    product = Product(name=name, category="پیراهن", size_system=system,
                      size_type="upper", gender=gender, is_active=True)
    db.add(product)
    db.flush()
    return product


def _variant(db, product, size, *, color="مشکی", stock=5, barcode):
    band = resolve_band(product.size_system, size, product.size_type, product.gender)
    variant = ProductVariant(
        product_id=product.id, size=size, color=color, price=500_000,
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


def _sell(db, now, *pairs, refunded=False):
    sale = Sale(
        total_amount=500_000 * len(pairs), final_amount=500_000 * len(pairs),
        payment_method="cash", payment_confirmed=True, created_at=now,
        is_refunded=refunded, refund_reason="سایز" if refunded else None,
    )
    db.add(sale)
    db.flush()
    for product, variant in pairs:
        db.add(SaleItem(sale_id=sale.id, product_id=product.id,
                        variant_id=variant.id, quantity=1, unit_price=500_000,
                        unit_cost=200_000, total_price=500_000))
    db.commit()
    return sale


def _use_setting(db, key, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))
    db.commit()


def _use_standard(db, value):
    _use_setting(db, "sizing_standard", value)


def _use_adult_standard(db, value):
    _use_setting(db, "sizing_standard_adult", value)


def _window():
    now = datetime.now(timezone.utc)
    return now, now - timedelta(days=1), now + timedelta(days=1)


def test_one_band_is_one_row_across_tag_systems(client, db_session, authed):
    now, start, end = _window()
    age = _product(db_session, "پیراهن سنی", "age")
    iran = _product(db_session, "پیراهن ایرانی", "iran")
    v_age = _variant(db_session, age, "6-7", barcode="AN-AGE")
    v_iran = _variant(db_session, iran, "50", barcode="AN-IRAN")
    _sell(db_session, now, (age, v_age), (iran, v_iran))

    stats = get_variant_stats(db_session, start, end, "size")
    assert stats == [{"label": "6-7 سال", "quantity": 2, "revenue": 1_000_000}]

    matrix = get_color_size_matrix(db_session, start, end)
    assert matrix["rows"][0]["size"] == "6-7 سال"
    assert matrix["rows"][0]["cells"][0]["qty"] == 2

    top = get_top_selling_variants(db_session, start, end)
    assert {row["size"] for row in top} == {"6-7 سال"}

    stock = get_stock_by_band(db_session)
    assert stock[0]["size"] == "6-7 سال"
    assert (stock[0]["units"], stock[0]["cost"]) == (10, 2_000_000)

    turnover = get_turnover(db_session, start, end)
    assert {row["size"] for row in turnover["rows"]} == {"6-7 سال"}

    # The unit is a setting, not a stored string: the same bands move together.
    _use_standard(db_session, "height")
    stats = get_variant_stats(db_session, start, end, "size")
    assert stats[0]["label"] == "قد 116 تا 122"
    assert get_stock_by_band(db_session)[0]["size"] == "قد 116 تا 122"
    assert {row["size"] for row in get_turnover(db_session, start, end)["rows"]} == {"قد 116 تا 122"}

    # Dead stock and returns use the same label as the sold rows.
    dead_product = _product(db_session, "پیراهن بدون فروش", "age")
    _variant(db_session, dead_product, "8", stock=2, barcode="AN-DEAD")
    dead = get_dead_stock(db_session, start, end)
    assert dead["variants"][0]["size"] == "قد 128 تا 134"

    _sell(db_session, now, (age, v_age), refunded=True)
    returns = get_returns(db_session, start, end)
    assert returns["variants"][0]["size"] == "قد 116 تا 122"


def test_adult_and_kids_read_their_own_units(client, db_session, authed):
    now, start, end = _window()
    kid = _product(db_session, "پیراهن بچگانه", "age")
    adult = _product(db_session, "پیراهن بزرگسال", "adult", gender="woman")
    v_kid = _variant(db_session, kid, "6-7", barcode="AN-KID")
    v_adult = _variant(db_session, adult, "38", barcode="AN-AD")
    _sell(db_session, now, (kid, v_kid), (adult, v_adult))

    _use_standard(db_session, "age")
    _use_adult_standard(db_session, "numeric")
    labels = {row["label"] for row in get_variant_stats(db_session, start, end, "size")}
    assert labels == {"6-7 سال", "38 زنانه"}
    assert {row["size"] for row in get_stock_by_band(db_session)} == {"6-7 سال", "38 زنانه"}

    _use_adult_standard(db_session, "letters")
    labels = {row["label"] for row in get_variant_stats(db_session, start, end, "size")}
    assert labels == {"6-7 سال", "M"}


def test_the_same_adult_number_on_two_genders_stays_two_rows(client, db_session, authed):
    now, start, end = _window()
    women = _product(db_session, "پیراهن زنانه", "adult", gender="woman")
    men = _product(db_session, "پیراهن مردانه", "adult", gender="man")
    v_women = _variant(db_session, women, "46", barcode="AN-W46")
    v_men = _variant(db_session, men, "46", barcode="AN-M46")
    _sell(db_session, now, (women, v_women), (men, v_men))
    _use_adult_standard(db_session, "numeric")

    rows = {row["label"]: row["quantity"]
            for row in get_variant_stats(db_session, start, end, "size")}
    assert rows == {"46 زنانه": 1, "46 مردانه": 1}


def test_the_product_tab_shows_stock_by_band(client, db_session, authed):
    age = _product(db_session, "پیراهن موجودی", "age")
    _variant(db_session, age, "6-7", stock=4, barcode="AN-STOCK")
    db_session.commit()

    page = client.get("/admin/analytics?tab=product&period=all").text
    assert "موجودی به تفکیک سایز" in page
    assert "6-7 سال" in page
