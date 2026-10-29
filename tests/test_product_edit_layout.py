"""The product edit page keeps its answers in one form but shows one section
at a time: base details and variants are tabs, the summary sidebar never
scrolls away, and a direct open saves back onto the same page.

The variant edit page is the reference layout (main column + sticky side);
these tests pin the product page to the same contract.
"""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from models import Product, ProductVariant
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]


def _product(db_session, *, variant=True):
    product = Product(name="تیشرت تبی", category="تیشرت", brand="رای کیدز")
    db_session.add(product)
    db_session.flush()
    if variant:
        db_session.add(ProductVariant(
            product_id=product.id, size="M", color="آبی",
            price=250000, stock_quantity=4, barcode="TAB-001",
        ))
    db_session.commit()
    return product


def test_edit_page_is_a_two_pane_tabbed_editor(client, db_session, authed):
    product = _product(db_session)
    page = client.get(f"/admin/products/{product.id}").text

    assert "product-edit-layout" in page
    assert 'data-existing-variants="1"' in page
    assert 'role="tablist"' in page
    for tab in ("product", "variants"):
        assert f'data-product-tab="{tab}"' in page
        assert f'data-product-panel="{tab}"' in page
        # Both panels ship visible: without JS the page still shows everything.
        assert f'data-product-panel="{tab}">' in page
    assert "product-summary-card" in page
    assert "خلاصه محصول" in page
    assert 'form="product-form"' in page
    assert f'action="/admin/products/{product.id}/delete"' in page
    assert "product-tab-count" in page
    assert 'id="product-ui-tab"' in page


def test_add_page_gets_the_checklist_not_the_stats(client, authed):
    page = client.get("/admin/products/add").text
    assert "product-edit-layout" in page
    assert "product-checklist" in page
    assert "خلاصه محصول" not in page


def test_tab_script_keeps_the_view_and_validation_opens_it():
    page = (ROOT / "templates/admin/product_form.html").read_text()
    assert "RaykidProductTabs" in page
    assert "data-active-tab" in page
    assert "history.replaceState" in page
    assert "location.hash === '#variants'" in page
    # Hidden tab fields are validated, and the first problem opens its panel.
    assert "!hiddenBox.matches('[data-product-panel]')" in page
    assert "RaykidProductTabs.forField(field)" in page
    assert "RaykidProductTabs.forField(addButton)" in page


def test_direct_open_saves_back_to_the_product(client, db_session, authed):
    product = _product(db_session)
    token = csrf_token(client, f"/admin/products/{product.id}")
    resp = client.post(
        f"/admin/products/{product.id}",
        data={
            "csrf_token": token, "name": "تیشرت تبی", "next": "",
            "ui_tab": "variants",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith(f"/admin/products/{product.id}?msg=")
    assert location.endswith("#variants")


def test_list_open_still_returns_to_the_list(client, db_session, authed):
    product = _product(db_session)
    token = csrf_token(client, f"/admin/products/{product.id}")
    resp = client.post(
        f"/admin/products/{product.id}",
        data={
            "csrf_token": token, "name": "تیشرت تبی",
            "next": "/admin/products?search=تیشرت&state=active", "ui_tab": "variants",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    parts = urlsplit(resp.headers["location"])
    assert parts.path == "/admin/products"
    assert parse_qs(parts.query) == {"search": ["تیشرت"], "state": ["active"]}


def test_a_refused_save_reopens_the_tab_that_needs_an_answer(client, db_session, authed):
    product = _product(db_session, variant=False)
    token = csrf_token(client, f"/admin/products/{product.id}")
    resp = client.post(
        f"/admin/products/{product.id}",
        data={"csrf_token": token, "name": "تیشرت تبی", "ui_tab": "product"},
    )
    assert resp.status_code == 200
    assert "حداقل یک تنوع" in resp.text
    assert 'data-active-tab="variants"' in resp.text
