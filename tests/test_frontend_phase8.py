import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_base_template_loads_local_application_assets():
    text = (ROOT / "templates/base.html").read_text()
    assert "/static/js/app.js" in text
    # The chart engine shipped offline like everything else. It loads at page
    # level now (a boosted arrival re-runs body scripts, not head ones), so
    # the page that draws charts carriers its own script — the offline
    # guarantee is the page's, not base's.
    assert "/static/js/charts.js" in (ROOT / "templates/admin/settings_appearance.html").read_text()
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
    # Paper is a token pair now (`--paper`/`--paper-ink`, derived in
    # services/themes.py): every palette owns its printer, and
    # test_keyboard_shell's no-frozen-colour guard keeps literals out of the
    # print rules entirely.
    assert "html, body { background: var(--paper) !important; color: var(--paper-ink) !important; }" in css
    assert ".invoice, .invoice .invoice-header" in css


def test_theme_shell_has_distinct_tokens_and_unclipped_topbar_actions(client, authed):
    base = (ROOT / "templates/base.html").read_text()
    css = (ROOT / "static/css/style.css").read_text()
    assert 'class="topbar-actions"' in base
    # The two global actions are drawn from the registry, so their classes are
    # asserted where they render, not in the template's source.
    page = client.get("/admin").text
    assert 'class="quick-sale"' in page
    assert 'class="topbar-admin"' in page
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


def test_purchase_row_amount_follows_the_selected_variant():
    page = (ROOT / "templates/admin/purchases.html").read_text()
    # Choosing a product mirrors its own stock and cost into the row: nothing
    # is typed, so no field may carry the amounts.
    assert "function paintMirror(row)" in page
    assert "function afterSelectionChange(row, variant)" in page
    assert 'input[name^="purchase_qty_"]' not in page
    assert 'input[name^="purchase_cost_"]' not in page
    # Regression: amounts were once typed per row and fell back to the
    # product only when blank — the mirror leaves no blank state.
    assert "purchase_cost_" not in page
    assert "purchase_qty_" not in page


def test_purchase_picker_is_a_scoped_search_not_a_giant_select():
    """Selecting a product is a search inside the chosen supplier's catalogue."""
    page = (ROOT / "templates/admin/purchases.html").read_text()
    css = (ROOT / "static/css/style.css").read_text()
    assert "input.setAttribute('role', 'combobox')" in page
    assert "function scopedVariants()" in page
    assert "function searchVariants(query)" in page
    assert "product.supplier_id" in page
    # Persian text is matched loosely: نیم‌فاصله، ي/ك عربی and Persian digits.
    assert "function fold(value)" in page
    assert "byBarcode[fold(variant.barcode)] = variant;" in page
    # No <select> of products is built any more.
    assert "function buildSelect" not in page
    assert "optgroup" not in page
    assert ".purchase-picker-list" in css
    assert ".purchase-scope-note" in css


def test_purchase_form_is_a_draft_until_finalized():
    page = (ROOT / "templates/admin/purchases.html").read_text()
    detail = (ROOT / "templates/admin/purchases_detail.html").read_text()
    assert "ثبت پیش‌نویس فاکتور" in page
    assert "/finalize" in detail
    assert "نهایی‌سازی فاکتور" in detail
    # The row mirrors its variant: quantities and costs are read, never typed.
    assert "purchase-item-mirror" in page
    # Money is recorded when the invoice is finalised, not while drafting it.
    assert 'name="payment_amount"' not in page
    assert 'name="payment_amount"' not in detail


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
    # The picker's chosen colour reaches the field in upper-case hex.
    assert "field.value = hex.toUpperCase()" in js


def test_color_palette_suggests_the_persian_name_until_hand_edited():
    js = (ROOT / "static/js/app.js").read_text()
    # A palette pick suggests the Persian name in the sibling «رنگ» field.
    assert "findColorNameField" in js
    assert "nearestColorName" in js
    # A hand on the name field ends the sync; a synthetic fill never counts
    # as a hand, so later picks keep following until a real keystroke.
    assert "syntheticFill" in js
    assert 'dataset.touched' in js
    # Both shapes are covered: the variant page (#color / #color_code) and
    # product-form rows (variant_color_N / variant_color_code_N).
    assert "#color" in js or '"color"' in js or "'color'" in js


def test_product_form_variant_script_survives_a_boosted_return():
    page = (ROOT / "templates/admin/product_form.html").read_text()
    # `var`, never `let`/`const`, at script top level: htmx re-runs this
    # script on a boosted return, and a re-declared `let` throws —
    # «افزودن تنوع» dies with it.
    assert "let variantIndex" not in page
    assert "const container = document.getElementById('variants-container')" not in page
    # The capsule toggle binds the live container every run: htmx swaps the
    # body on a return, so a window-once flag would pin the listener to a
    # node no longer in the document and the arrow would go dead.
    assert "__variantFormBound" not in page
    # The next free row index is re-derived from the hidden markers, so a
    # return never reuses a field name already in the DOM.
    assert 'input[name^="variant_index_"]' in page
    # app.js is deferred and may not be back on a return: digit helpers are
    # reached through window with a plain-string fallback.
    assert "window.toEnglishDigits" in page


def test_product_form_validates_required_fields_in_the_page():
    page = (ROOT / "templates/admin/product_form.html").read_text()
    css = (ROOT / "static/css/style.css").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    # Native browser bubbles are English and can point at a folded variant row;
    # the form answers beside the field and opens the row it names.
    assert "novalidate" in page
    assert 'id="product-form-error"' in page
    assert 'id="add-variant-btn"' in page
    assert "classList.add('is-invalid')" in page
    assert "setEntryOpen(entry, true)" in page
    assert ".form-group .field-error" in css
    # The shell boosts every form, so a boosted submit sends its own request:
    # the flight itself has to be stopped, not just the submit event.
    assert "htmx:beforeRequest" in page
    # A refused submit must leave the button usable — a locked button after a
    # validation refusal is how a shop loses the sale.
    assert "event.defaultPrevented" in js


def test_a_new_variant_row_inherits_the_run_from_the_row_above():
    """A size is not typed from scratch: the new row takes the colour, the
    price and the shelf from the row above it, so a run of sizes costs two
    answers per row — the size and the stock."""
    page = (ROOT / "templates/admin/product_form.html").read_text()
    match = re.search(r"var SHARED_FIELDS = \[([^\]]*)\]", page)
    assert match, "the inherited fields are declared in one place"
    fields = [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]
    # What every size of one garment shares.
    assert fields == ["color", "color_code", "price", "cost_price", "storage_location"]
    # What never rides along: the size (a forgotten size would become a twin
    # row), the stock (it would invent units), the barcode (unique in the
    # database), the SKU (derived from the size) and the weight (sizes of one
    # product rarely share one).
    for field in ("size", "stock", "barcode", "sku", "weight"):
        assert field not in fields
    # The photo travels with the colour.
    assert "function copyPhoto" in page
    assert "new DataTransfer()" in page
    # The copy happens where the row is built.
    assert "copySharedFields(previous, fresh)" in page


def test_variant_capsule_is_a_designed_component():
    form = (ROOT / "templates/admin/product_form.html").read_text()
    css = (ROOT / "static/css/style.css").read_text()
    motion = (ROOT / "static/css/motion.css").read_text()
    # The header carries its own affordances: an index chip, the garment dot
    # and a drawn chevron — never a bare text glyph.
    assert "variant-entry-index" in form
    assert "data-dot" in form
    assert "variant-entry-chevron" in form
    assert "<svg" in form and "variant-entry-caret" not in form
    # The accordion motion lives in motion.css beside the scale, so the theme
    # catalogue never owns a duration.
    assert ".variant-entry-body" in motion
    assert "var(--acc-expand)" in motion
    assert "var(--acc-" not in css
    assert "var(--duration-" not in css
    # Fills read the contrast-derived brand pair, never a raw palette hue.
    assert "--on-candy" not in css
    # Reduced motion covers the capsule's transitions.
    assert ".variant-entry-body" in motion.split("prefers-reduced-motion")[1]


COLOR_NAME_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const noop = function () { return undefined; };
const sandbox = {
    document: {
        addEventListener: noop,
        readyState: 'loading',
        querySelectorAll: function () { return []; },
        querySelector: function () { return null; },
        getElementById: function () { return null; },
        createElement: function () {
            return { style: {}, dataset: {}, classList: { add: noop, remove: noop, toggle: noop },
                     setAttribute: noop, addEventListener: noop, appendChild: noop, insertBefore: noop };
        },
    },
    setTimeout: noop, clearTimeout: noop, setInterval: noop, clearInterval: noop,
    console: console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

function name(hex) {
    return vm.runInContext('nearestColorName(' + JSON.stringify(hex) + ')', sandbox);
}

function hslHex(h, s, l) {
    s /= 100; l /= 100;
    const k = function (n) { return (n + h / 30) % 12; };
    const a = s * Math.min(l, 1 - l);
    const f = function (n) {
        const v = l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
        return Math.round(255 * v).toString(16).padStart(2, '0');
    };
    return '#' + f(0) + f(8) + f(4);
}

const named = {};
['#7B1E3A', '#801E3C', '#7E1518', '#2BC4C2', '#2E9E6B', '#6F7B3E',
 '#274A5C', '#A6A6A6', '#563321', '#C9A227', '#C8A8E8', '#6FB6E8',
 '#4C2E52', '#FFFFFF', '#000000', '#1E3A8A', '#2563EB', '#DC2626',
 '#EC4899', '#16A34A', '#E7D8B7', '#F4C2C2', '#C0FFC0'].forEach(function (hex) { named[hex] = name(hex); });

const sweep = [];
for (let hue = 0; hue < 360; hue += 30) {
    [32, 62].forEach(function (l) { sweep.push([hslHex(hue, 68, l), name(hslHex(hue, 68, l))]); });
}

const tableNames = vm.runInContext('COLOR_NAMES.map(function (pair) { return pair[0]; })', sandbox);
const familyNames = vm.runInContext('COLOR_FAMILIES.reduce(function (all, group) { return all.concat(group[1]); }, [])', sandbox);

console.log(JSON.stringify({
    named: named,
    sweep: sweep,
    empty: name(''),
    trash: name('#xyz'),
    short: name('#4AA'),
    table_names: tableNames,
    family_names: familyNames,
}));
"""


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="a JavaScript engine is needed to read the colour names")


@needs_node
def test_custom_palette_picks_are_named_precisely(tmp_path):
    """The complaint this answers: a custom pick snapped to the nearest of the
    eight presets, so a زرشکی read «قرمز» and a طوسی read «سفید». The name now
    comes from a wide garment-colour table by weighted distance, so the picked
    colour names itself — however far from the presets it falls."""
    script = tmp_path / "colour_names.js"
    script.write_text(COLOR_NAME_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(script), str(ROOT / "static/js/app.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    # The names a shopkeeper writes: wines, muted greys, dusted teals.
    assert out["named"]["#7B1E3A"] == "زرشکی"
    assert out["named"]["#801E3C"] == "زرشکی"
    assert out["named"]["#7E1518"] == "جگری"
    assert out["named"]["#2BC4C2"] == "فیروزه‌ای"
    assert out["named"]["#2E9E6B"] == "یشمی"
    assert out["named"]["#6F7B3E"] == "زیتونی"
    assert out["named"]["#274A5C"] == "نفتی"
    assert out["named"]["#A6A6A6"] == "طوسی"
    assert out["named"]["#563321"] == "شکلاتی"
    assert out["named"]["#C9A227"] == "خردلی"
    assert out["named"]["#C8A8E8"] == "یاسی"
    assert out["named"]["#6FB6E8"] == "آبی آسمانی"
    assert out["named"]["#4C2E52"] == "بادمجانی"
    # Washed pastels hold their own family instead of falling into a neutral:
    # a baby pink is a صورتی, a pale mint is a نعنایی.
    assert out["named"]["#F4C2C2"] == "صورتی روشن"
    assert out["named"]["#C0FFC0"] == "نعنایی"

    # The eight presets name themselves the same way when their hex is typed —
    # the preset row and the custom picker speak one vocabulary.
    assert out["named"]["#FFFFFF"] == "سفید"
    assert out["named"]["#000000"] == "مشکی"
    assert out["named"]["#1E3A8A"] == "سرمه‌ای"
    assert out["named"]["#2563EB"] == "آبی"
    assert out["named"]["#DC2626"] == "قرمز"
    assert out["named"]["#EC4899"] == "صورتی"
    assert out["named"]["#16A34A"] == "سبز"
    assert out["named"]["#E7D8B7"] == "کرم"

    # Most of the wheel is covered: every sweep colour gets a name, and the
    # names spread across the vocabulary rather than collapsing to a base eight.
    assert all(n for _, n in out["sweep"])
    assert len({n for _, n in out["sweep"]}) >= 12

    # Nothing sensible to say about nothing: empty and broken stay empty; a
    # short #abc still names itself.
    assert out["empty"] == "" and out["trash"] == ""
    assert out["short"]

    # Every table colour sits in exactly one palette family: the grid the
    # shop picks from and the matcher that names picks speak one vocabulary,
    # and no colour is orphaned from the grid.
    assert sorted(out["family_names"]) == sorted(out["table_names"])


def test_the_palette_picker_offers_every_named_colour():
    """The popup is the shop's own palette now: the quick row it had, then a
    chip for every named colour under its family, the name riding hover and
    focus — with the native picker kept for anything off the table."""
    js = (ROOT / "static/js/app.js").read_text()
    assert "COLOR_FAMILIES" in js
    assert "color-family-row" in js and "color-family-label" in js
    assert "color-pick-name" in js
    assert "'رنگ دلخواه…'" in js  # the native picker stays for off-table colours
    # The palette is fixed to the viewport and placed by script — the variant
    # row clips its children (`overflow: clip` for the accordion), so a
    # palette living inside the card would be cut off.
    assert "placePop" in js
    assert "document.body.appendChild(pop)" in js
    css = (ROOT / "static/css/style.css").read_text()
    assert ".color-family-label" in css and ".color-pick-name" in css
    assert ".color-presets" in css and "overflow-y: auto" in css and "position: fixed" in css
