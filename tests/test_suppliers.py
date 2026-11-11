"""Supplier profiles: the shared add/edit form and its validation."""
from sqlalchemy import create_engine, text

from migrations import MIGRATION_VERSION, upgrade
from models import Supplier
from tests.conftest import csrf_token


def _post(client, data, url="/admin/suppliers/add", csrf_url="/admin/suppliers/new"):
    payload = {"csrf_token": csrf_token(client, csrf_url)}
    payload.update(data)
    return client.post(url, data=payload, follow_redirects=False)


def _full_supplier(**overrides):
    data = {
        "name": "پوشاک آوا",
        "supplier_type": "تولیدی",
        "economic_code": "۱۲۳۴۵۶۷۸۹۰۱۲۳۴",
        "owner_first_name": "مریم",
        "owner_last_name": "رضایی",
        "phone": "۰۹۱۲۱۲۳۴۵۶۷",
        "landline": "۰۲۱-۱۲۳۴۵۶۷۸",
        "city": "تهران",
        "address": "خیابان ولیعصر، پلاک ۱۰",
        "sheba": "123456789012345678901234",
        "note": "تسویه هفتگی",
    }
    data.update(overrides)
    return data


def _named_supplier(db, name="تأمین‌کننده تست", **extra):
    supplier = Supplier(name=name, phone=extra.pop("phone", "09120000000"), **extra)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def test_add_page_has_every_supplier_field_and_no_purchase_fields(client, authed):
    page = authed.get("/admin/suppliers/new")
    assert page.status_code == 200
    for name in ("name", "supplier_type", "economic_code", "owner_first_name",
                 "owner_last_name", "phone", "landline", "city", "address",
                 "sheba", "note"):
        assert f'name="{name}"' in page.text, name
    assert 'name="amount"' not in page.text
    assert 'name="purchase_id"' not in page.text
    assert "عمده‌فروش" in page.text  # default type suggestion


def test_adding_a_supplier_saves_every_profile_field(client, authed, db_session):
    response = _post(authed, _full_supplier())
    assert response.status_code == 303

    supplier = db_session.query(Supplier).filter(Supplier.name == "پوشاک آوا").one()
    assert supplier.supplier_type == "تولیدی"
    assert supplier.economic_code == "12345678901234"
    assert supplier.owner_first_name == "مریم"
    assert supplier.owner_last_name == "رضایی"
    assert supplier.phone == "09121234567"
    assert supplier.landline == "02112345678"
    assert supplier.city == "تهران"
    assert supplier.address == "خیابان ولیعصر، پلاک ۱۰"
    assert supplier.sheba == "IR123456789012345678901234"
    assert supplier.note == "تسویه هفتگی"


def test_mobile_is_required_and_errors_keep_entered_values(client, authed, db_session):
    response = _post(authed, _full_supplier(name="پوشاک بدون موبایل", phone="", owner_first_name="مریم"))
    assert response.status_code == 400
    assert "شماره موبایل الزامی است." in response.text
    assert 'value="پوشاک بدون موبایل"' in response.text
    assert 'value="مریم"' in response.text
    assert db_session.query(Supplier).filter(Supplier.name == "پوشاک بدون موبایل").count() == 0


def test_invalid_mobile_economic_code_and_sheba_are_refused(client, authed, db_session):
    for data, message in (
        (_full_supplier(name="پوشاک نامعتبر", phone="abc"), "شماره موبایل معتبر نیست"),
        (_full_supplier(name="پوشاک نامعتبر", economic_code="123"), "کد اقتصادی باید دقیقاً ۱۴ رقم باشد."),
        (_full_supplier(name="پوشاک نامعتبر", sheba="IR123"), "شبا معتبر نیست"),
    ):
        response = _post(authed, data)
        assert response.status_code == 400
        assert message in response.text
    assert db_session.query(Supplier).filter(Supplier.name == "پوشاک نامعتبر").count() == 0


def test_sheba_accepts_a_full_ir_prefix(client, authed, db_session):
    response = _post(authed, _full_supplier(name="پوشاک با شبا", sheba="ir123456789012345678901234"))
    assert response.status_code == 303
    supplier = db_session.query(Supplier).filter(Supplier.name == "پوشاک با شبا").one()
    assert supplier.sheba == "IR123456789012345678901234"


def test_type_and_city_suggestions_include_saved_values(client, authed, db_session):
    _named_supplier(db_session, name="چرمی", supplier_type="چرم و کیف", city="اصفهان")

    page = authed.get("/admin/suppliers/new")
    assert '<option value="چرم و کیف"></option>' in page.text
    assert '<option value="اصفهان"></option>' in page.text


def test_edit_page_loads_values_and_updates_the_profile(client, authed, db_session):
    supplier = _named_supplier(db_session, name="قدیمی", phone="09121111111")
    page = authed.get(f"/admin/suppliers/{supplier.id}/edit")
    assert page.status_code == 200
    assert f'value="{supplier.name}"' in page.text
    assert f'value="{supplier.phone}"' in page.text

    response = _post(
        authed,
        _full_supplier(name="نام تازه", phone="09122222222", city="کرج"),
        url=f"/admin/suppliers/{supplier.id}/edit",
        csrf_url=f"/admin/suppliers/{supplier.id}/edit",
    )
    assert response.status_code == 303
    db_session.refresh(supplier)
    assert supplier.name == "نام تازه"
    assert supplier.phone == "09122222222"
    assert supplier.city == "کرج"


def test_an_old_supplier_without_mobile_must_gain_one_before_saving(client, authed, db_session):
    supplier = Supplier(name="بدون موبایل")
    db_session.add(supplier)
    db_session.commit()
    db_session.refresh(supplier)

    response = _post(
        authed, {"name": "بدون موبایل", "note": "تغییر"},
        url=f"/admin/suppliers/{supplier.id}/edit",
        csrf_url=f"/admin/suppliers/{supplier.id}/edit",
    )
    assert response.status_code == 400
    assert "شماره موبایل الزامی است." in response.text
    db_session.refresh(supplier)
    assert supplier.note is None

    response = _post(
        authed, {"name": "بدون موبایل", "phone": "09123333333", "note": "تغییر"},
        url=f"/admin/suppliers/{supplier.id}/edit",
        csrf_url=f"/admin/suppliers/{supplier.id}/edit",
    )
    assert response.status_code == 303
    db_session.refresh(supplier)
    assert supplier.phone == "09123333333"
    assert supplier.note == "تغییر"


def test_list_shows_type_and_city_and_links_to_the_edit_page(client, authed, db_session):
    supplier = _named_supplier(
        db_session, name="پوشاک آوا", supplier_type="تولیدی", city="تهران",
        owner_first_name="مریم", owner_last_name="رضایی",
    )
    page = authed.get("/admin/suppliers")
    assert "پوشاک آوا" in page.text
    assert "تولیدی" in page.text
    assert "تهران" in page.text
    assert f'href="/admin/suppliers/{supplier.id}/edit"' in page.text
    assert '<select name="per_page" id="supplier-per-page" data-fancy' in page.text


def test_search_covers_owner_city_type_and_economic_code(client, authed, db_session):
    _named_supplier(
        db_session, name="پوشاک آوا", supplier_type="تولیدی", city="تهران",
        owner_first_name="مریم", owner_last_name="رضایی",
        economic_code="12345678901234",
    )
    _named_supplier(db_session, name="کیف دیگر", supplier_type="چرم", city="اصفهان")

    for query, expected in (
        ("مریم", "پوشاک آوا"),
        ("تهران", "پوشاک آوا"),
        ("تولیدی", "پوشاک آوا"),
        ("12345678901234", "پوشاک آوا"),
    ):
        page = authed.get("/admin/suppliers", params={"q": query})
        assert expected in page.text, query
        # Sorting and paging must carry the active filter, not drop it.
        assert f'<input type="hidden" name="q" value="{query}">' in page.text
        assert f"/admin/suppliers?q={query}&" in page.text


def test_startup_heals_a_supplier_table_stamped_39_without_the_columns(tmp_path):
    from main import _apply_missing_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'drifted.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,"
            " phone VARCHAR(15), note TEXT, created_at DATETIME)"))
    _apply_missing_columns(engine)
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(suppliers)"))}
    assert {"owner_first_name", "owner_last_name", "landline", "city", "address",
            "supplier_type", "economic_code", "sheba"} <= columns


def test_revision_39_adds_the_supplier_profile_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version (id, version) VALUES (1, 38)"))
        conn.execute(text(
            "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,"
            " phone VARCHAR(15), note TEXT, created_at DATETIME)"))
    assert upgrade(engine) == MIGRATION_VERSION
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(suppliers)"))}
    assert {"owner_first_name", "owner_last_name", "landline", "city", "address",
            "supplier_type", "economic_code", "sheba"} <= columns
