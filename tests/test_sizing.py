"""Sizing engine contracts — every tag language resolves to one band, and the
shop's chosen display unit is the only thing printed. Entry is one number or
letter; doubles are entered by their lower number and keep their pair."""
from services.sizing import (
    SIZE_TYPES, SYSTEMS, DISPLAY_STANDARDS,
    Band, resolve_band, band_display, size_options,
)


def test_taxonomy_is_ten_tag_languages_and_four_units():
    assert SIZE_TYPES["upper"] == "بالاتنه"
    assert list(SYSTEMS) == [
        "age", "height", "kids_letters", "adult", "iran", "sock",
        "shoe_eu", "shoe_uk", "shoe_us", "shoe_cm",
    ]
    assert all(spec["label"] for spec in SYSTEMS.values())
    assert not any(word in spec["label"] for spec in SYSTEMS.values()
                   for word in ("چین", "EU", "US", "ایران"))
    assert list(DISPLAY_STANDARDS) == ["age", "letters", "height", "garment"]


def test_age_tags_read_as_single_months_years_and_toddler_sizes():
    assert resolve_band("age", "6-7") == Band(116, 122, 72, 84)
    assert resolve_band("age", "8") == Band(128, 134, 96, 108)
    assert resolve_band("age", "2T") == Band(92, 98, 24, 36)
    assert resolve_band("age", "5T") == Band(110, 116, 60, 72)
    assert resolve_band("age", "18m") == Band(86, 92, 18, 24)
    assert resolve_band("age", "12-24 ماه") == Band(80, 92, 12, 24)
    assert resolve_band("age", "5-8 anos") == Band(110, 128, 60, 96)
    assert resolve_band("age", "۲-۴") == Band(92, 104, 24, 48)


def test_persian_digits_normalize_before_resolving():
    assert resolve_band("age", "۶-۷") == resolve_band("age", "6-7")


def test_height_doubles_keep_the_pair_behind_the_lower_number():
    assert resolve_band("height", "122") == Band(116, 128, 72, 96)
    assert resolve_band("height", "122/128") == Band(116, 128, 72, 96)
    assert resolve_band("height", "86") == Band(80, 92, 12, 24)
    assert resolve_band("height", "110") == Band(104, 116, 48, 72)
    assert resolve_band("height", "112") == Band(112, 118, 60, 72)
    assert resolve_band("height", "112/118") == Band(112, 118, 60, 72)


def test_height_singles_use_the_coarse_chinese_tolerance():
    assert resolve_band("height", "120") == Band(115, 125, 72, 84)
    assert resolve_band("height", "90") == Band(85, 95, 18, 24)
    assert resolve_band("height", "92") == Band(89, 95, 18, 24)
    assert {option["value"] for option in size_options("height")} >= {"90", "112", "120", "122"}


def test_kids_letters_accept_the_printed_parenthetical():
    assert resolve_band("kids_letters", "M") == Band(128, 140, 96, 120)
    assert resolve_band("kids_letters", "M(8/10)") == Band(128, 140, 96, 120)
    assert resolve_band("kids_letters", "S(6/6x)") == Band(116, 122, 72, 84)
    assert resolve_band("kids_letters", "L") == Band(140, 152, 120, 144)


def test_adult_numeric_and_letters_are_separate_from_kids_letters():
    assert resolve_band("adult", "38", gender="woman") == Band(None, None, None, None, 38, 38)
    assert resolve_band("adult", "S", gender="woman") == Band(None, None, None, None, 36, 38)
    assert resolve_band("adult", "S", gender="man") == Band(None, None, None, None, 46, 48)
    assert resolve_band("adult", "50", gender="man") == Band(None, None, None, None, 50, 50)
    assert resolve_band("adult", "36/38", gender="woman") == Band(None, None, None, None, 36, 38)
    assert resolve_band("adult", "38", gender="man") is None
    assert resolve_band("kids_letters", "L") != resolve_band("adult", "L", gender="man")


def test_iranian_table_is_the_published_one_and_needs_no_garment_type():
    assert resolve_band("iran", "25") == Band(44, 68, 0, 6)
    assert resolve_band("iran", "35") == Band(80, 92, 12, 24)
    assert resolve_band("iran", "45") == Band(104, 110, 48, 60)
    assert resolve_band("iran", "50") == Band(116, 122, 72, 84)
    assert resolve_band("iran", "65") == Band(152, 158, 144, 156)
    assert resolve_band("iran", "70") is None


def test_sock_ranges_resolve_to_child_bands():
    assert resolve_band("sock", "23-26") == Band(92, 104, 24, 48)
    assert resolve_band("sock", "23/26") == Band(92, 104, 24, 48)
    assert resolve_band("sock", "15-18") == Band(44, 80, 0, 12)


def test_four_shoe_tag_languages_land_on_one_band():
    expected = Band(92, 104, 24, 48)
    assert resolve_band("shoe_eu", "24") == expected
    assert resolve_band("shoe_uk", "7") == expected
    assert resolve_band("shoe_uk", "7k") == expected
    assert resolve_band("shoe_us", "8") == expected
    assert resolve_band("shoe_us", "8C") == expected
    assert resolve_band("shoe_cm", "14") == expected
    assert resolve_band("shoe_cm", "14cm") == expected
    assert resolve_band("shoe_cm", "14.5") == expected
    assert resolve_band("shoe_eu", "۳۰") == Band(104, 116, 48, 72)
    assert resolve_band("shoe_eu", "39") is None          # adult: raw tag, no band
    assert resolve_band("shoe_cm", "24.5") is None


def test_ambiguous_uk_us_scales_need_their_marker():
    assert resolve_band("shoe_uk", "1") is None
    assert resolve_band("shoe_us", "1") is None
    assert resolve_band("shoe_uk", "1k") == Band(44, 80, 0, 12)
    assert resolve_band("shoe_uk", "1y") == Band(116, 128, 72, 96)
    assert resolve_band("shoe_us", "1C") == Band(44, 80, 0, 12)
    assert resolve_band("shoe_us", "1Y") == Band(116, 128, 72, 96)
    assert resolve_band("shoe_us", "12") == Band(104, 116, 48, 72)


def test_display_prints_exactly_one_unit():
    band = resolve_band("height", "122")
    assert band_display(band, "age") == "6-8 سال"
    assert band_display(band, "height") == "قد 116 تا 128"
    assert band_display(band, "letters") == "S"
    assert band_display(band, "garment") == "50"
    assert "·" not in band_display(band, "age")
    assert "قد" not in band_display(band, "age")


def test_adult_numeric_prints_adult_in_age_and_dash_in_body_units():
    band = resolve_band("adult", "38", gender="woman")
    assert band_display(band, "age") == "بزرگسال"
    assert band_display(band, "letters", "woman") == "S"
    assert band_display(band, "height") == "—"
    assert band_display(band, "garment") == "—"


def test_letters_display_derives_from_the_kids_band():
    assert band_display(resolve_band("age", "4-5"), "letters") == "XS"
    assert band_display(resolve_band("age", "6-7"), "letters") == "S"
    assert band_display(resolve_band("kids_letters", "M"), "letters") == "M"
    assert band_display(resolve_band("age", "8-10"), "letters") == "M"
    assert band_display(resolve_band("age", "12-14"), "letters") == "XL"
    assert band_display(resolve_band("age", "18m"), "letters") == "—"


def test_garment_display_is_the_tag_number_or_one_equivalent():
    assert band_display(resolve_band("iran", "45"), "garment") == "45"
    assert band_display(resolve_band("height", "122"), "garment") == "50"
    assert band_display(resolve_band("kids_letters", "M"), "garment") == "55"
    assert band_display(resolve_band("height", "170"), "garment") == "—"


def test_unparseable_or_unknown_never_raises():
    assert resolve_band("height", "abc") is None
    assert resolve_band("age", "999") is None
    assert resolve_band("age", "25") is None
    assert resolve_band("height", "") is None
    assert resolve_band("nope", "8") is None
    assert band_display(None, "age") == "—"


def test_every_ladder_option_resolves_and_height_uses_single_numbers():
    for system in ("age", "height", "kids_letters", "iran", "sock",
                   "shoe_eu", "shoe_uk", "shoe_us", "shoe_cm"):
        options = size_options(system)
        assert options, system
        assert all(resolve_band(system, option["value"]) is not None for option in options), system

    height_values = [option["value"] for option in size_options("height")]
    assert all("/" not in value for value in height_values)
    assert "112" in height_values and "122" in height_values

    for option in size_options("adult", "woman"):
        assert resolve_band("adult", option["value"], gender="woman") is not None
    for option in size_options("adult", "man"):
        assert resolve_band("adult", option["value"], gender="man") is not None


def test_age_picker_has_single_months_years_and_toddler_sizes():
    values = [option["value"] for option in size_options("age")]
    assert {"0m", "3m", "12m", "18m", "24m", "2", "8", "16", "2T", "3T", "5T"} <= set(values)
    assert all("-" not in value for value in values)
    for value in values:
        if not value.endswith("m"):
            band = resolve_band("age", value)
            assert band.months_max - band.months_min == 12, value


def test_iranian_and_sock_pickers_carry_age_labels_and_single_values():
    iran = size_options("iran")
    assert [option["value"] for option in iran] == ["25", "30", "35", "40", "45", "50", "55", "60", "65"]
    assert all("(" in option["label"] for option in iran)
    sock = size_options("sock")
    assert [option["value"] for option in sock] == ["15-18", "19-22", "23-26", "27-30", "31-34", "35-38"]
    assert [option["value"] for option in size_options("shoe_eu")][:2] == ["15", "16"]
    assert {"1k", "7k", "1y"} <= {option["value"] for option in size_options("shoe_uk")}
    assert {"1C", "8C", "1Y"} <= {option["value"] for option in size_options("shoe_us")}
    assert {"8", "14", "23"} <= {option["value"] for option in size_options("shoe_cm")}
    assert size_options("nope") is None
    assert size_options("letters") is None
