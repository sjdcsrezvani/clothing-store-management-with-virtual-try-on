"""Test setup — MUST set env vars before anything imports the app modules.

Uses a throwaway SQLite database and stubs the SMS gateway so tests never touch
the network or real customer data.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

# Point the app at a scratch DB + fixed secrets BEFORE importing it.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="raykids-tests-"))
_TEST_DB = _TEST_DIR / "test_app.db"

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
from services.customers import invalidate_customer_cache
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
    from models import (BusinessEvent, StaffUser, CheckReminder, CheckRecord, TagPrintBatchLine, TagPrintBatch, SaleCampaign, SaleItem, Sale, POSTransaction, CheckoutEvent, CheckoutSession, StockReservation, StockMovement, GeneratedImage,
                        SmsMessage, SmsTemplate, SmsDevice, BackgroundJob,
                        Referral, Customer, ProductVariant, Product, CampaignAssignment,
                        Campaign, Settings, AdminLog, Payment, Expense, CashSessionEntry, Purchase, PurchaseItem, Supplier, Refund, RefundLine, PaymentReversal, FinancialEntry, SalaryPayment, CashSession, CashSessionEntry, SupplierPayment,
                        VariantImage, ProductImage)
    # The receipt stamp points variants at their purchase: null it before the
    # purchases go, or the foreign key refuses the cleanup itself.
    db_session.query(ProductVariant).update({ProductVariant.received_purchase_id: None})
    db_session.flush()
    # Children before parents: supplier_payments reference purchases, so they
    # must go first or a linked payment blocks the purchase delete.
    # CampaignAssignment points at both campaigns and sales, so it must go before
    # either of them or SQLite refuses the parent delete — and a cash session
    # entry points at its shift, so it goes before that too.
    # Variant images die with their variants; tag templates are left standing
    # exactly as the suite always has — products may still be wearing them.
    for model in (BusinessEvent, TagPrintBatchLine, TagPrintBatch, CheckoutEvent, StockReservation, CheckoutSession, CheckReminder, CheckRecord, RefundLine, FinancialEntry, PaymentReversal, Refund, SalaryPayment, CampaignAssignment, SaleCampaign, SaleItem, StockMovement, POSTransaction, Payment, Sale, GeneratedImage,
                  SmsMessage, SmsTemplate, SmsDevice, BackgroundJob,
                  Referral, SupplierPayment, PurchaseItem, Purchase, VariantImage, ProductImage, ProductVariant, Product, Expense,
                  Campaign, CashSessionEntry, CashSession, AdminLog, StaffUser, Settings, Customer):
        db_session.query(model).delete()
    db_session.commit()
    db_session.expire_all()  # drop stale identity-map entries
    invalidate_store_cache()
    # The customer-module flags (child profiles, birthday target) are cached the
    # same way the store profile is, so they must be dropped between tests too.
    invalidate_customer_cache()
    from services.themes import invalidate_theme_cache
    invalidate_theme_cache()
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
    resp = client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass", "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303, resp.text[:300]
    return client


@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    yield
    for suffix in ("", "-wal", "-shm"):
        Path(str(_TEST_DB) + suffix).unlink(missing_ok=True)
    _TEST_DIR.rmdir()
