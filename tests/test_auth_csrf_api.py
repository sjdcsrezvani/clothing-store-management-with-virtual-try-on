"""Auth (session login), CSRF protection, and the /api/* token gate."""
from tests.conftest import csrf_token


def test_admin_routes_redirect_when_logged_out(client):
    for url in ("/admin/", "/admin/customers", "/admin/settings", "/admin/analytics"):
        resp = client.get(url, follow_redirects=False)
        assert resp.status_code == 303, url
        assert "/admin/login" in resp.headers["location"], url


def test_login_wrong_password_shows_error(client):
    token = csrf_token(client)
    resp = client.post("/admin/login", data={"password": "wrong", "csrf_token": token})
    assert resp.status_code == 200
    assert "رمز عبور اشتباه" in resp.text


def test_login_correct_password_grants_access(client):
    token = csrf_token(client)
    resp = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/admin")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "داشبورد مدیریت" in resp.text


def test_logout_clears_session(client, authed):
    resp = client.post("/admin/logout", data={"csrf_token": csrf_token(client, "/admin/")}, follow_redirects=False)
    assert resp.status_code == 303
    resp = client.get("/admin/", follow_redirects=False)
    assert resp.status_code == 303


def test_post_without_csrf_token_is_rejected(client):
    resp = client.post("/admin/login", data={"password": "test-admin-pass"}, follow_redirects=False)
    assert resp.status_code == 403


def test_post_with_wrong_csrf_token_is_rejected(client):
    token = csrf_token(client)
    resp = client.post("/admin/login", data={"password": "test-admin-pass", "csrf_token": "bogus"}, follow_redirects=False)
    assert resp.status_code == 403


def test_post_with_valid_csrf_token_succeeds(client):
    token = csrf_token(client)
    resp = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303


def test_api_stats_requires_token(client):
    assert client.get("/api/stats").status_code == 401


def test_api_stats_accepts_header_token(client):
    resp = client.get("/api/stats", headers={"X-API-Token": "test-api-token"})
    assert resp.status_code == 200
    assert "total_customers" in resp.json()


def test_api_stats_rejects_wrong_token(client):
    resp = client.get("/api/stats", headers={"X-API-Token": "nope"})
    assert resp.status_code == 401


def test_api_stats_accepts_admin_session(client, authed):
    resp = client.get("/api/stats")
    assert resp.status_code == 200


def test_api_customers_requires_token(client):
    resp = client.post("/api/customers", json={"phone": "09120000001"})
    assert resp.status_code == 401


def test_tryon_generate_reaches_quota_check_without_request_name_error(client, db_session):
    from models import Product, ProductVariant, Settings

    product = Product(name="لباس تست")
    db_session.add(product)
    db_session.flush()
    db_session.add(ProductVariant(
        product_id=product.id,
        price=100_000,
        cost_price=50_000,
        stock_quantity=1,
        barcode="TRYON-001",
    ))
    db_session.add(Settings(key="tryon_daily_limit", value="0"))
    db_session.commit()

    response = client.post(
        "/api/image-gen/generate",
        json={"barcodes": ["TRYON-001"]},
        headers={"X-API-Token": "test-api-token"},
    )

    assert response.status_code == 429
    assert "سقف تولید روزانه" in response.json()["detail"]


def test_store_branding_renders(client, db_session):
    from models import Settings
    from services.store import invalidate_store_cache

    resp = client.get("/admin/login")
    assert "رای کیدز" in resp.text  # default store name

    row = db_session.query(Settings).filter(Settings.key == "store_name").first()
    if row:
        row.value = "فروشگاه نمونه"
    else:
        db_session.add(Settings(key="store_name", value="فروشگاه نمونه"))
    db_session.commit()
    invalidate_store_cache()

    resp = client.get("/admin/login")
    assert "فروشگاه نمونه" in resp.text
