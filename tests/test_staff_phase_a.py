"""Phase A: the staff list becomes a thin directory; each person gets a page."""
from urllib.parse import unquote

from models import StaffUser
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def _owner_client(client, db_session, name="phasea-owner"):
    owner, password = _staff(db_session, name, "owner")
    _session_as(client, owner, password)
    return owner, password


def test_list_is_thin_directory_with_profile_links(client, db_session):
    _owner_client(client, db_session)
    worker, _ = _staff(db_session, "phasea-cashier", "cashier")
    worker.full_name = "کارمند نمونه"
    db_session.commit()

    page = client.get("/admin/staff").text
    assert "هر نقش چه کارهایی می‌تواند بکند" in page       # permission matrix
    assert "/admin/staff/new" in page                        # hire moved off-list
    assert f"/admin/staff/{worker.id}" in page               # row links to profile
    assert "staff-record" not in page                        # no expanding edit rows
    assert "ثبت پرداخت حقوق" not in page                     # no inline salary boxes


def test_pagination_slices_the_directory(client, db_session):
    _owner_client(client, db_session, name="phasea-owner-pg")
    for n in range(12):
        _staff(db_session, f"phasea-pg-{n:02d}", "cashier")

    first = client.get("/admin/staff?per_page=10").text
    assert "phasea-pg-11" in first                             # newest first
    assert "phasea-pg-00" not in first
    assert 'aria-label="صفحه 2"' in first

    second = client.get("/admin/staff?per_page=10&page=2").text
    assert "phasea-pg-00" in second
    assert "phasea-pg-11" not in second

    clamped = client.get("/admin/staff?per_page=10&page=99")
    assert clamped.status_code == 200                         # clamps to last page


def test_salary_sort_orders_by_money(client, db_session):
    _owner_client(client, db_session, name="phasea-owner-sort")
    low, _ = _staff(db_session, "phasea-low", "cashier")
    low.salary_amount = 5_000_000
    high, _ = _staff(db_session, "phasea-high", "cashier")
    high.salary_amount = 50_000_000
    db_session.commit()

    page = client.get("/admin/staff?sort=salary&dir=desc").text
    assert page.index("phasea-high") < page.index("phasea-low")

    page = client.get("/admin/staff?sort=salary&dir=asc").text
    assert page.index("phasea-low") < page.index("phasea-high")

    bogus = client.get("/admin/staff?sort=banana&dir=sideways")
    assert bogus.status_code == 200                            # unknown sorts degrade


def test_new_page_renders_full_hire_form(client, db_session):
    _owner_client(client, db_session, name="phasea-owner-new")
    page = client.get("/admin/staff/new").text
    for field in ("username", "password", "role", "full_name", "national_id",
                  "iban", "birth_date", "salary_payment_day", "emergency_contact"):
        assert f'name="{field}"' in page


def test_create_lands_on_the_new_profile(client, db_session):
    _owner_client(client, db_session, name="phasea-owner-create")
    token = csrf_token(client, "/admin/staff/new")
    response = client.post("/admin/staff", data={
        "csrf_token": token,
        "username": "phasea-hire",
        "password": "hire-pass",
        "role": "cashier",
        "full_name": "استخدام تازه",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/staff/")
    assert "msg=" in response.headers["location"]


def test_profile_tabs_and_guards(client, db_session):
    owner, owner_password = _owner_client(client, db_session, name="phasea-owner-prof")
    worker, _ = _staff(db_session, "phasea-worker", "cashier")

    for tab in ("overview", "permissions", "payroll"):
        page = client.get(f"/admin/staff/{worker.id}?tab={tab}")
        assert page.status_code == 200
    fallback = client.get(f"/admin/staff/{worker.id}?tab=banana")
    assert fallback.status_code == 200
    assert 'aria-current="page"' in fallback.text
    assert client.get("/admin/staff/999999").status_code == 404

    profile = client.get(f"/admin/staff/{worker.id}").text
    assert 'data-confirm="ورود' in profile                   # dialog surface wired
    assert 'data-confirm-title=' in profile
    assert 'id="staff-confirm"' in profile

    cashier, cashier_password = _staff(db_session, "phasea-peek", "cashier")
    _session_as(client, cashier, cashier_password)
    assert client.get(f"/admin/staff/{worker.id}").status_code == 403
    assert client.get("/admin/staff/new").status_code == 403
    _session_as(client, owner, owner_password)


def test_update_and_disable_land_back_on_profile(client, db_session):
    _owner_client(client, db_session, name="phasea-owner-edit")
    worker, _ = _staff(db_session, "phasea-edit", "cashier")
    worker.phone = "09120000001"
    db_session.commit()
    token = csrf_token(client, f"/admin/staff/{worker.id}")

    # The permissions tab posts the role alone: omitted profile fields must
    # survive the rewrite.
    response = client.post(f"/admin/staff/{worker.id}", data={
        "csrf_token": token,
        "role": "manager",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert unquote(response.headers["location"]) == f"/admin/staff/{worker.id}?msg=اطلاعات کارمند ذخیره شد."
    db_session.refresh(worker)
    assert worker.role == "manager"
    assert worker.phone == "09120000001"
    # The role-only permissions form must not wipe the profile it omits.
    assert worker.username == "phasea-edit"

    response = client.post(f"/admin/staff/{worker.id}/disable",
                           data={"csrf_token": token}, follow_redirects=False)
    assert unquote(response.headers["location"]) == f"/admin/staff/{worker.id}?msg=کاربر غیرفعال شد."


def test_list_shows_last_login_and_inactive_wash(client, db_session):
    from datetime import datetime, timezone
    _owner_client(client, db_session, name="phasea-owner-login")
    worker, _ = _staff(db_session, "phasea-login", "cashier")
    worker.last_login_at = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    worker.is_active = False
    db_session.commit()

    page = client.get("/admin/staff").text
    assert "staff-row-inactive" in page
    assert "غیرفعال" in page
    assert "۱۴۰۵" in page                                      # jalali login stamp
