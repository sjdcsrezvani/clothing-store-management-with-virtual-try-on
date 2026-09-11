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


def test_theme_shell_has_distinct_tokens_and_unclipped_topbar_actions():
    base = (ROOT / "templates/base.html").read_text()
    css = (ROOT / "static/css/style.css").read_text()
    assert 'class="topbar-actions"' in base
    assert 'class="quick-sale"' in base
    assert 'class="topbar-admin"' in base
    assert 'id="icon-cart"' in base
    assert 'id="icon-settings"' in base
    assert 'class="sidebar-brand-icon"' not in base
    assert '.sidebar-brand-icon' not in css
    assert '.sidebar-brand::before' not in css
    assert 'position: absolute; left: 1.25rem' in css
    assert '.topbar-admin { width: 42px' not in css
    assert '--card-accent' in css
    assert '--table-header' in css
    assert 'html[data-theme="kids-boutique"] .card { border: none; }' in css


def test_frontend_assets_have_accessibility_and_loading_support():
    css = (ROOT / "static/css/style.css").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert ":focus-visible" in css
    assert ".is-loading" in css
    assert "requestSubmit" in js


def test_color_code_fields_open_a_native_colour_palette():
    variant_form = (ROOT / "templates/admin/variant_form.html").read_text()
    product_form = (ROOT / "templates/admin/product_form.html").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert 'data-color-picker' in variant_form
    assert 'data-color-picker' in product_form
    # Variant blocks are added by script, so the hook must re-run for them.
    assert "initColorPickers(container)" in product_form
    assert "function initColorPickers" in js
    assert "picker.type = 'color'" in js
    assert "field.value = picker.value.toUpperCase()" in js
