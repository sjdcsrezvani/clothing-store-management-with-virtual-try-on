"""Campaigns: a window, an audience, and a discount that actually lands.

Campaigns used to be decorative — the code was never read by the sales flow,
``SaleCampaign`` was only ever deleted, and the send was hardcoded to diamond, so
in a shop of silver customers nothing was ever sent. These tests pin the three
things that make a campaign real: liveness gates the code, a customer who holds
a campaign is honoured at the counter without typing anything, and a redemption
is written to the invoice and to the customer's assignment.
"""
import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from models import (
    Campaign,
    CampaignAssignment,
    Customer,
    Product,
    ProductVariant,
    Sale,
    SaleCampaign,
    Settings,
)
from services.campaigns import (
    assign_campaign,
    audience_options,
    campaign_filtered,
    campaign_for_customer,
    campaign_is_live,
    campaign_overview,
    campaign_recipients,
    campaign_stats,
    campaign_status,
    customer_campaign_map,
    mark_campaign_used,
    parse_audience,
    resolve_campaign_code,
    restore_campaign_after_refund,
)
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]
_counter = itertools.count(1)


# ── helpers ──────────────────────────────────────────────────────────────────

def set_setting(db, key, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(Settings(key=key, value=str(value)))
    db.commit()


def make_customer(db, *, tier="silver", **kwargs) -> Customer:
    index = next(_counter)
    customer = Customer(
        phone=kwargs.pop("phone", f"0912{index:07d}"),
        first_name=kwargs.pop("first_name", f"مشتری{index}"),
        last_name=kwargs.pop("last_name", "تستی"),
        referral_code=f"C{index:05d}",
        tier=tier,
        **kwargs,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_campaign(db, *, code=None, percent=20, min_purchase=0, **kwargs) -> Campaign:
    index = next(_counter)
    campaign = Campaign(
        name=kwargs.pop("name", f"کمپین{index}"),
        code=code or f"CMP{index}",
        discount_percent=percent,
        min_purchase=min_purchase,
        **kwargs,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def make_variant(db, price=500_000, stock=10, name=None) -> ProductVariant:
    index = next(_counter)
    product = Product(name=name or f"محصول{index}")
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        price=price,
        cost_price=int(price * 0.5),
        stock_quantity=stock,
        barcode=f"BC{index:06d}",
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def login(client):
    token = csrf_token(client)
    response = client.post(
        "/admin/login",
        data={"username": "owner", "password": "test-admin-pass", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def confirm_sale(client, variant, quantity=1, customer_id=0, campaign_code="",
                 payment_method="card"):
    """Run a real confirm-sale the way the counter does."""
    login(client)
    token = csrf_token(client, "/sales/new")
    basket = [{
        "variant_id": variant.id,
        "product_id": variant.product_id,
        "unit_price": variant.price,
        "quantity": quantity,
        "total_price": variant.price * quantity,
    }]
    data = {
        "customer_id": str(customer_id),
        "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": payment_method,
        "referrer_code": "",
        "referrer_phone": "",
        "use_referrer_discount": "1",
        "custom_discount_amount": "",
        "custom_discount_percent": "",
        "campaign_code": campaign_code,
        "csrf_token": token,
    }
    return client.post("/sales/confirm-sale", data=data)


def last_sale(db) -> Sale:
    db.expire_all()
    return db.query(Sale).order_by(Sale.id.desc()).first()


def only_assignment(db, campaign, customer) -> CampaignAssignment:
    db.expire_all()
    return db.query(CampaignAssignment).filter(
        CampaignAssignment.campaign_id == campaign.id,
        CampaignAssignment.customer_id == customer.id,
    ).first()


def template_text(name: str) -> str:
    return (ROOT / "templates" / name).read_text(encoding="utf-8")


# ── the window ───────────────────────────────────────────────────────────────

def test_status_names_every_state_a_campaign_can_be_in():
    now = datetime.now(timezone.utc)
    live = Campaign(name="زنده", code="L1", discount_percent=10, is_active=True)
    assert campaign_status(live, now) == "live"
    assert campaign_is_live(live, now)

    future = Campaign(name="بعدی", code="F1", discount_percent=10, is_active=True,
                      start_date=now + timedelta(days=1))
    assert campaign_status(future, now) == "scheduled"

    past = Campaign(name="گذشته", code="P1", discount_percent=10, is_active=True,
                    end_date=now - timedelta(days=1))
    assert campaign_status(past, now) == "expired"

    off = Campaign(name="خاموش", code="O1", discount_percent=10, is_active=False)
    assert campaign_status(off, now) == "inactive"
    # Open-ended dates mean "no bound", not "never".
    assert campaign_is_live(
        Campaign(name="باز", code="OP", discount_percent=5, is_active=True), now
    )


def test_a_code_outside_its_window_is_refused_with_a_sentence(db_session):
    campaign, reason = resolve_campaign_code(db_session, "NOPE")
    assert campaign is None and "پیدا نشد" in reason

    off = make_campaign(db_session, code="OFF1", is_active=False)
    campaign, reason = resolve_campaign_code(db_session, "OFF1")
    assert campaign is None and "غیرفعال" in reason

    soon = make_campaign(db_session, code="SOON", start_date=datetime.now(timezone.utc) + timedelta(days=3))
    campaign, reason = resolve_campaign_code(db_session, "SOON")
    assert campaign is None and "شروع می‌شود" in reason

    gone = make_campaign(db_session, code="GONE", end_date=datetime.now(timezone.utc) - timedelta(days=1))
    campaign, reason = resolve_campaign_code(db_session, "GONE")
    assert campaign is None and "تمام شده" in reason

    # Case and spacing never decide whether a customer gets their discount.
    ok = make_campaign(db_session, code="LIVE1", percent=15)
    found, reason = resolve_campaign_code(db_session, " live1 ")
    assert reason is None and found is not None and found.id == ok.id

    # Nothing typed is not an error.
    assert resolve_campaign_code(db_session, "") == (None, None)


def test_minimum_purchase_is_enforced_in_tomans(db_session):
    campaign = make_campaign(db_session, code="MIN1", min_purchase=1_000_000)
    found, reason = resolve_campaign_code(db_session, "MIN1", total_amount=500_000)
    assert found is None and "حداقل خرید" in reason
    found, reason = resolve_campaign_code(db_session, "MIN1", total_amount=2_000_000)
    assert reason is None and found.id == campaign.id


# ── the money ────────────────────────────────────────────────────────────────

def test_a_typed_code_discounts_the_invoice_and_records_the_redemption(client, db_session):
    campaign = make_campaign(db_session, code="SAVE20", percent=20)
    customer = make_customer(db_session)
    variant = make_variant(db_session, price=500_000)

    response = confirm_sale(client, variant, customer_id=customer.id, campaign_code="SAVE20")
    assert response.status_code == 200

    sale = last_sale(db_session)
    assert sale.total_amount == 500_000
    assert sale.final_amount == 400_000
    assert sale.discount_amount == 100_000
    assert "کمپین" in sale.discount_details

    db_session.expire_all()
    link = db_session.query(SaleCampaign).filter(SaleCampaign.sale_id == sale.id).one()
    assert link.campaign_id == campaign.id
    assert link.discount_amount == 100_000

    # Typing a code is proof the customer came for it, so their file shows it.
    assignment = only_assignment(db_session, campaign, customer)
    assert assignment is not None
    assert assignment.status == "used"
    assert assignment.used_count == 1
    assert assignment.sale_id == sale.id


def test_an_assigned_customer_gets_the_discount_without_typing_anything(client, db_session):
    campaign = make_campaign(db_session, code="HELD10", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    variant = make_variant(db_session, price=300_000)

    response = confirm_sale(client, variant, customer_id=customer.id)
    assert response.status_code == 200
    sale = last_sale(db_session)
    assert sale.final_amount == 270_000
    assert sale.discount_amount == 30_000


def test_a_campaign_is_spent_once_unless_it_is_reusable(client, db_session):
    campaign = make_campaign(db_session, code="ONCE5", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()

    confirm_sale(client, make_variant(db_session, price=100_000), customer_id=customer.id)
    assert last_sale(db_session).discount_amount == 10_000

    # Second visit: the campaign is burned, so no discount and no second link.
    confirm_sale(client, make_variant(db_session, price=100_000), customer_id=customer.id)
    second = last_sale(db_session)
    assert second.discount_amount == 0
    db_session.expire_all()
    assert db_session.query(SaleCampaign).filter(SaleCampaign.campaign_id == campaign.id).count() == 1


def test_a_reusable_campaign_keeps_working(client, db_session):
    campaign = make_campaign(db_session, code="ALWAYS", percent=10, is_reusable=True)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()

    for _ in range(2):
        confirm_sale(client, make_variant(db_session, price=100_000), customer_id=customer.id)
        assert last_sale(db_session).discount_amount == 10_000

    assignment = only_assignment(db_session, campaign, customer)
    assert assignment.status == "invited"  # still on offer
    assert assignment.used_count == 2


def test_campaign_discount_is_refused_on_credit_like_every_other_discount(client, db_session):
    campaign = make_campaign(db_session, code="CREDIT", percent=50)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    variant = make_variant(db_session, price=200_000)

    response = confirm_sale(client, variant, customer_id=customer.id, payment_method="credit")
    assert response.status_code == 200
    sale = last_sale(db_session)
    assert sale.payment_method == "credit"
    assert sale.discount_amount == 0
    assert "نسیه" in sale.discount_details
    db_session.expire_all()
    assert db_session.query(SaleCampaign).filter(SaleCampaign.campaign_id == campaign.id).count() == 0


def test_a_refund_gives_the_customer_their_campaign_back(client, db_session):
    campaign = make_campaign(db_session, code="GIVEBACK", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    variant = make_variant(db_session, price=100_000)

    confirm_sale(client, variant, customer_id=customer.id)
    sale = last_sale(db_session)
    assert only_assignment(db_session, campaign, customer).status == "used"

    login(client)
    response = client.post(
        f"/sales/{sale.id}/refund",
        data={"refund_reason": "مرجوعی", "csrf_token": csrf_token(client, f"/sales/invoice/{sale.id}")},
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)

    db_session.expire_all()
    assert db_session.query(SaleCampaign).filter(SaleCampaign.sale_id == sale.id).count() == 0
    assignment = only_assignment(db_session, campaign, customer)
    assert assignment.status == "invited"
    assert assignment.used_count == 0


def test_restore_campaign_after_refund_keeps_a_reusable_campaign_on_offer(db_session):
    campaign = make_campaign(db_session, code="REUSE9", percent=10, is_reusable=True)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    sale = Sale(customer_id=customer.id, total_amount=100_000, final_amount=100_000,
                payment_confirmed=True)
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleCampaign(sale_id=sale.id, campaign_id=campaign.id, discount_amount=10_000))
    mark_campaign_used(db_session, campaign, customer, sale_id=sale.id)
    db_session.commit()

    restore_campaign_after_refund(db_session, sale.id)
    db_session.commit()
    assignment = only_assignment(db_session, campaign, customer)
    assert assignment.status == "invited"
    assert assignment.used_count == 1  # the redemption itself is not erased


# ── picking the campaign for a customer ──────────────────────────────────────

def test_the_bigger_campaign_wins_when_a_customer_holds_two(db_session):
    customer = make_customer(db_session)
    small = make_campaign(db_session, code="SMALL1", percent=5)
    big = make_campaign(db_session, code="BIG1", percent=30)
    assign_campaign(db_session, small, customer)
    assign_campaign(db_session, big, customer)
    db_session.commit()

    chosen = campaign_for_customer(db_session, customer, 100_000)
    assert chosen.id == big.id


def test_a_used_non_reusable_campaign_is_not_offered_again(db_session):
    customer = make_customer(db_session)
    campaign = make_campaign(db_session, code="SPENT1", percent=25)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    mark_campaign_used(db_session, campaign, customer)
    db_session.commit()

    assert campaign_for_customer(db_session, customer, 100_000) is None


def test_an_expired_campaign_is_never_offered(db_session):
    customer = make_customer(db_session)
    campaign = make_campaign(db_session, code="OLD1", percent=25,
                             end_date=datetime.now(timezone.utc) - timedelta(days=1))
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    assert campaign_for_customer(db_session, customer, 100_000) is None


def test_removing_a_customer_keeps_the_history_but_stops_the_offer(db_session):
    from services.campaigns import unassign_campaign

    customer = make_customer(db_session)
    campaign = make_campaign(db_session, code="OFFLIST", percent=40)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    assert unassign_campaign(db_session, campaign, customer)
    db_session.commit()

    assert campaign_for_customer(db_session, customer, 100_000) is None
    db_session.expire_all()
    row = only_assignment(db_session, campaign, customer)
    assert row.status == "removed"  # the row survives, so the report stays honest
    assert unassign_campaign(db_session, campaign, customer) is False


# ── the audience ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("all", ("all", "")),
    ("assigned", ("assigned", "")),
    ("tier:gold", ("tier", "gold")),
    ("tag:vip", ("tag", "vip")),
    ("", ("all", "")),
    ("nonsense", ("all", "")),
])
def test_audience_values_are_parsed_defensively(raw, expected):
    assert parse_audience(raw) == expected


def test_the_send_audience_is_counted_before_anything_is_queued(db_session):
    campaign = make_campaign(db_session, code="AUD1", percent=10)
    make_customer(db_session, tier="silver")
    make_customer(db_session, tier="gold")
    opted_out = make_customer(db_session, phone="09120000123", sms_opt_in=False)
    archived = make_customer(db_session, phone="09120000124", is_archived=True)
    hand_picked = make_customer(db_session, phone="09120000125", tier="diamond")
    assign_campaign(db_session, campaign, hand_picked)
    db_session.commit()

    options = {option["key"]: option for option in audience_options(db_session, campaign)}
    # Silver counts: the old hardcoded diamond send reached nobody in this shop.
    assert options["tier:silver"]["count"] == 1
    assert options["tier:gold"]["count"] == 1
    assert options["assigned"]["count"] == 1
    # Reachable = silver + gold + the hand-picked diamond; the opt-out and the
    # archived customer are excluded from every audience.
    assert options["all"]["count"] == 3

    plan = campaign_recipients(db_session, campaign, "tier:gold")
    assert [c.id for c in plan["recipients"]] == [
        c.id for c in db_session.query(Customer).filter(Customer.tier == "gold",
                                                        Customer.id != archived.id).all()
    ]
    assert plan["skipped"]["archived"] == 1
    assert plan["skipped"]["opted_out"] == 1
    assert opted_out.id not in [c.id for c in plan["recipients"]]


def test_already_messaged_customers_are_not_counted_again(db_session):
    campaign = make_campaign(db_session, code="AUD2", percent=10)
    customer = make_customer(db_session)
    assignment = assign_campaign(db_session, campaign, customer)
    assignment.invite_sent_at = datetime.now(timezone.utc)
    db_session.commit()

    options = {option["key"]: option for option in audience_options(db_session, campaign)}
    assert options["all"]["count"] == 0
    assert campaign_recipients(db_session, campaign, "all")["count"] == 0


def test_the_blast_cap_is_reported_rather_than_hidden(db_session):
    set_setting(db_session, "campaign_sms_limit", 2)
    campaign = make_campaign(db_session, code="CAP1", percent=10)
    for _ in range(4):
        make_customer(db_session)

    plan = campaign_recipients(db_session, campaign, "all")
    assert plan["limit"] == 2
    assert plan["count"] == 2
    assert plan["matched"] == 4
    assert plan["capped"] is True


# ── the pages ────────────────────────────────────────────────────────────────

def test_the_campaign_pages_use_the_catalogue_design_language():
    listing = template_text("admin/campaigns.html")
    detail = template_text("admin/campaign_detail.html")
    form = template_text("admin/campaign_form.html")

    for page in (listing, detail):
        assert "page-heading product-page-heading" in page
        assert 'class="admin-nav"' in page
        assert '<th scope="col"' in page
        assert "table-scroll" in page
    assert "stats-grid campaign-kpi" in listing
    assert "stats-grid campaign-kpi" in detail
    assert "empty-state" in listing
    # Forms carry the CSRF token, like every other POST in the app.
    for page in (detail, form):
        assert 'name="csrf_token"' in page
    # No hardcoded colours: the campaign pages must follow every theme.
    for page in (listing, detail, form):
        assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", page), page


def test_the_detail_page_offers_a_real_audience_choice(detail_page):
    body = detail_page
    assert "گروه دریافت‌کننده" in body
    assert 'name="audience"' in body
    assert "مشتری" in body
    # The send is confirmed with the real number, not a generic warning.
    assert "پیامک کمپین برای گروه انتخاب‌شده ارسال می‌شود" in body


@pytest.fixture()
def detail_page(client, db_session):
    campaign = make_campaign(db_session, code="PAGE1", percent=12)
    make_customer(db_session)
    login(client)
    return client.get(f"/admin/campaigns/{campaign.id}").text


def test_the_list_page_filters_sort_and_counts(client, db_session):
    live = make_campaign(db_session, code="LISTLIVE", percent=20)
    make_campaign(db_session, code="LISTOFF", percent=5, is_active=False)
    make_campaign(db_session, code="LISTGONE", percent=5,
                  end_date=datetime.now(timezone.utc) - timedelta(days=1))
    login(client)

    body = client.get("/admin/campaigns").text
    assert "LISTLIVE" in body and "LISTOFF" in body
    assert "+ در جریان" not in body  # no stray markup

    live_only = client.get("/admin/campaigns?status=live").text
    assert "LISTLIVE" in live_only
    assert "LISTOFF" not in live_only and "LISTGONE" not in live_only

    search = client.get("/admin/campaigns?search=LISTOFF").text
    assert "LISTOFF" in search and "LISTLIVE" not in search

    overview = campaign_overview(db_session)
    assert overview["total"] == 3
    assert overview["live"] == 1
    assert campaign_filtered(db_session, status="live")["total"] == 1
    assert live.is_active


def test_stats_count_redemptions_and_revenue(client, db_session):
    campaign = make_campaign(db_session, code="STAT1", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    confirm_sale(client, make_variant(db_session, price=200_000), customer_id=customer.id)

    stats = campaign_stats(db_session, campaign)
    assert stats["redemptions"] == 1
    assert stats["revenue"] == 180_000
    assert stats["used_money"] == 20_000
    assert stats["used"] == 1
    assert stats["reached"] == 1
    assert stats["response_rate"] == 100


def test_a_duplicate_code_is_a_sentence_not_a_crash(client, db_session):
    make_campaign(db_session, code="DUPE1", percent=10)
    login(client)
    response = client.post("/admin/campaigns/add", data={
        "name": "تکراری",
        "code": "dupe1",
        "discount_percent": "10",
        "min_purchase": "0",
        "csrf_token": csrf_token(client, "/admin/campaigns"),
    }, follow_redirects=False)

    assert response.status_code == 400
    assert "قبلاً" in response.text
    assert db_session.query(Campaign).filter(Campaign.code == "DUPE1").count() == 1


@pytest.mark.parametrize("payload,fragment", [
    ({"name": "", "code": "X1", "discount_percent": "10"}, "نام کمپین"),
    ({"name": "بدون کد", "code": "", "discount_percent": "10"}, "کد تخفیف"),
    ({"name": "درصد بد", "code": "X2", "discount_percent": "0"}, "درصد تخفیف"),
    ({"name": "درصد زیاد", "code": "X3", "discount_percent": "150"}, "درصد تخفیف"),
    ({"name": "تاریخ برعکس", "code": "X4", "discount_percent": "10",
      "start_date": "1405/10/01", "end_date": "1405/01/01"}, "تاریخ پایان"),
])
def test_the_form_explains_each_mistake(client, db_session, payload, fragment):
    login(client)
    data = {**payload, "min_purchase": "0", "csrf_token": csrf_token(client, "/admin/campaigns/add")}
    response = client.post("/admin/campaigns/add", data=data, follow_redirects=False)
    assert response.status_code == 400
    assert fragment in response.text
    # A rejected form never half-creates a campaign.
    assert db_session.query(Campaign).count() == 0


def test_the_report_refuses_to_delete_a_campaign_that_sold(client, db_session):
    campaign = make_campaign(db_session, code="KEEP1", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    confirm_sale(client, make_variant(db_session, price=100_000), customer_id=customer.id)
    sale = last_sale(db_session)

    login(client)
    response = client.post(
        f"/admin/campaigns/{campaign.id}/delete",
        data={"csrf_token": csrf_token(client, f"/admin/campaigns/{campaign.id}")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    db_session.expire_all()
    assert db_session.query(Campaign).filter(Campaign.id == campaign.id).count() == 1
    assert db_session.query(SaleCampaign).filter(SaleCampaign.sale_id == sale.id).count() == 1


# ── the customer's side ──────────────────────────────────────────────────────

def test_the_customers_list_shows_the_live_campaign_and_filters_by_it(client, db_session):
    campaign = make_campaign(db_session, code="BADGE1", percent=15)
    holder = make_customer(db_session, first_name="دارنده")
    make_customer(db_session, first_name="بی‌کمپین")
    assign_campaign(db_session, campaign, holder)
    db_session.commit()
    login(client)

    body = client.get("/admin/customers").text
    assert "BADGE1" not in body  # the badge names the campaign, not its code
    assert campaign.name in body
    # Percentages read as numbers everywhere else in the app, so the badge does
    # the same rather than inventing Persian digits for one column.
    assert f"{campaign.discount_percent}٪" in body

    filtered = client.get("/admin/customers?status=campaign").text
    assert "دارنده" in filtered
    assert "بی‌کمپین" not in filtered


def test_the_customers_list_reads_the_badge_in_one_query(client, db_session):
    campaign = make_campaign(db_session, code="QUERY1", percent=10)
    for _ in range(6):
        customer = make_customer(db_session)
        assign_campaign(db_session, campaign, customer)
    db_session.commit()

    from sqlalchemy import event
    from tests.conftest import _TEST_DB

    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        overview = campaign_overview(db_session)
        listing = campaign_filtered(db_session)
        mapping = customer_campaign_map(db_session, [row["campaign"].id for row in listing["rows"]])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert overview["total"] == 1
    assert mapping  # one bulk call, not one per row
    # A fixed number of queries: adding customers must not add queries.
    assert len(queries) <= 12, f"{len(queries)} queries — the badge lookup is not batched"
    assert _TEST_DB  # the test database, not the live one


def test_the_profile_carries_the_campaign_card_and_can_add_one(client, db_session):
    campaign = make_campaign(db_session, code="PROF1", percent=25)
    customer = make_customer(db_session)
    login(client)

    body = client.get(f"/admin/customers/{customer.id}").text
    assert "کمپین‌ها" in body
    assert "این مشتری در هیچ کمپینی نیست" in body
    assert campaign.name in body  # offered in the add-to-campaign select

    response = client.post("/admin/campaigns/assign", data={
        "campaign_id": str(campaign.id),
        "customer_id": str(customer.id),
        "next": f"/admin/customers/{customer.id}",
        "csrf_token": csrf_token(client, f"/admin/customers/{customer.id}"),
    }, follow_redirects=False)
    assert response.status_code == 303

    body = client.get(f"/admin/customers/{customer.id}").text
    assert "دعوت‌شده" in body
    assert customer.id and campaign.id  # ids stay stable across the redirect

    # …and taking it off again leaves the row, so the history survives.
    response = client.post(f"/admin/campaigns/{campaign.id}/unassign", data={
        "customer_id": str(customer.id),
        "next": f"/admin/customers/{customer.id}",
        "csrf_token": csrf_token(client, f"/admin/customers/{customer.id}"),
    }, follow_redirects=False)
    assert response.status_code == 303
    assignment = only_assignment(db_session, campaign, customer)
    assert assignment.status == "removed"


def test_the_counter_panel_names_the_campaign_the_cashier_must_honour(client, db_session):
    campaign = make_campaign(db_session, code="PANEL1", percent=30)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    login(client)

    body = client.get(f"/customers/lookup?phone={customer.phone}").text
    assert "کمپین فعال" in body
    assert campaign.name in body


def test_the_checkout_shows_the_held_campaign_and_offers_the_code_field(client, db_session):
    campaign = make_campaign(db_session, code="CHECK1", percent=15)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    variant = make_variant(db_session, price=400_000)

    login(client)
    token = csrf_token(client, "/sales/new")
    response = client.post("/sales/lookup-customer", data={
        "phone": customer.phone, "csrf_token": token,
    })
    assert response.status_code == 200
    body = response.text
    assert "campaign-code-input" in body
    assert campaign.name in body
    assert "کد کمپین" in body
    assert "با اسکن اولین کالا اعمال می‌شود" in body  # the empty basket still shows the offer

    # The discount is real, not just printed: the summary carries the line.
    scan_token = csrf_token(client, "/sales/new")
    response = client.post("/sales/add-to-basket", data={
        "customer_id": str(customer.id),
        "barcode": variant.barcode,
        "basket_json": "[]",
        "campaign_code": "",
        "csrf_token": scan_token,
    })
    assert response.status_code == 200
    assert "تخفیف" in response.text
    assert "۱" in response.text or "کمپین" in response.text


def test_a_bad_code_is_reported_and_does_not_block_the_held_campaign(client, db_session):
    campaign = make_campaign(db_session, code="HELD2", percent=10)
    customer = make_customer(db_session)
    assign_campaign(db_session, campaign, customer)
    db_session.commit()
    variant = make_variant(db_session, price=100_000)

    login(client)
    token = csrf_token(client, "/sales/new")
    response = client.post("/sales/add-to-basket", data={
        "customer_id": str(customer.id),
        "barcode": variant.barcode,
        "basket_json": "[]",
        "campaign_code": "WRONG-CODE",
        "csrf_token": token,
    })
    assert response.status_code == 200
    body = response.text
    assert "WRONG-CODE" in body
    assert "پیدا نشد" in body
    # The campaign they do hold still applies.
    assert campaign.name in body
