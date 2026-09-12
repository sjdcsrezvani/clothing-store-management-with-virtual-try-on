"""Persian date picker: theme contract, keyboard/a11y surface, and calendar maths.

The picker is plain JavaScript, so these tests pin the parts that Python can
see — the markup/CSS contract, the absence of the old hardcoded palette, and the
Jalali calendar rules checked against `jdatetime` — plus a live route test for
the form date format.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import jdatetime
import pytest

from models import Campaign
from services._common import parse_form_date, parse_form_date_end, parse_jalali_input
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]
PICKER_JS = (ROOT / "static" / "js" / "datepicker.js").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()
BASE_HTML = (ROOT / "templates" / "base.html").read_text()

TEMPLATES = ROOT / "templates"


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text()


# ── presentation contract ────────────────────────────────────────────────────

def test_picker_renders_no_inline_styles_and_no_hardcoded_palette():
    """The old picker styled itself purple inline; every colour must be a token."""
    assert 'style="' not in PICKER_JS, "popup markup must not carry inline styles"
    for declaration in ("border:", "background:", "box-shadow:", "font-family:"):
        assert declaration not in PICKER_JS.replace("+ ", ""), declaration
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", PICKER_JS), "no hardcoded colours in the picker"


def test_picker_markup_contract_is_present():
    for hook in (
        "pdp-popup", "pdp-head", "pdp-prev", "pdp-next", "pdp-month", "pdp-year",
        "pdp-weekdays", "pdp-grid", "pdp-day", "pdp-today", "pdp-clear", "pdp-status",
    ):
        assert hook in PICKER_JS, hook


def test_picker_styles_come_from_theme_tokens():
    assert ".pdp-popup {" in STYLE_CSS
    section = STYLE_CSS[STYLE_CSS.index("/* ===================== Persian date picker"):]
    for token in ("var(--card)", "var(--rule)", "var(--candy)", "var(--ink)",
                  "var(--field-bg)", "var(--focus-ring)", "var(--button-text)",
                  "var(--surface-soft)", "var(--radius)", "var(--shadow-hover)"):
        assert token in section, token
    # States must not rely on colour alone for high-contrast themes.
    assert "html[data-theme-mode=\"high-contrast\"] .pdp-day.is-selected" in STYLE_CSS
    assert ".pdp-day:disabled" in STYLE_CSS and "line-through" in STYLE_CSS
    # The old palette is gone repo-wide.
    for dead_colour in ("#c44dff", "#f0e6ff", "#ff6b6b"):
        assert dead_colour not in STYLE_CSS
        assert dead_colour not in PICKER_JS


def test_picker_jumps_straight_to_a_typed_year():
    """Scrolling a century of year options was the only way to reach 1380."""
    for hook in ("yearBuffer", "pushYearDigit", "popYearDigit", "jumpToYear",
                 "clampYear", "isDigitKey", "pdp-hint", "YEAR_HINT"):
        assert hook in PICKER_JS, hook
    assert re.search(r"YEAR_DIGITS = 4", PICKER_JS), "a Jalali year is four digits"
    # Both scripts type the same year ("۱۳۸۰" arrives as a single key).
    assert "toLatinDigits(event.key)" in PICKER_JS
    # The fourth digit commits; earlier digits are shown, not acted on.
    assert "yearBuffer.length < YEAR_DIGITS" in PICKER_JS
    assert "if (yearBuffer.length >= YEAR_DIGITS) yearBuffer = '';" in PICKER_JS
    # A typed year outside the window lands on the nearest year we can show.
    assert "year < window_.startYear" in PICKER_JS and "year > window_.endYear" in PICKER_JS
    # Escape discards a half-typed year, Backspace takes one digit back, and the
    # buffer cannot outlive the popup.
    assert "if (yearBuffer) {" in PICKER_JS
    assert "popYearDigit()" in PICKER_JS
    assert "flushYearBuffer();" in PICKER_JS
    assert "expireYearBuffer" in PICKER_JS
    # Screen readers hear the digits through the live region already in the popup.
    assert "announce('سال '" in PICKER_JS


def test_type_ahead_hint_is_themed_and_present_in_the_markup():
    assert "pdp-hint" in PICKER_JS and ".pdp-hint {" in STYLE_CSS
    section = STYLE_CSS[STYLE_CSS.index("/* ===================== Persian date picker"):]
    hint = section[section.index(".pdp-hint {"):]
    hint = hint[:hint.index("}")]
    assert "var(--ink-soft)" in hint, "the hint must follow the active theme"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", hint)


def test_picker_has_keyboard_navigation_and_dialog_semantics():
    for hook in (
        "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown",
        "Home", "End", "Escape", "Tab", "'Enter'",
        "role', 'dialog'", "aria-modal", "aria-haspopup", "aria-expanded",
        "aria-current", "aria-selected", "aria-label", 'role="status"',
    ):
        assert hook in PICKER_JS, hook
    assert ".pdp-day:focus-visible" in STYLE_CSS
    assert "pdp-popup.is-sheet" in STYLE_CSS, "narrow screens need the bottom sheet"


def test_picker_opens_on_the_fields_own_value_instead_of_a_fixed_month():
    """The old picker always opened on 1403/1 and mis-read Persian digits."""
    assert "1403" not in PICKER_JS
    assert "parseDate(input.value)" in PICKER_JS
    assert "toLatinDigits" in PICKER_JS and "toPersianDigits" in PICKER_JS
    # Selecting must look like a real edit to the rest of the page.
    assert "new Event('input'" in PICKER_JS
    assert "new Event('change'" in PICKER_JS


def test_picker_repositions_with_fixed_placement():
    assert "position: fixed" in STYLE_CSS
    assert "getBoundingClientRect" in PICKER_JS
    assert "addEventListener('scroll'" in PICKER_JS
    assert "addEventListener('resize'" in PICKER_JS
    assert "requestAnimationFrame" in PICKER_JS


def test_transient_picker_is_appended_to_body_and_closes_without_leaking():
    assert "document.body.appendChild" in PICKER_JS
    assert "active.popup.remove()" in PICKER_JS
    # A single shared outside-click listener, not one per field.
    assert PICKER_JS.count("document.addEventListener('pointerdown'") == 1


# ── asset wiring ─────────────────────────────────────────────────────────────

def test_base_template_loads_the_picker_without_jquery():
    assert "/static/js/datepicker.js" in BASE_HTML
    assert "jquery" not in BASE_HTML.lower()
    assert "persian-datepicker.min.js" not in BASE_HTML
    assert "persianDatepicker(" not in BASE_HTML


def test_dead_picker_assets_are_gone_and_unreferenced():
    for name in (
        "static/js/jquery.min.js",
        "static/js/persian-datepicker.min.js",
        "static/js/persian-date.min.js",
        "static/css/persian-datepicker.min.css",
    ):
        assert not (ROOT / name).exists(), f"{name} should be deleted"
        for path in list(TEMPLATES.rglob("*.html")) + list((ROOT / "static").rglob("*.js")):
            if path.name == "datepicker.js":
                continue
            assert name.split("/")[-1] not in path.read_text(), f"{name} still referenced by {path}"


# ── calendar maths ───────────────────────────────────────────────────────────

def test_jalali_calendar_rules_match_jdatetime():
    """The leap rule and month lengths the picker ships must match jdatetime."""
    match = re.search(r"return m === ([\d |m=]+);", PICKER_JS)
    assert match, "the leap-year rule must stay recognisable"
    leap_mods = {int(n) for n in re.findall(r"\d+", match.group(1))}
    assert leap_mods == {1, 5, 9, 13, 17, 22, 26, 30}

    def is_leap(jy: int) -> bool:
        return (((jy % 33) + 33) % 33) in leap_mods

    def days_in_month(jy: int, jm: int) -> int:
        if jm <= 6:
            return 31
        if jm <= 11:
            return 30
        return 30 if is_leap(jy) else 29

    for jy in range(1390, 1445):
        for jm in range(1, 13):
            current = jdatetime.date(jy, jm, 1).togregorian()
            following = (jdatetime.date(jy + 1, 1, 1) if jm == 12
                         else jdatetime.date(jy, jm + 1, 1)).togregorian()
            assert days_in_month(jy, jm) == (following - current).days, (jy, jm)


def test_picker_day_numbers_stay_linear_against_jdatetime():
    """dayNumber/fromDayNumber back the keyboard maths, so pin the epoch."""
    epoch_match = re.search(r"EPOCH_YEAR = (\d+)", PICKER_JS)
    assert epoch_match and int(epoch_match.group(1)) == 1350, "1350/01/01 was a Sunday"
    assert "EPOCH_WEEKDAY_COLUMN = 1" in PICKER_JS
    assert jdatetime.date(1350, 1, 1).togregorian().isoformat() == "1971-03-21"
    # The epoch was a Sunday, and jdatetime counts Saturday as 0 — which is why
    # the picker's Saturday-first grid needs EPOCH_WEEKDAY_COLUMN = 1.
    assert jdatetime.date(1350, 1, 1).weekday() == 1
    assert jdatetime.date(1405, 6, 21).weekday() == 0  # 1405/06/21 was a Saturday
    assert jdatetime.date(1405, 6, 21).togregorian().isoformat() == "2026-09-12"


# ── server-side date contract ────────────────────────────────────────────────

def test_form_date_parser_reads_jalali_and_iso_without_confusing_them():
    """parse_jalali_input alone turned an ISO 2026 into the Jalali year 2647."""
    assert parse_jalali_input("2026-09-12").year == 2647  # the trap this guards

    for value in ("1405/06/21", "۱۴۰۵/۰۶/۲۱", "1405-6-1", "2026-09-12", "2026/09/12", "2026.09.12"):
        parsed = parse_form_date(value)
        assert parsed is not None, value
        assert parsed.tzinfo is timezone.utc
        assert parsed.year < 2100, f"{value} must not be read as a Jalali year"

    assert parse_form_date("1405/06/21").date() == jdatetime.date(1405, 6, 21).togregorian()
    assert parse_form_date("2026-09-12").date().isoformat() == "2026-09-12"
    assert parse_form_date_end("2026-09-12").hour == 23
    assert parse_form_date_end("1405/06/21").hour == 23
    assert parse_form_date("") is None and parse_form_date_end("   ") is None


def test_campaign_form_uses_the_shared_jalali_picker():
    form = _template("admin/campaign_form.html")
    listing = _template("admin/campaigns.html")
    assert 'type="date"' not in form
    assert form.count('class="persian-date-input"') == 2
    assert 'data-pdp-pair="#end_date"' in form
    assert "jalali_str(campaign.start_date" in form
    # The list shows Persian dates like the rest of the app, not Gregorian.
    assert "strftime('%Y/%m/%d')" not in listing
    assert "jalali_str(campaign.start_date" in listing


def test_campaign_route_stores_a_jalali_date_as_the_same_gregorian_day(client, db_session, authed):
    response = client.post("/admin/campaigns/add", data={
        "name": "کمپین تاریخ",
        "code": "DATE-TEST",
        "discount_percent": "10",
        "min_purchase": "0",
        "start_date": "۱۴۰۵/۰۶/۲۱",
        "end_date": "۱۴۰۵/۰۷/۰۱",
        "csrf_token": csrf_token(client, "/admin/campaigns"),
    }, follow_redirects=False)

    assert response.status_code == 303
    campaign = db_session.query(Campaign).filter(Campaign.code == "DATE-TEST").one()
    assert campaign.start_date.date() == jdatetime.date(1405, 6, 21).togregorian()
    assert campaign.end_date.year == campaign.start_date.year
    assert campaign.end_date.hour == 23

    # An ISO payload from an older client still lands on the right day.
    client.post("/admin/campaigns/add", data={
        "name": "کمپین ISO",
        "code": "ISO-TEST",
        "discount_percent": "5",
        "min_purchase": "0",
        "start_date": "2026-09-12",
        "end_date": "2026-09-20",
        "csrf_token": csrf_token(client, "/admin/campaigns"),
    }, follow_redirects=False)
    iso_campaign = db_session.query(Campaign).filter(Campaign.code == "ISO-TEST").one()
    assert iso_campaign.start_date.year == 2026
    assert parse_form_date("2026-09-12").year != 2647


# ── field-level constraints ──────────────────────────────────────────────────

@pytest.mark.parametrize("template,fragment", [
    ("admin/accounting.html", 'data-pdp-pair="#accounting-end"'),
    ("admin/analytics.html", 'data-pdp-pair="#analytics-end"'),
    ("admin/purchases.html", 'data-pdp-pair="#filter-end"'),
    ("admin/inventory_movements.html", 'data-pdp-pair="#filter-end"'),
    ("admin/checks.html", 'data-pdp-pair="#due_date"'),
    ("admin/staff.html", 'data-pdp-pair="#contract_end_date"'),
    ("admin/campaign_form.html", 'data-pdp-pair="#end_date"'),
])
def test_range_pairs_are_declared_once(template, fragment):
    assert fragment in _template(template)


@pytest.mark.parametrize("template", ["index.html", "customer.html", "sales/checkout.html"])
def test_birthdays_cannot_be_in_the_future(template):
    text = _template(template)
    assert 'class="persian-date-input" data-pdp-max="today"' in text


def test_picker_reads_an_iso_field_value_as_a_gregorian_date():
    """Jalali years never reach 1900, so a bigger year must be converted, not read."""
    assert "y >= 1900" in PICKER_JS
    assert "gregorianToJalali(y, m, d)" in PICKER_JS
    # The year list must never be dragged to a Gregorian year by a stale value.
    assert "yearInList" not in PICKER_JS  # sanity: no stray debugging hooks
    assert "window.PersianDatePicker" in PICKER_JS
    for export in ("init:", "open:", "close:", "parse:", "format:"):
        assert export in PICKER_JS, export


def test_picker_understands_the_declared_attributes():
    for attribute in ("pdpMin", "pdpMax", "pdpPair", "pdpReady"):
        assert attribute in PICKER_JS, attribute
    assert "FLOOR_YEAR = 1300" in PICKER_JS, "typo'd years must be refused"


def test_year_list_reaches_back_past_a_lifetime_of_birthdays():
    """A 20-year reach put every birthday before ~1385 out of bounds."""
    match = re.search(r"YEARS_BACK = (\d+)", PICKER_JS)
    assert match, "the back-reach must stay recognisable"
    years_back = int(match.group(1))
    assert years_back >= 100, "a birth date from 100 years ago must be selectable"

    forward = re.search(r"YEARS_FORWARD = (\d+)", PICKER_JS)
    assert forward and 0 < int(forward.group(1)) <= 20

    # Guard against the reach being narrowed anywhere else: a birth year ~105
    # years back has to clear both the rolling window and the typo floor.
    today_year = jdatetime.date.today().year
    earliest = max(1300, today_year - years_back)
    assert earliest <= today_year - 105, earliest


def test_year_list_is_measured_from_today_so_it_keeps_up_with_the_years():
    """Nothing may pin the calendar to a fixed range — it has to re-align itself."""
    assert "todayYear - YEARS_BACK" in PICKER_JS
    assert "todayYear + YEARS_FORWARD" in PICKER_JS
    # Both ends come from today() on every render, so the window rolls forward.
    assert "var todayYear = today().y;" in PICKER_JS
    window = PICKER_JS[PICKER_JS.index("function yearWindow"):]
    window = window[:window.index("function yearOptions")]
    assert "today()" in window, "the window must never be a constant"

    # And the year list is built from that window rather than hardcoded bounds.
    options = PICKER_JS[PICKER_JS.index("function yearOptions"):]
    options = options[:options.index("// ── positioning")]
    assert "window_.startYear" in options and "window_.endYear" in options

    # The typo floor is an absolute year, and it is old enough to be harmless:
    # 1300/01/01 is 1921 in the Gregorian calendar.
    floor = int(re.search(r"FLOOR_YEAR = (\d+)", PICKER_JS).group(1))
    assert jdatetime.date(floor, 1, 1).togregorian().year == 1921
