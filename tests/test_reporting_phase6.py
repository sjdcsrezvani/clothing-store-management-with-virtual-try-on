from datetime import datetime, timedelta, timezone

from models import Customer, Product, ProductVariant, Sale, SaleItem, Expense
from services.reporting import canonical_report, reconciliation_checks


def test_canonical_report_exposes_financial_definitions(db_session):
    customer = Customer(phone="09120000001", referral_code="REPORT1")
    product = Product(name="Report item")
    db_session.add_all([customer, product])
    db_session.flush()
    variant = ProductVariant(product_id=product.id, barcode="REPORT1", price=100, cost_price=40, stock_quantity=3)
    db_session.add(variant)
    db_session.flush()
    sale = Sale(customer_id=customer.id, total_amount=100, discount_amount=10, final_amount=90, payment_method="cash", payment_confirmed=True)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id, quantity=1, unit_price=100, unit_cost=40, total_price=100))
    db_session.add(Expense(amount=20, payment_method="cash"))
    db_session.commit()
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    report = canonical_report(db_session, start, end)
    assert report["gross_sales"] == 100
    assert report["discounts"] == 10
    assert report["net_sales"] == 90
    assert report["cogs"] == 40
    assert report["gross_profit"] == 50
    assert report["operating_expenses"] == 20
    assert report["net_profit"] == 30


def test_reconciliation_checks_return_discrepancy_status(db_session):
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    result = reconciliation_checks(db_session, start, end)
    assert "checks" in result
    assert set(result["checks"]) == {"sales_balance", "inventory_balance", "customer_debt"}
