from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_base_template_loads_local_application_assets():
    text = (ROOT / "templates/base.html").read_text()
    assert "/static/js/app.js" in text
    assert "/static/js/charts.js" in text
    assert 'lang="fa" dir="rtl"' in text


def test_checkout_has_keyboard_payment_and_cash_feedback_hooks():
    text = (ROOT / "templates/sales/checkout.html").read_text()
    assert 'id="barcode-input"' in text
    assert 'id="terminal-status"' in text
    assert 'payment-timeline' in text
    assert 'cash-calculator' in text
    assert 'aria-live="polite"' in text


def test_analytics_has_no_external_chart_dependency():
    text = (ROOT / "templates/admin/analytics.html").read_text()
    assert "cdn.jsdelivr.net/npm/chart.js" not in text
    assert "/static/js/charts.js" not in text


def test_print_layout_is_light_and_readable_for_all_themes():
    css = (ROOT / "static/css/style.css").read_text()
    assert "html, body { background: #fff !important; color: #000 !important; }" in css
    assert ".invoice, .invoice .invoice-header" in css


def test_frontend_assets_have_accessibility_and_loading_support():
    css = (ROOT / "static/css/style.css").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert ":focus-visible" in css
    assert ".is-loading" in css
    assert "requestSubmit" in js
