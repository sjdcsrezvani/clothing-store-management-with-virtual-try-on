"""Test setup — MUST set env vars before anything imports the app modules.

Uses a throwaway SQLite database and stubs the SMS gateway so tests never touch
the network or real customer data.
"""
import os
import re
import sys
from pathlib import Path

# Point the app at a scratch DB + fixed secrets BEFORE importing it.
_TEST_DB = Path(__file__).parent / "test_app.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
for _suffix in ("-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["ADMIN_PASSWORD"] = "test-admin-pass"
os.environ["API_TOKEN"] = "test-api-token"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["SMS_API_KEY"] = ""  # never attempt a real SMS in tests
os.environ["TRYON_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from database import Base, engine, SessionLocal
from main import app
from services.store import invalidate_store_cache


@pytest.fixture()
def client():
    """Fresh TestClient per test so cookies/sessions never leak between tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    """Fresh session per test — never shares identity maps across tests."""
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _clean_db(client, db_session):
    """Reset all tables before each test so tests are independent."""
    from models import (SaleCampaign, SaleItem, Sale, POSTransaction, StockMovement, GeneratedImage,
                        Referral, Customer, ProductVariant, Product,
                        Campaign, Settings, AdminLog, Payment, Expense,
                        Purchase, PurchaseItem, Supplier)
    for model in (SaleCampaign, SaleItem, POSTransaction, StockMovement, Sale, GeneratedImage, Payment,
                  Referral, Customer, ProductVariant, Product, PurchaseItem,
                  Purchase, Expense, Supplier, Campaign, Settings, AdminLog):
        db_session.query(model).delete()
    db_session.commit()
    db_session.expire_all()  # drop stale identity-map entries
    invalidate_store_cache()
    yield


def csrf_token(client, url="/admin/login") -> str:
    """GET a page, extract the CSRF token from its meta tag (auto-injected by base.html)."""
    resp = client.get(url)
    match = re.search(r'name="csrf-token" content="([^"]+)"', resp.text)
    assert match, f"No csrf meta found on {url} (status {resp.status_code})"
    return match.group(1)


@pytest.fixture()
def authed(client):
    """Log in as admin and return the client."""
    token = csrf_token(client)
    resp = client.post("/admin/login", data={"password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:300]
    return client


@pytest.fixture(scope="session")
def session_cleanup():
    yield
    for suffix in ("", "-wal", "-shm"):
        Path(str(_TEST_DB) + suffix).unlink(missing_ok=True)
