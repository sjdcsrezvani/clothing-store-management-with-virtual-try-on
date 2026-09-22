"""Field/button/copy system (Phase 1): one spec everywhere.

Placeholders speak مثال:, money takes Persian digits, spinners are gone,
buttons wear one variant, and every icon metaphor is unique.
"""
import re
from pathlib import Path

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


def test_placeholder_voice_has_one_rule_and_no_emoji():
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert "::placeholder" in style
    assert "var(--ink-soft)" in style
    for source in sorted((ROOT / "templates").rglob("*.html")):
        text = source.read_text()
        for match in re.findall(r'placeholder="([^"]*)"', text):
            assert not match.startswith(("🔍", "🔲", "📝", "📞")), f"{source.name}: {match}"
            assert match != "اختیاری", f"{source.name}: lazy placeholder"
            assert not match.startswith(("از:", "تا:", "از تاریخ", "تا تاریخ")), f"{source.name}: {match}"


def test_no_native_number_inputs_remain():
    offenders = []
    for source in sorted((ROOT / "templates").rglob("*.html")):
        if 'type="number"' in source.read_text():
            offenders.append(source.name)
    assert not offenders, f"type=number survivors: {offenders}"


def test_numeric_macro_is_text_with_numeric_keypad():
    macro = (ROOT / "templates" / "partials" / "numeric_input.html").read_text()
    assert 'type="text"' in macro
    assert 'inputmode="numeric"' in macro
    assert 'type="number"' not in macro


def test_spinners_are_gone_everywhere():
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert "::-webkit-inner-spin-button" in style
    assert "appearance: textfield" in style


def test_buttons_wear_one_variant():
    sms = (ROOT / "templates" / "admin" / "sms.html").read_text()
    assert "btn-ghost btn-danger" not in sms
    saved = (ROOT / "templates" / "admin" / "tryon_saved.html").read_text()
    assert "font-size:0.7rem; padding:0.3rem" not in saved
    assert "btn-sm btn-success" in saved
    tryon = (ROOT / "templates" / "admin" / "tryon.html").read_text()
    assert 'class="btn">💾' not in tryon


def test_icon_metaphors_are_unique():
    base = (ROOT / "templates" / "base.html").read_text()
    assert 'id="icon-calendar"' in base
    assert 'id="icon-crown"' in base
    nav = (ROOT / "services" / "navigation.py").read_text()
    assert '"calendar"' in nav and '"crown"' in nav
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".icon-calendar::before" in style and ".icon-crown::before" in style
    assert 'content: "📅"' in style and 'content: "👑"' in style


def test_discount_mirrors_submit_without_js():
    """The mirrors carry names bound to the apply form: no JS, no lost discount."""
    source = (ROOT / "templates" / "sales" / "checkout.html").read_text()
    assert 'name="custom_discount_amount" form="apply-discount-form"' in source
    assert 'name="custom_discount_percent" form="apply-discount-form"' in source
    assert 'for="custom-amount-input"' in source
    assert 'for="custom-percent-input"' in source


def test_discount_parse_reads_persian_digits():
    from routers.sales import _discount_int
    assert _discount_int("۱۲٬۵۰۰") == 12500
    assert _discount_int("10") == 10
    assert _discount_int("") == 0
    assert _discount_int("abc") == 0


def test_credit_limit_reads_persian_digits(client, db_session):
    from models import Customer
    customer = Customer(phone="09120009999", first_name="تست", last_name="میدان",
                        referral_code="T99999", tier="silver")
    db_session.add(customer)
    db_session.commit()
    _login(client)
    token = csrf_token(client, f"/admin/credit/{customer.id}")
    resp = client.post(f"/admin/credit/{customer.id}/limit", data={
        "credit_limit": "۱٬۰۰۰٬۰۰۰", "csrf_token": token,
    }, follow_redirects=False)
    assert resp.status_code == 303
    db_session.refresh(customer)
    assert customer.credit_limit == 1000000


def test_digit_entry_is_unified_to_english_live():
    """Typing Persian digits converts in the field: the server only ever
    receives one script. Date-picker fields are excluded by design."""
    app = (ROOT / "static" / "js" / "app.js").read_text()
    assert "function toEnglishDigits" in app
    assert 'input[inputmode="numeric"]' in app
    assert "unifyFieldDigits" in app
    # The old tel handler stripped Persian digits outright; now it converts first.
    assert "unifyFieldDigits(input)" in app


def test_digit_examples_are_latin_outside_the_date_picker():
    """Example placeholders teach the script the field keeps: Latin for
    money/counts, Persian only where the Jalali picker lives."""
    import re
    offenders = []
    for source in sorted((ROOT / "templates").rglob("*.html")):
        text = source.read_text()
        for match in re.finditer(r"<input\b[^>]*placeholder=\"([^\"]*[۰-۹][^\"]*)\"", text):
            tag = match.group(0)
            if "persian-date-input" in tag:
                continue  # Jalali dates live in Persian digits by design
            if 'inputmode="numeric"' in tag or 'type="tel"' in tag or 'type="number"' in tag:
                offenders.append((source.name, match.group(1)))
    assert not offenders, offenders
