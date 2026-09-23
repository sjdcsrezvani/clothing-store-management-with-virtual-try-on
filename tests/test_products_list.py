"""Products list (manage-first pass): sorting, paging, stock chips, highlight,
CSV parity, bulk archive and the expandable variant rows.

Unknown sort keys and page sizes answer the default list — a hand-typed query
is a list, never an error.
"""
from models import Product, ProductVariant


def _product(db_session, name, price=100_000, stock=5, barcode="B-1", category="لباس"):
    product = Product(name=name, category=category)
    db_session.add(product)
    db_session.flush()
    db_session.add(ProductVariant(
        product_id=product.id, price=price,
        stock_quantity=stock, barcode=barcode,
    ))
    db_session.commit()
    return product


def _names_in_order(html, first, second):
    return html.index(first) < html.index(second)


def test_toolbar_names_both_tag_destinations_and_lists_csv(client, authed):
    html = client.get("/admin/products").text
    assert "🏷️ تگ‌ها" in html
    assert 'class="btn btn-ghost">تنظیمات تگ</a>' in html  # a real button, not bare text
    assert "/admin/products/export?" in html


def test_sort_by_name_and_price_reorders_rows(client, authed, db_session):
    _product(db_session, "zz پایانی", price=900_000, barcode="S-1")
    _product(db_session, "aa آغازی", price=100_000, barcode="S-2")

    by_name = client.get("/admin/products?sort=name&dir=asc").text
    assert _names_in_order(by_name, "aa آغازی", "zz پایانی")
    assert 'aria-sort="ascending"' in by_name

    by_price = client.get("/admin/products?sort=price&dir=desc").text
    assert _names_in_order(by_price, "zz پایانی", "aa آغازی")


def test_unknown_sort_and_page_size_fall_back_to_default(client, authed, db_session):
    _product(db_session, "پیش‌فرض", barcode="D-1")
    html = client.get("/admin/products?sort=nope&dir=sideways&per_page=99").text
    assert 'value="10" selected' in html
    # The default list is newest-first: no column claims the sort.
    assert 'aria-sort="none"' in html


def test_per_page_picker_is_offered_and_honoured(client, authed):
    html = client.get("/admin/products?per_page=25").text
    assert 'value="25" selected' in html


def test_stock_chips_narrow_to_low_and_out(client, authed, db_session):
    _product(db_session, "سالم", stock=10, barcode="C-1")
    _product(db_session, "کم", stock=1, barcode="C-2")
    _product(db_session, "تمام", stock=0, barcode="C-3")

    low = client.get("/admin/products?stock=low").text
    assert "<strong>کم</strong>" in low and "<strong>تمام</strong>" in low
    assert "<strong>سالم</strong>" not in low

    out = client.get("/admin/products?stock=out").text
    assert "<strong>تمام</strong>" in out
    assert "<strong>کم</strong>" not in out and "<strong>سالم</strong>" not in out


def test_search_term_is_highlighted_and_escaped(client, authed, db_session):
    _product(db_session, "پیراهن <b>حراج</b>", barcode="H-1")
    html = client.get("/admin/products?search=حراج").text
    assert "<mark>حراج</mark>" in html
    assert "<b>حراج</b>" not in html

    tag_search = client.get("/admin/products?search=<b>").text
    assert "<mark>&lt;b&gt;</mark>" in tag_search


def test_variant_rows_disclose_detail(client, authed, db_session):
    product = _product(db_session, "جزئیات", barcode="V-1")
    html = client.get("/admin/products").text
    assert f'aria-controls="variants-{product.id}"' in html
    assert f'id="variants-{product.id}" hidden' in html
    assert f"/admin/variants/" in html and "ویرایش تنوع" in html


def test_single_delete_now_reads_archive(client, authed, db_session):
    _product(db_session, "بایگانی تکی", barcode="A-1")
    html = client.get("/admin/products").text
    assert "غیرفعال‌کردن" not in html
    assert ">بایگانی</button>" in html
    assert "بایگانی شود؟" in html


def test_csv_export_matches_the_filtered_view(client, authed, db_session):
    _product(db_session, "صادراتی", barcode="E-1")
    _product(db_session, "غیرمرتبط", barcode="E-2")

    response = client.get("/admin/products/export?search=صادراتی")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    body = response.text
    assert body.startswith("\ufeff")
    assert "نام" in body.splitlines()[0]
    assert "صادراتی" in body and "غیرمرتبط" not in body


def test_bulk_archive_deactivates_products_and_variants(client, authed, db_session):
    first = _product(db_session, "انبوه یک", barcode="K-1")
    second = _product(db_session, "انبوه دو", barcode="K-2")
    token = client.get("/admin/products").text.split('name="csrf-token" content="')[1].split('"')[0]

    response = client.post(
        "/admin/products/bulk-archive",
        data={"ids": [str(first.id), str(second.id)], "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]

    db_session.expire_all()
    for product_id in (first.id, second.id):
        product = db_session.get(Product, product_id)
        assert product.is_active is False
        assert all(v.is_active is False for v in product.variants)

    assert "انبوه یک" not in client.get("/admin/products").text


def test_bulk_archive_without_selection_names_it(client, authed):
    token = client.get("/admin/products").text.split('name="csrf-token" content="')[1].split('"')[0]
    response = client.post(
        "/admin/products/bulk-archive",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]


def test_pagination_preserves_every_param(client, authed, db_session):
    for index in range(12):
        _product(db_session, f"صفحه {index:02d}", barcode=f"P-{index}")
    html = client.get("/admin/products?stock=all&sort=name&dir=asc&per_page=10").text
    assert "stock=all" in html and "sort=name" in html and "per_page=10" in html
