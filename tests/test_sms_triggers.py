"""Automatic SMS: templates the owner pointed at a moment of their own.

The whole risk of this feature is that a message leaves without anyone watching,
so these tests are mostly about what must *not* happen: nothing fires before the
owner opts in, nothing fires twice for the same event, nothing reaches somebody
the «ارسال پیامک» page would have skipped, and a broken template can never
disturb the sale it hangs off.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from models import Customer, Product, ProductVariant, Sale, Settings, SmsMessage, SmsTemplate
from services.sms_templates import (
    create_custom,
    fire_summary,
    get_template,
    send_info,
    template_variables,
    trigger_from_form,
)
from services.sms_triggers import (
    SETTING_AUTO_SEND_LIMIT,
    fire_follow_up_sms,
    fire_purchase_sms,
    follow_up_candidates,
)
from tests.conftest import csrf_token


# ── helpers ──────────────────────────────────────────────────────────────────

def make_customer(db, *, days_since_purchase=None, **kwargs) -> Customer:
    customer = Customer(
        phone=kwargs.pop("phone", "09120000009"),
        first_name=kwargs.pop("first_name", "سارا"),
        last_name=kwargs.pop("last_name", "رضایی"),
        referral_code=kwargs.pop("referral_code", "TRG001"),
        **kwargs,
    )
    if days_since_purchase is not None:
        customer.last_purchase_date = datetime.now(timezone.utc) - timedelta(days=days_since_purchase)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_purchase_template(db, *, body="پس از خرید %var1%، ممنون!", **kwargs) -> SmsTemplate:
    kwargs.setdefault("trigger_key", "purchase")
    template = create_custom(db, name=kwargs.pop("name", "تشکر از خرید"), body=body, **kwargs)
    db.commit()
    return template


def make_follow_up_template(db, *, days=30, body="%var1% عزیز، دلمان برایتان تنگ شده",
                            **kwargs) -> SmsTemplate:
    kwargs.setdefault("trigger_key", "follow_up")
    kwargs.setdefault("trigger_days", days)
    template = create_custom(db, name=kwargs.pop("name", "یادت نرود"), body=body, **kwargs)
    db.commit()
    return template


def a_sale(db, *, customer=None) -> Sale:
    sale = Sale(total_amount=500_000, final_amount=500_000, payment_method="card",
                payment_confirmed=True, customer_id=customer.id if customer else None)
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return sale


def queued_texts(db) -> list[str]:
    db.expire_all()
    return [row.body for row in db.query(SmsMessage).order_by(SmsMessage.id.asc()).all()]


# ── nothing fires without a deliberate opt-in ────────────────────────────────

def test_a_new_template_is_hand_sent_until_someone_says_otherwise(db_session):
    """The default has to be «دیست»: an upgrade can never start messaging a
    shop's customers because a feature appeared."""
    template = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، عید مبارک")
    db_session.commit()

    assert template.trigger_key == ""
    info = send_info(template)
    assert info["mode"] == "manual"
    assert info["url"] == "/admin/sms/send"

    customer = make_customer(db_session, days_since_purchase=90)
    asyncio.run(fire_purchase_sms(db_session, sale=a_sale(db_session, customer=customer),
                                  customer=customer))
    asyncio.run(fire_follow_up_sms(db_session))
    assert queued_texts(db_session) == []


def test_an_unknown_trigger_falls_back_to_hand_sent():
    """A form is not a trusted source of behaviour; a typo must not invent a send."""
    assert trigger_from_form("", None) == ("", 0)
    assert trigger_from_form("hourly", 5) == ("", 0)
    assert trigger_from_form("purchase", 30) == ("purchase", 0)


def test_a_row_that_reached_a_trigger_with_a_hole_does_not_fire(db_session):
    """The editor refuses this combination, but a row could still arrive some
    other way (an import, a script). The last gate is the engine itself."""
    template = create_custom(db_session, name="یادت نرود", body="%var1% عزیز، %var2%",
                             trigger_key="follow_up", trigger_days=30)
    db_session.commit()
    make_customer(db_session, days_since_purchase=40, total_spent=1_250_000)

    assert asyncio.run(fire_follow_up_sms(db_session))["sent"] == 0
    assert queued_texts(db_session) == []

    # Once the second slot reads something real, the very same row fires.
    template.variables = json.dumps([
        {"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"},
        {"token": "var2", "label": "مجموع خرید", "sample": "1,250,000", "field": "total_spent"},
    ], ensure_ascii=False)
    db_session.commit()

    assert asyncio.run(fire_follow_up_sms(db_session))["sent"] == 1
    assert queued_texts(db_session) == ["سارا عزیز، 1,250,000"]


def test_a_triggered_template_without_text_never_sends(db_session):
    template = make_purchase_template(db_session, body="   ")
    customer = make_customer(db_session)
    sale = a_sale(db_session, customer=customer)

    asyncio.run(fire_purchase_sms(db_session, sale=sale, customer=customer))

    assert queued_texts(db_session) == []
    assert template.trigger_key == "purchase"          # opted in, still silent


def test_switching_the_template_off_stops_the_automatic_send(db_session):
    """«فعال/غیرفعال» is the same switch everywhere, including for triggers."""
    template = make_purchase_template(db_session)
    template.is_active = False
    db_session.commit()
    customer = make_customer(db_session)

    asyncio.run(fire_purchase_sms(db_session, sale=a_sale(db_session, customer=customer),
                                  customer=customer))

    assert queued_texts(db_session) == []


# ── the purchase trigger ─────────────────────────────────────────────────────

def test_a_purchase_fires_once_per_sale_for_that_customer(db_session):
    make_purchase_template(db_session, body="%var1% جان، ممنون از خریدت")
    customer = make_customer(db_session, first_name="سارا")
    sale = a_sale(db_session, customer=customer)

    asyncio.run(fire_purchase_sms(db_session, sale=sale, customer=customer))
    # The same sale cannot be thanked twice, however often the hook is reached.
    asyncio.run(fire_purchase_sms(db_session, sale=sale, customer=customer))

    assert queued_texts(db_session) == ["سارا جان، ممنون از خریدت"]
    row = db_session.query(SmsMessage).one()
    assert row.source == "purchase"
    assert row.ref == f"sale:{sale.id}"
    assert row.kind == "transactional"

    # The *next* sale is a new event, so it may be thanked again.
    second = a_sale(db_session, customer=customer)
    asyncio.run(fire_purchase_sms(db_session, sale=second, customer=customer))
    assert len(queued_texts(db_session)) == 2


def test_a_purchase_sms_goes_even_to_somebody_who_opted_out(db_session):
    """It is about their own order, not marketing — the same rule the نسیه
    reminder follows."""
    make_purchase_template(db_session)
    customer = make_customer(db_session, sms_opt_in=False)

    asyncio.run(fire_purchase_sms(db_session, sale=a_sale(db_session, customer=customer),
                                  customer=customer))

    assert queued_texts(db_session) == ["پس از خرید سارا، ممنون!"]


def test_an_archived_or_blocked_customer_is_left_alone(db_session):
    make_purchase_template(db_session)
    archived = make_customer(db_session, phone="09120000011", referral_code="TRG011",
                            is_archived=True)
    blocked = make_customer(db_session, phone="09120000012", referral_code="TRG012",
                            tags="blocked")

    for customer in (archived, blocked):
        asyncio.run(fire_purchase_sms(db_session, sale=a_sale(db_session, customer=customer),
                                      customer=customer))

    assert queued_texts(db_session) == []


def test_a_guest_sale_messages_nobody(db_session):
    make_purchase_template(db_session)
    sale = a_sale(db_session)                       # no customer on the sale

    asyncio.run(fire_purchase_sms(db_session, sale=sale, customer=None))

    assert queued_texts(db_session) == []


def test_a_broken_template_cannot_disturb_the_sale(db_session, monkeypatch):
    """The trigger runs after the sale commits, and swallows its own errors."""
    make_purchase_template(db_session)
    customer = make_customer(db_session)
    sale = a_sale(db_session, customer=customer)

    import services.sms_triggers as triggers

    def explode(*args, **kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(triggers, "render_template", explode)
    result = asyncio.run(fire_purchase_sms(db_session, sale=sale, customer=customer))

    assert result == []
    assert db_session.query(Sale).count() == 1      # the sale is untouched


# ── the follow-up sweep ──────────────────────────────────────────────────────

def test_a_follow_up_goes_to_the_lapsed_and_only_once(db_session):
    make_follow_up_template(db_session, days=30)
    make_customer(db_session, days_since_purchase=40)

    first = asyncio.run(fire_follow_up_sms(db_session))
    second = asyncio.run(fire_follow_up_sms(db_session))

    assert first["sent"] == 1
    assert second["sent"] == 0                      # the sweep repeats safely
    assert queued_texts(db_session) == ["سارا عزیز، دلمان برایتان تنگ شده"]
    row = db_session.query(SmsMessage).one()
    assert row.source == "follow_up"
    assert row.kind == "marketing"
    assert row.ref.startswith("purchase:")


def test_a_fresh_customer_is_not_nagged(db_session):
    make_follow_up_template(db_session, days=30)
    make_customer(db_session, days_since_purchase=10)

    assert asyncio.run(fire_follow_up_sms(db_session))["sent"] == 0
    assert queued_texts(db_session) == []


def test_somebody_who_never_bought_is_not_a_follow_up(db_session):
    """«پیگیری پس از آخرین خرید» is about a customer the shop already has; a
    visitor who never bought is not late for anything."""
    template = make_follow_up_template(db_session, days=30)
    make_customer(db_session, days_since_purchase=None)

    plan = follow_up_candidates(db_session, template=template)

    assert plan["due"] == []
    assert plan["skipped"]["no_purchase"] == 1


def test_buying_again_re_arms_the_follow_up(db_session):
    make_follow_up_template(db_session, days=30)
    customer = make_customer(db_session, days_since_purchase=40)
    asyncio.run(fire_follow_up_sms(db_session))
    assert len(queued_texts(db_session)) == 1

    # They came back, so the clock starts over — and thirty days later the shop
    # may say hello again about this *new* purchase.
    customer.last_purchase_date = datetime.now(timezone.utc) - timedelta(days=31)
    db_session.commit()

    assert asyncio.run(fire_follow_up_sms(db_session))["sent"] == 1
    assert len(queued_texts(db_session)) == 2


def test_a_follow_up_respects_consent_and_the_block_list(db_session):
    make_follow_up_template(db_session, days=30)
    make_customer(db_session, phone="09120000021", referral_code="TRG021",
                  days_since_purchase=40, sms_opt_in=False)

    assert asyncio.run(fire_follow_up_sms(db_session))["sent"] == 0
    assert queued_texts(db_session) == []


def test_the_sweep_is_throttled_and_nobody_is_dropped(db_session):
    make_follow_up_template(db_session, days=30)
    for index in range(3):
        make_customer(db_session, phone=f"0912000003{index}", referral_code=f"TRG03{index}",
                      days_since_purchase=40, first_name=f"مشتری{index}")
    db_session.add(Settings(key=SETTING_AUTO_SEND_LIMIT, value="2"))
    db_session.commit()

    first = asyncio.run(fire_follow_up_sms(db_session))
    second = asyncio.run(fire_follow_up_sms(db_session))

    # Two now, the third on the next pass — a throttle, not a truncation.
    assert first["sent"] == 2
    assert second["sent"] == 1
    assert len(queued_texts(db_session)) == 3


# ── the pages say the same thing ─────────────────────────────────────────────

def test_the_manager_page_calls_a_triggered_template_automatic(client, authed, db_session):
    template = make_follow_up_template(db_session, days=45)

    page = authed.get("/admin/sms").text

    assert "sms-fire-badge is-auto" in page
    assert "45" in page
    summary = fire_summary(db_session)
    assert template.id in [row.id for row in summary["auto"]]
    assert send_info(template)["mode"] == "auto"


def test_the_editor_offers_the_triggers_and_saves_the_choice(client, authed, db_session):
    template = create_custom(db_session, name="یادت نرود", body="%var1% عزیز")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{template.id}/edit")

    editor = authed.get(f"/admin/sms/templates/{template.id}/edit").text
    assert 'name="trigger_key"' in editor
    assert "پس از هر خرید" in editor and "پیگیری پس از چند روز" in editor
    # It starts hand-sent, so the switch is visibly off rather than implied.
    assert "فقط دستی" in editor

    saved = authed.post(f"/admin/sms/templates/{template.id}", data={
        "csrf_token": token, "name": "یادت نرود", "body": "%var1% عزیز",
        "source_var1": "first_name", "trigger_key": "follow_up", "trigger_days": "45",
    }, follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()

    stored = db_session.query(SmsTemplate).filter(SmsTemplate.id == template.id).one()
    assert (stored.trigger_key, stored.trigger_days) == ("follow_up", 45)
    assert "45" in send_info(stored)["text"]


def test_an_automatic_template_may_not_carry_a_hole(client, authed, db_session):
    """Nobody reads an automatic message before it leaves, so a slot nothing
    fills is refused at the door instead of arriving as « عزیز، »."""
    template = create_custom(db_session, name="یادت نرود", body="%var1% عزیز، %var2%")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{template.id}/edit")

    refused = authed.post(f"/admin/sms/templates/{template.id}", data={
        "csrf_token": token, "name": "یادت نرود", "body": "%var1% عزیز، %var2%",
        "source_var1": "first_name", "trigger_key": "follow_up", "trigger_days": "30",
    })
    db_session.expire_all()

    assert refused.status_code == 200                    # the form came back
    assert "به هیچ مقداری وصل نیست" in refused.text
    stored = db_session.query(SmsTemplate).filter(SmsTemplate.id == template.id).one()
    assert stored.trigger_key == ""                      # nothing was switched on

    # Binding the second slot (or dropping it) is what unlocks the trigger.
    accepted = authed.post(f"/admin/sms/templates/{template.id}", data={
        "csrf_token": token, "name": "یادت نرود", "body": "%var1% عزیز، %var2%",
        "source_var1": "first_name", "source_var2": "total_spent",
        "trigger_key": "follow_up", "trigger_days": "30",
    }, follow_redirects=False)
    db_session.expire_all()

    assert accepted.status_code == 303
    assert db_session.query(SmsTemplate).filter(
        SmsTemplate.id == template.id,
    ).one().trigger_key == "follow_up"


def test_a_hand_sent_template_may_keep_that_hole(client, authed, db_session):
    """The refusal is about *automatic* only: hand-sent keeps the warning that
    already exists, because there the owner sees the preview."""
    template = create_custom(db_session, name="تبریک عید", body="%var1% عزیز، %var2%")
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{template.id}/edit")

    saved = authed.post(f"/admin/sms/templates/{template.id}", data={
        "csrf_token": token, "name": "تبریک عید", "body": "%var1% عزیز، %var2%",
        "source_var1": "first_name", "trigger_key": "",
    }, follow_redirects=False)

    assert saved.status_code == 303
    assert db_session.query(SmsTemplate).filter(
        SmsTemplate.id == template.id,
    ).one().trigger_key == ""


def test_a_builtin_cannot_be_given_a_trigger(client, authed, db_session):
    """The built-ins have their own senders; a posted trigger must be ignored
    rather than quietly adding a second one."""
    from services.sms_templates import ensure_seeded

    ensure_seeded(db_session)
    welcome = get_template(db_session, "welcome")
    welcome.body, welcome.is_active = "خوش آمدی %var1%", True
    db_session.commit()
    token = csrf_token(client, f"/admin/sms/templates/{welcome.id}/edit")

    authed.post(f"/admin/sms/templates/{welcome.id}", data={
        "csrf_token": token, "name": welcome.name, "body": welcome.body,
        "is_active": "on", "trigger_key": "purchase",
    }, follow_redirects=False)
    db_session.expire_all()

    assert get_template(db_session, "welcome").trigger_key == ""
    assert send_info(get_template(db_session, "welcome"))["mode"] == "auto"   # its own sender


def test_duplicating_a_triggered_template_starts_hand_sent(client, authed, db_session):
    """Copying a live trigger would silently double every message."""
    template = make_purchase_template(db_session)
    token = csrf_token(client, "/admin/sms")

    authed.post(f"/admin/sms/templates/{template.id}/duplicate",
                data={"csrf_token": token}, follow_redirects=False)
    db_session.expire_all()

    copy = db_session.query(SmsTemplate).filter(
        SmsTemplate.category == "custom", SmsTemplate.id != template.id,
    ).one()
    assert copy.trigger_key == ""
    assert send_info(copy)["mode"] == "manual"
    # …but its text and its slot bindings survive the copy.
    assert copy.body == template.body
    assert template_variables(copy)[0]["field"] == "first_name"


# ── the sale path itself ─────────────────────────────────────────────────────

def test_completing_a_sale_in_the_app_fires_the_thanks_sms(client, authed, db_session):
    """End to end through the real checkout route: the hook is wired, not just
    the function it calls."""
    make_purchase_template(db_session, body="%var1% جان، ممنون از خریدت")
    customer = make_customer(db_session, phone="09120000099", referral_code="TRG099",
                             first_name="سارا")
    product = Product(name="تیشرت")
    db_session.add(product)
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=100_000, cost_price=50_000,
                             stock_quantity=3, barcode="TRG-BC1")
    db_session.add(variant)
    db_session.commit()
    basket = [{"variant_id": variant.id, "product_id": product.id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]

    response = authed.post("/sales/confirm-sale", data={
        "customer_id": str(customer.id),
        "basket_json": json.dumps(basket, ensure_ascii=False),
        "payment_method": "cash",
        "referrer_code": "", "referrer_phone": "",
        "use_referrer_discount": "0",
        "custom_discount_amount": "", "custom_discount_percent": "",
        "csrf_token": csrf_token(authed, "/sales/new"),
    })

    assert response.status_code == 200
    db_session.expire_all()
    sale = db_session.query(Sale).one()
    rows = db_session.query(SmsMessage).all()
    assert [row.body for row in rows] == ["سارا جان، ممنون از خریدت"]
    assert rows[0].ref == f"sale:{sale.id}"
    assert rows[0].customer_id == customer.id
