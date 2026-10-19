"""Sizing engine contracts — the two-hop conversion (label -> band -> label):
body-based systems share the core height/age table; garment-based systems
(Iranian length, letters) carry per-size-type ladders. Display speaks Latin
digits, months only below 24m, adults as «بزرگسال»."""
import pytest

from services.sizing import (
    SIZE_TYPES, SYSTEMS, DISPLAY_STANDARDS,
    Band, resolve_band, band_display, band_summary, size_options,
)


def test_taxonomy_is_the_four_garment_types():
    assert SIZE_TYPES["upper"] == "بالاتنه"
    assert SIZE_TYPES["lower"] == "پایین‌تنه"
    assert SIZE_TYPES["set"] == "سرهمی/ست"
    assert SIZE_TYPES["shoes"] == "کفش"
    # Four systems, no country names, no two names for one thing.
    assert list(SYSTEMS) == ["age", "height", "iran", "letters"]
    assert all(spec["label"] for spec in SYSTEMS.values())
    assert not any(word in spec["label"] for spec in SYSTEMS.values()
                   for word in ("چین", "EU", "US", "ایران", "عدد"))
    # Only a body can be a display unit.
    assert set(DISPLAY_STANDARDS) == {"age", "height"}


def test_age_band_resolves_from_the_core_table():
    band = resolve_band("age", "6-7")
    assert band == Band(116, 122, 72, 84)


def test_persian_digits_normalize_before_resolving():
    assert resolve_band("age", "۶-۷") == resolve_band("age", "6-7")


def test_height_codes_resolve_with_tolerance_and_younger_row_on_ties():
    # EU 122: the tie between rows 6-7 and 7-8 resolves to the younger row,
    # matching the charts (122 = 6-7 years).
    assert resolve_band("height", "122") == Band(119, 125, 72, 84)
    # EU 92 sits on the 18-24m / 2-3y seam — younger side again.
    assert resolve_band("height", "92") == Band(89, 95, 18, 24)


def test_the_coarse_height_codes_live_in_the_same_height_system():
    # Tags print 59/100/120 as well as 98/122 — one axis, one picker.
    assert resolve_band("height", "120") == Band(117, 123, 72, 84)
    assert resolve_band("height", "100") == Band(97, 103, 36, 48)
    assert {option["value"] for option in size_options("height")} >= {"120", "100", "122"}


def test_bare_year_numerals_read_as_age_bands():
    # A tag that says «8» and one that says «6-7 سال» are one system.
    assert resolve_band("age", "8") == Band(128, 134, 96, 108)
    assert resolve_band("age", "2") == Band(92, 98, 24, 36)


def test_iranian_length_needs_the_type_and_seeds_are_provisional():
    # Provisional seeds until the shop calibrates with real stock:
    # upper center = 92 + 2.8*(N-40), lower center = 92 + 2.15*(N-40).
    assert resolve_band("iran", "50", "upper") == Band(113, 127, 72, 84)
    assert resolve_band("iran", "50", "lower") == Band(108, 120, 60, 72)
    # Without a type the garment ladders cannot answer.
    assert resolve_band("iran", "50") is None
    assert resolve_band("letters", "M") is None


def test_letters_are_adult_wide_bands_with_no_age():
    band = resolve_band("letters", "M", "upper")
    assert band == Band(158, 175, None, None)
    assert band.months_min is None and band.months_max is None


def test_unparseable_or_unknown_never_raises():
    assert resolve_band("height", "abc") is None
    assert resolve_band("age", "999") is None
    assert resolve_band("age", "25") is None
    assert resolve_band("height", "") is None
    assert resolve_band("nope", "8") is None


def test_display_months_only_in_the_baby_zone_latin_digits():
    assert band_display(resolve_band("age", "18-24m"), "age") == "18-24 ماه"
    assert band_display(resolve_band("age", "2-3"), "age") == "2-3 سال"
    assert band_display(resolve_band("age", "8"), "age") == "8-9 سال"
    assert band_display(resolve_band("age", "6-7"), "age") == "6-7 سال"
    assert band_display(resolve_band("letters", "M", "upper"), "age") == "بزرگسال"


def test_height_display_and_summary_put_the_standard_first():
    band = resolve_band("age", "6-7")
    assert band_display(band, "height") == "قد 116 تا 122"
    assert band_summary(band, "age") == "6-7 سال · قد 116 تا 122"
    assert band_summary(band, "height") == "قد 116 تا 122 · 6-7 سال"
    assert band_summary(resolve_band("letters", "L", "upper"), "age") == "بزرگسال · قد 165 تا 182"


def test_adult_letter_spans_do_not_fake_precision():
    # Wide, overlapping, honest: consecutive adult letters share ground.
    s = resolve_band("letters", "S", "upper")
    m = resolve_band("letters", "M", "upper")
    assert s.height_max > m.height_min


def test_ladders_are_seamless_and_render_options():
    for system in ("age", "height"):
        options = size_options(system)
        assert options, system
        bands = [resolve_band(system, option["value"]) for option in options]
        assert all(b is not None for b in bands), system
        for left, right in zip(bands, bands[1:]):
            assert left.height_max >= right.height_min, f"{system}: gap before {right}"
    for system in ("age", "height"):
        bands = [resolve_band(system, option["value"]) for option in size_options(system)]
        for left, right in zip(bands, bands[1:]):
            assert left.months_max >= right.months_min, f"{system}: month gap"
    for size_type in ("upper", "lower", "set"):
        options = size_options("iran", size_type)
        bands = [resolve_band("iran", option["value"], size_type) for option in options]
        assert all(b is not None for b in bands)
        for left, right in zip(bands, bands[1:]):
            assert left.height_max >= right.height_min
    letters = size_options("letters", "upper")
    assert [o["value"] for o in letters] == ["S", "M", "L", "XL", "2XL", "3XL"]
    assert size_options("nope") is None
    assert size_options("cn") is None          # merged into height
    assert size_options("us") is None          # merged into age


def test_baby_seam_between_months_and_first_year_band():
    baby = resolve_band("age", "18-24m")
    toddler = resolve_band("age", "2-3")
    assert baby.months_max == toddler.months_min == 24
    assert toddler.height_min == 92


def test_every_year_band_is_twelve_months():
    for option in size_options("age"):
        value = option["value"]
        if not value.endswith("m"):
            band = resolve_band("age", value)
            assert band.months_max - band.months_min == 12, value


def test_size_type_choices_carry_labels_for_the_form():
    assert SIZE_TYPES["upper"] == "بالاتنه"
    options = size_options("iran", "lower")
    assert options[0]["value"] == "35"
    assert options[-1]["value"] == "80"
    assert all(o["label"] for o in options)
