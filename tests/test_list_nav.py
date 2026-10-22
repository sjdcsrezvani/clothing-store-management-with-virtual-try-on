"""Reload-free list controls, phase 3: every sort header, page link and GET
filter form lives inside a [data-list-region] whose own hx- attributes tell
htmx to fetch that URL and swap only the region — the server still draws the
whole page, htmx selects the region out of it, so no-JS browsers get the same
list through plain navigation and a scrolled page never jumps.

The bridge htmx cannot express in attributes lives in htmx-shell.js: a POST
inside a region and any control that leaves the page never swap, the veil is
worn while rows fly, and focus lands on the marked heading after the swap.
"""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

# Every list page and the name of its swappable zone. Mutually exclusive views
# (the ledger and its reconcile list) share one name: only one ever renders.
REGIONS = {
    "templates/admin/products.html": "products",
    "templates/admin/sales.html": "sales",
    "templates/admin/purchases.html": "purchases",
    "templates/admin/expenses.html": "expenses",
    "templates/admin/customers.html": "customers",
    "templates/admin/credit.html": "credit",
    "templates/admin/checks.html": "checks",
    "templates/admin/pos_reconciliation.html": "pos",
    "templates/admin/campaigns.html": "campaigns",
    "templates/admin/sms_history.html": "sms-history",
    "templates/admin/inventory_movements.html": "movements",
    "templates/admin/barcode_print.html": "tags",
    "templates/admin/tier_downgrades.html": "tier-downgrades",
    "templates/admin/follow-ups.html": "follow-ups",
    "templates/admin/suppliers.html": "suppliers",
    "templates/admin/tier_up.html": "tier-up",
    "templates/admin/cashbox.html": "cashbox-shifts",
    "templates/admin/birthdays.html": "birthdays",
    "templates/admin/staff.html": "staff",
}

LIST_PAGES = {
    "/admin/products": "products",
    "/sales": "sales",
    "/admin/purchases": "purchases",
    "/admin/expenses": "expenses",
    "/admin/customers": "customers",
    "/admin/credit": "credit",
    "/admin/checks": "checks",
    "/admin/pos-reconciliation": "pos",
    "/admin/campaigns": "campaigns",
    "/admin/sms/history": "sms-history",
    "/admin/inventory-movements": "movements",
    "/admin/barcodes/print": "tags",
}

# The attributes every region must carry, in one string so a hand-edit that
# drops one of them fails loudly (the template editor's copy remains exact).
REGION_ATTRS = ('hx-boost="true" hx-target="closest [data-list-region]" '
                'hx-swap="outerHTML" hx-select="[data-list-region=')


def test_the_transport_served_by_htmx_not_list_nav():
    base = (TEMPLATES / "base.html").read_text()
    assert "list_nav.js" not in base
    assert not (ROOT / "static/js/list_nav.js").exists()


def test_the_bridge_keeps_the_loose_ends():
    shell = (ROOT / "static/js/htmx-shell.js").read_text()
    # A control that leaves the page never swaps into the region.
    assert "target.pathname === window.location.pathname" in shell
    # A POST inside a region stays a plain POST, dialogs and all.
    assert "elt.submit()" in shell
    # One flight per region, and the veil prints busy to assistive tech.
    assert "'aria-busy'" in shell
    assert "preventDefault()" in shell
    # Focus moves onto the marked heading; the scroll never does.
    assert "preventScroll" in shell
    assert "[data-list-heading]" in shell


def test_region_veil_is_motion_safe():
    css = (ROOT / "static/css/style.css").read_text()
    assert "[data-list-region].is-loading" in css
    assert "prefers-reduced-motion" in css


def test_every_region_carries_its_wiring():
    for template, name in REGIONS.items():
        source = (ROOT / template).read_text()
        assert f'data-list-region="{name}"' in source, template
        # Each marker travels with the boost wiring that points back at itself.
        assert (f'data-list-region="{name}" {REGION_ATTRS}{name}]"'
                in source), template


def test_list_controls_come_after_the_region_marker():
    """Sort headers and page links must sit inside the region — a control
    above the marker would reload the page on click."""
    for template, name in REGIONS.items():
        source = (ROOT / template).read_text()
        marker = source.index(f'data-list-region="{name}"')
        for control in ('class="sort-link"', "product-pagination", "tag-pagination"):
            for index in _occurrences(source, control):
                assert index > marker, f"{template}: {control} sits outside the region"


def test_get_forms_inside_regions_stay_on_the_page():
    """A GET form the region boost intercepts must answer on the same path: the
    swap replaces the region with the answer's own region, so a form posting
    elsewhere would paint the wrong list. (The products search form stays
    outside its region on purpose — it redefines the result set and the KPIs
    with it, so it keeps its full reload.)"""
    import re

    form_paths = {
        "templates/admin/products.html": ["/admin/products"],
        "templates/admin/sales.html": [],
        "templates/admin/purchases.html": ["/admin/purchases"],
        "templates/admin/expenses.html": ["/admin/expenses", "/admin/expenses"],
        "templates/admin/customers.html": ["/admin/customers", "/admin/customers"],
        "templates/admin/credit.html": ["/admin/credit", "/admin/credit"],
        "templates/admin/checks.html": ["/admin/checks", "/admin/checks"],
        "templates/admin/pos_reconciliation.html": ["/admin/pos-reconciliation", "/admin/pos-reconciliation"],
        "templates/admin/campaigns.html": ["/admin/campaigns", "/admin/campaigns"],
        "templates/admin/sms_history.html": ["/admin/sms/history", "/admin/sms/history"],
        "templates/admin/inventory_movements.html": ["/admin/inventory-movements", "/admin/inventory-movements", "/admin/inventory-movements"],
        "templates/admin/barcode_print.html": ["/admin/barcodes/print"],
        "templates/admin/tier_downgrades.html": ["/admin/tier-downgrades"],
        "templates/admin/follow-ups.html": ["/admin/follow-ups"],
        "templates/admin/suppliers.html": ["/admin/suppliers", "/admin/suppliers"],
        "templates/admin/tier_up.html": ["/admin/tier-up"],
        "templates/admin/cashbox.html": ["/admin/cashbox", "/admin/cashbox"],
        "templates/admin/birthdays.html": ["/admin/birthdays"],
        "templates/admin/staff.html": ["/admin/staff", "/admin/staff"],
    }
    for template, name in REGIONS.items():
        source = (ROOT / template).read_text()
        region = source[source.index(f'data-list-region="{name}"'):]
        actions = re.findall(r'<form[^>]*method="get"[^>]*action="([^"]+)"', region)
        assert sorted(actions) == sorted(form_paths[template]), \
            f"{template}: region GET forms changed: {actions}"


def _occurrences(source: str, needle: str):
    start = 0
    while True:
        index = source.find(needle, start)
        if index < 0:
            return
        yield index
        start = index + 1


def test_widget_regions_rebind_after_swap():
    products = (ROOT / "templates/admin/products.html").read_text()
    assert "initProductsRegion(region)" in products
    tags = (ROOT / "templates/admin/barcode_print.html").read_text()
    assert "bindTagRegion(region)" in tags
    customers = (ROOT / "templates/admin/customers.html").read_text()
    assert "initCustomersRegion(region)" in customers


def test_list_pages_serve_their_region(client, authed):
    for path, name in LIST_PAGES.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert f'data-list-region="{name}"' in response.text, path


def test_products_region_holds_sort_page_and_size_controls():
    html = (ROOT / "templates/admin/products.html").read_text()
    region = html[html.index('data-list-region="products"'):]
    assert 'sort=name' in region and 'sort=price' in region and 'sort=stock' in region
    assert 'per-page-form' in region


def test_same_path_links_are_swappable_and_escapes_are_not():
    """The region only takes same-path links: row actions that leave the page
    must keep navigating normally — the bridge in htmx-shell.js sees them at
    configRequest and hands them back to the browser."""
    html = (ROOT / "templates/admin/products.html").read_text()
    assert "/admin/products/{{ product.id }}" in html  # edit leaves the page
    assert "/admin/products/export?" in html  # the CSV is a download, not rows


SHELL_BOOT_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const listeners = { document: [], body: [] };
const doc = {
    body: null,
    addEventListener: (type, handler) => listeners.document.push([type, handler]),
    querySelector: () => null,
};
const sandbox = {
    document: doc,
    window: { location: { href: 'http://shop/', assign: () => {} } },
    EventTarget, Map, URL, console,
};
vm.createContext(sandbox);
// The head pass, with no body on the document, exactly as the browser runs it.
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const before = listeners.body.length;
// The body arrives; the shell's page-level listeners land now.
doc.body = { addEventListener: (type, handler) => listeners.body.push([type, handler]) };
listeners.document
    .filter(([type]) => type === 'DOMContentLoaded')
    .forEach(([, handler]) => handler());
console.log(JSON.stringify({
    before,
    after: listeners.body.map(([type]) => type),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="a JavaScript engine is needed to boot the shell")
def test_the_shell_binds_its_body_bridge_after_the_body_exists(tmp_path):
    """The shell loads in <head>, where document.body is null. Binding it
    there threw, and every list page lost the bridge: a «ویرایش» click (or any
    row control that leaves the page) pushed the URL and swapped nothing."""
    script = tmp_path / "shell_boot.js"
    script.write_text(SHELL_BOOT_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [shutil.which("node"), str(script), str(ROOT / "static/js/htmx-shell.js")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["before"] == 0
    assert "htmx:configRequest" in out["after"]
    assert "htmx:beforeRequest" in out["after"]
    assert "htmx:load" in out["after"]
