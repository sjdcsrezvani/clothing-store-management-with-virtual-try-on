"""Referral API uses the shop's configured discount values."""
from models import Customer, Referral, Settings


def _set(db, key, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(Settings(key=key, value=str(value)))
    db.commit()


def test_api_customer_uses_configured_referral_discounts(client, db_session):
    _set(db_session, "default_referrer_discount", 12345)
    _set(db_session, "default_referred_discount", 6789)
    referrer = Customer(phone="09120000001", referral_code="REF123")
    db_session.add(referrer)
    db_session.commit()

    resp = client.post(
        "/api/customers",
        headers={"X-API-Token": "test-api-token"},
        json={"phone": "09120000002", "referrer_code": "REF123"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["referred_discount"] == 6789

    db_session.expire_all()
    referrer = db_session.query(Customer).filter(Customer.phone == "09120000001").one()
    assert referrer.referrer_discount == 12345
    referral = db_session.query(Referral).filter(Referral.referrer_id == referrer.id).one()
    assert referral.referrer_discount == 12345
    assert referral.referred_discount == 6789
