"""Reload-free list controls: every sort header, page link and GET filter form
lives inside a [data-list-region], so the shell's fetch-and-swap helper can
replace the rows without reloading the document — and the scroll never jumps.

Pages render the region server-side; the helper only ever swaps what the
server drew, so no-JS browsers get the same list through plain navigation.
"""
from pathlib import Path

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
    "templates/admin/campaigns.html": "campaigns",
    "templates/admin/sms_history.html": "sms-history",
    "templates/admin/inventory_movements.html": "movements",
    "templates/admin/barcode_print.html": "tags",
}

LIST_PAGES = {
    "/admin/products": "products",
    "/sales": "sales",
    "/admin/purchases": "purchases",
    "/admin/expenses": "expenses",
    "/admin/customers": "customers",
    "/admin/credit": "credit",
    "/admin/checks": "checks",
    "/admin/campaigns": "campaigns",
    "/admin/sms/history": "sms-history",
    "/admin/inventory-movements": "movements",
    "/admin/barcodes/print": "tags",
}


def test_helper_is_loaded_by_the_shell():
    base = (TEMPLATES / "base.html").read_text()
    assert "list_nav.js" in base


def test_helper_swaps_pushes_and_falls_back():
    helper = (ROOT / "static/js/list_nav.js").read_text()
    assert "DOMParser" in helper  # the server draws; the helper never builds rows
    assert "pushState" in helper  # the address bar stays shareable
    assert "popstate" in helper  # back and forward replay in place
    assert "window.location.href = url" in helper  # a failed fetch still navigates
    assert "aria-busy" in helper
    assert "preventScroll" in helper  # focus moves, the scroll does not


def test_region_veil_is_motion_safe():
    css = (ROOT / "static/css/style.css").read_text()
    assert "[data-list-region].is-loading" in css
    assert "prefers-reduced-motion" in css


def test_every_list_template_marks_its_region():
    for template, name in REGIONS.items():
        source = (ROOT / template).read_text()
        assert f'data-list-region="{name}"' in source, template


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
    """A GET form the helper intercepts must answer on the same path: the swap
    replaces the region with the answer's own region, so a form posting
    elsewhere would paint the wrong list. (The products search form stays
    outside its region on purpose — it redefines the result set and the KPIs
    with it, so it keeps its full reload.)"""
    import re

    form_paths = {
        "templates/admin/products.html": ["/admin/products"],
        "templates/admin/sales.html": [],
        "templates/admin/purchases.html": ["/admin/purchases"],
        "templates/admin/expenses.html": ["/admin/expenses"],
        "templates/admin/customers.html": ["/admin/customers"],
        "templates/admin/credit.html": ["/admin/credit"],
        "templates/admin/checks.html": ["/admin/checks"],
        "templates/admin/campaigns.html": ["/admin/campaigns"],
        "templates/admin/sms_history.html": ["/admin/sms/history"],
        "templates/admin/inventory_movements.html": ["/admin/inventory-movements"],
        "templates/admin/barcode_print.html": ["/admin/barcodes/print"],
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


def test_widget_pages_re_register_after_swap():
    products = (ROOT / "templates/admin/products.html").read_text()
    assert "register('products'" in products
    tags = (ROOT / "templates/admin/barcode_print.html").read_text()
    assert "register('tags'" in tags


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
    """The helper only takes same-path links: row actions that leave the page
    must keep navigating normally."""
    html = (ROOT / "templates/admin/products.html").read_text()
    assert "/admin/products/{{ product.id }}" in html  # edit leaves the page
    assert "/admin/products/export?" in html  # the CSV is a download, not rows
