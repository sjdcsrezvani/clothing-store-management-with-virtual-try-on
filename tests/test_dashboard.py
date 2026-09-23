"""The dashboard, and the two things it must never do.

The old page was a manager's page carrying owner-only buttons, so a manager who
pressed one got FastAPI's raw ``{"detail": ...}`` instead of a page, and it
counted its own rows, so its numbers disagreed with the pages it linked to. The
tests below are mostly about those two failures: that every role is handed only
what it may act on, and that a headline and the page behind it are one number.
"""
import re

import pytest

from models import Customer, POSTransaction, Product, ProductVariant, Settings
from services.tier import tier_up_marker_key
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff

_customer_seq = iter(range(10_000))

# The shell's own `<main>` is read as a pattern, not as a literal tag: this cut
# must not break every time the shell learns an attribute — it just gained `id`
# and `tabindex` for the skip link — and a test that stops finding the tag would
# fail for the wrong reason.
MAIN_OPEN = re.compile(r'<main class="app-main"[^>]*>')

ROOT_FILES = {
    "dashboard": "templates/admin/dashboard.html",
    "service": "services/dashboard.py",
    "products": "templates/admin/products.html",
    "reconciliation": "templates/admin/pos_reconciliation.html",
}


def _read(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / ROOT_FILES[name]).read_text()


def content_only(html: str) -> str:
    """The page's own content, without the shell around it.

    The sidebar still shows every link to every role; that is a known, separate
    gap and fixing it is not what this page is about. Cutting at ``<main>`` keeps
    this test honest about what it does and does not cover, rather than letting
    it pass because it never looked at the links the page actually draws.
    """
    return html[MAIN_OPEN.search(html).end():]


def attention_counts(html: str) -> dict:
    """«کارت label» → the number printed on it, exactly as the page draws it."""
    return {
        match.group(2): match.group(1).strip()
        for match in re.finditer(
            r'<span class="action-count tnum">([^<]*)</span>\s*<strong>([^<]*)</strong>', html
        )
    }


def stat_values(html: str) -> dict:
    """«stat label» → its value, for the cards that are not in the alert row.

    Values are stripped: a card carrying a delta has the badge on the next line,
    inside the same element. Two cards can share a label — today's «فروش» and the
    month's — so this keeps the last, and `stat_labels` is the honest way to ask
    how many there are.
    """
    return {
        match.group(1).strip(): match.group(2).strip()
        for match in re.finditer(
            r'<div class="stat-label">([^<]*)</div>\s*<div class="stat-value[^"]*">([^<]*)</div>',
            html,
        )
    }


def stat_labels(html: str) -> list:
    return [match.strip()
            for match in re.findall(r'<div class="stat-label">([^<]*)</div>', html)]


def internal_links(html: str) -> set:
    return {match.group(1) for match in re.finditer(r'href="(/admin[^"#]*)"', html)}


def _product(db, stock_levels, name="کالای داشبورد"):
    product = Product(name=name)
    db.add(product)
    db.flush()
    for index, stock in enumerate(stock_levels):
        db.add(ProductVariant(
            product_id=product.id, price=100_000, cost_price=50_000,
            stock_quantity=stock, barcode=f"DSH-{product.id}-{index}",
        ))
    db.commit()
    return product


# ── who sees what ────────────────────────────────────────────────────────────

# Labels only an owner may be handed. The sections carry the period now, so
# «امروز ← فروش» is one card labelled «فروش» rather than a label repeating the
# span the heading already names.
OWNER_ONLY = (
    "سود ناخالص", "سود خالص", "هزینه‌ها",
    "ارزش موجودی انبار", "کارهای دوره‌ای", "پیامک تولد", "پیگیری مشتریان",
    "ارتقای سطح", "کاهش سطح", "پشتیبان‌گیری",
)


def test_owner_sees_the_numbers_and_the_work_that_are_theirs(client, db_session):
    owner, password = _staff(db_session, "dash-owner", "owner")
    _session_as(client, owner, password)
    html = content_only(client.get("/admin").text)

    for label in OWNER_ONLY:
        assert label in html, f"owner is missing {label}"
    assert "نیازمند توجه" in html
    assert "باشگاه مشتریان" in html
    assert "پرفروش‌ترین‌های این ماه" in html
    labels = stat_values(html)
    assert "سود ناخالص" in labels
    assert "سود خالص" in labels
    assert "کاهش سطح" in labels
    # There is no database-wipe action anywhere in the shell: backups are the
    # safe path, and no page carries a reset button for any role.
    assert "منطقه خطر" not in html
    assert "منطقه خطر" not in client.get("/admin/settings").text


def test_manager_is_never_shown_an_owner_number_or_button(client, db_session):
    manager, password = _staff(db_session, "dash-manager", "manager")
    _session_as(client, manager, password)
    html = content_only(client.get("/admin").text)

    for label in OWNER_ONLY:
        assert label not in html, f"manager was shown {label}"
    assert "منطقه خطر" not in html
    # …and the takings, which a manager does run the shop on, are all still here.
    # «فروش» appears twice because today's and the month's are separate cards.
    labels = stat_values(html)
    for label in ("صندوق", "نسیه در گردش", "اعضای باشگاه", "مشتریان فعال",
                  "مشتریان بدهکار"):
        assert label in labels, f"manager is missing {label}"
    # The periods are named by their sections, so «فروش» appears once under
    # «امروز» and once under «این ماه» rather than in three separate labels.
    assert stat_labels(html).count("فروش") == 2
    assert "امروز" in html and "این ماه" in html


@pytest.mark.parametrize("role", ["manager", "owner"])
def test_every_destination_the_page_draws_can_be_opened_by_whoever_it_drew_it_for(
    client, db_session, role
):
    """The defect this replaces: a button that answered with JSON.

    Every link the page puts in front of a role is fetched as that role, so a
    card pointing at a route the viewer may not open fails here rather than in
    the owner's face.
    """
    user, password = _staff(db_session, f"dash-links-{role}", role)
    _session_as(client, user, password)
    links = internal_links(content_only(client.get("/admin").text))
    assert links, "the page drew no destinations at all"

    for link in sorted(links):
        response = client.get(link, follow_redirects=False)
        assert response.status_code < 400, f"{role} cannot open {link} ({response.status_code})"
        assert response.headers.get("content-type", "").startswith("text/html"), \
            f"{link} answered with a non-HTML body for {role}"


# ── a number and the page behind it are one number ───────────────────────────

def test_the_low_stock_alert_and_the_products_page_quote_the_same_shelf(
    client, db_session
):
    owner, password = _staff(db_session, "dash-stock", "owner")
    _session_as(client, owner, password)
    # Two variants at zero and one at the threshold: all three count as low, two
    # of them as gone — the exact distinction the two numbers exist to make.
    _product(db_session, [0, 0, 2, 40])

    dashboard = attention_counts(content_only(client.get("/admin").text))
    assert dashboard["تنوع کم‌موجود"] == "3"
    assert "2 تنوع کاملاً تمام شده" in client.get("/admin").text

    products = stat_values(client.get("/admin/products").text)
    assert products["تنوع کم‌موجودی"] == dashboard["تنوع کم‌موجود"]
    assert products["تنوع ناموجود"] == "2"


def test_the_reconciliation_alert_counts_everything_not_just_the_readable_page(
    client, db_session
):
    """A count is not a window: 205 outstanding must not read as 200."""
    owner, password = _staff(db_session, "dash-pos", "owner")
    _session_as(client, owner, password)
    db_session.add_all([
        POSTransaction(checkout_nonce=f"dash-pos-{index}", amount=10_000,
                       host="127.0.0.1", port=8500, status="sent")
        for index in range(205)
    ] + [
        # A settled attempt and a declined one: neither is outstanding, so the
        # count has to leave both alone.
        POSTransaction(checkout_nonce="dash-pos-linked", amount=10_000,
                       host="127.0.0.1", port=8500, status="linked_to_sale", sale_id=None),
        POSTransaction(checkout_nonce="dash-pos-declined", amount=10_000,
                       host="127.0.0.1", port=8500, status="declined"),
    ])
    db_session.commit()

    from services.pos_reconciliation import unresolved_transactions
    assert unresolved_transactions(db_session) == 205

    html = client.get("/admin").text
    assert attention_counts(content_only(html))["تراکنش کارتخوان"] == "205"

    page = client.get("/admin/pos-reconciliation").text
    assert stat_values(page)["⚠️ نیازمند بررسی"] == "205"
    # The page lists fewer than it counts, and has to say so.
    assert "تنها 200 رکورد اخیر" in page


def test_the_follow_up_review_page_and_its_dashboard_card_agree(client, db_session):
    """The card is a count-only twin of the page, not a second opinion.

    The page renders every message it lists; the card renders none. If they ever
    disagree, one of them is lying about who is waiting.
    """
    from datetime import datetime, timedelta, timezone

    from models import Customer, SmsTemplate

    owner, password = _staff(db_session, "dash-follow", "owner")
    _session_as(client, owner, password)

    db_session.add(SmsTemplate(
        key="dash-follow-up", name="پیگیری خرید", category="custom",
        body="دلمان برایتان تنگ شده؛ منتظرتان هستیم.", variables="[]",
        trigger_key="follow_up", trigger_days=30, is_active=True,
    ))
    long_ago = datetime.now(timezone.utc) - timedelta(days=45)
    db_session.add_all([
        Customer(first_name=f"مشتری {index}", phone=f"0914000{index:04d}",
                 referral_code=f"FU{index}", last_purchase_date=long_ago)
        for index in range(3)
    ])
    # Bought yesterday: not late, so neither number may include them.
    db_session.add(Customer(first_name="تازه", phone="09140009999",
                            referral_code="FUNEW",
                            last_purchase_date=datetime.now(timezone.utc) - timedelta(days=1)))
    db_session.commit()

    from services._common import _to_persian_digits as persian

    card = stat_values(content_only(client.get("/admin").text))["پیگیری مشتریان"]
    page = client.get("/admin/follow-ups").text
    assert card == "3"
    # Same number, each in its own page's convention: the dashboard follows the
    # customers/analytics pages in latin figures for figures, while the review
    # page spells its counts in Persian digits.
    assert f"{persian(card)} مشتری در ۱ قالب پیگیری" in page
    assert page.count('class="sms-name"') == 3


def test_the_top_sellers_list_hides_its_profit_figure_from_a_manager(client, db_session):
    """The figure is chosen where the row is built, so no template edit can add it."""
    from datetime import datetime, timezone

    from models import Sale, SaleItem

    owner, password = _staff(db_session, "dash-top-owner", "owner")
    _session_as(client, owner, password)

    product = Product(name="شلوار جین")
    db_session.add(product)
    db_session.flush()
    sale = Sale(total_amount=1_000_000, final_amount=1_000_000, payment_method="card",
                payment_confirmed=True, created_at=datetime.now(timezone.utc))
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=2,
                            unit_price=500_000, unit_cost=200_000, total_price=1_000_000))
    db_session.commit()

    owner_html = content_only(client.get("/admin").text)
    assert "شلوار جین" in owner_html
    assert "ranked-profit" in owner_html

    manager, password = _staff(db_session, "dash-top-manager", "manager")
    _session_as(client, manager, password)
    manager_html = content_only(client.get("/admin").text)
    assert "شلوار جین" in manager_html
    assert "ranked-profit" not in manager_html


# ── the page stays cheap, and stays lazy ─────────────────────────────────────

HEAVY = (
    ("services.reporting", "reconciliation_checks"),
    ("services.backup", "list_backups"),
    ("services.sms_triggers", "follow_up_plans"),
    ("services.analytics", "get_revenue_summary"),
)


@pytest.mark.parametrize("role", ["manager", "owner"])
def test_the_dashboard_cannot_reach_the_helpers_that_are_too_heavy_for_it(
    client, db_session, role, monkeypatch
):
    """Each of these verifies files, walks every sale or renders every message.

    They are the right tools for their own pages and the wrong ones for a page
    opened all day, so the dashboard must not be able to call any of them.
    """
    import services.dashboard as dashboard
    from importlib import import_module

    for module_name, attribute in HEAVY:
        module = import_module(module_name)

        def _refuse(*args, _name=f"{module_name}.{attribute}", **kwargs):
            raise AssertionError(f"the dashboard called {_name}")

        monkeypatch.setattr(module, attribute, _refuse)

    for _, attribute in HEAVY:
        assert attribute not in vars(dashboard), f"dashboard imports {attribute}"

    user, password = _staff(db_session, f"dash-heavy-{role}", role)
    _session_as(client, user, password)
    assert client.get("/admin").status_code == 200


def test_a_card_a_role_may_not_see_is_never_computed(client, db_session, monkeypatch):
    """Laziness is a correctness property here, not a performance tweak.

    The owner-only cards are the expensive ones — a follow-up count walks the
    customer list and a tier-up count reads every marker. If a manager's page
    computed them anyway the manager would be paying for the owner's work, and a
    number nobody is shown would exist at all.
    """
    import services.dashboard as dashboard

    def _refuse(*args, **kwargs):
        raise AssertionError("an owner-only card was built for a manager")

    monkeypatch.setattr(dashboard, "follow_up_due_count", _refuse)
    monkeypatch.setattr(dashboard, "tier_up_candidates", _refuse)
    monkeypatch.setattr(dashboard, "latest_backup", _refuse)
    monkeypatch.setattr(dashboard, "downgrade_candidates", _refuse)

    manager, password = _staff(db_session, "dash-lazy", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin").status_code == 200

    owner, password = _staff(db_session, "dash-lazy-owner", "owner")
    _session_as(client, owner, password)
    with pytest.raises(AssertionError):
        client.get("/admin")


def test_tier_up_candidates_asks_the_database_once_however_many_customers(db_session):
    """The list is a dashboard number now, so it cannot be one query each."""
    from sqlalchemy import event

    from database import engine
    from models import Customer
    from services.tier import tier_up_candidates

    customers = [
        Customer(first_name=f"مشتری {index}", phone=f"0912000{index:04d}",
                 tier="gold", referral_code=f"TU{index}")
        for index in range(15)
    ]
    db_session.add_all(customers)
    db_session.flush()
    # Every one of them was already wished at silver, so all fifteen are due.
    db_session.add_all([
        Settings(key=tier_up_marker_key(customer.id), value="silver")
        for customer in customers
    ])
    db_session.commit()

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        candidates = tier_up_candidates(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(candidates) == 15
    assert len(statements) <= 3, f"expected one query per side, got {len(statements)}"


# ── a zero is not good news ─────────────────────────────────────────────────

def _today_sales_card(html: str) -> str:
    """Just the «امروز → فروش» card, so its styling can be read on its own."""
    start = html.index("امروز</h3>")
    return html[start:start + 1200]


def test_a_day_with_no_sales_is_never_dressed_up_as_all_clear(client, db_session):
    """`is-clear` means «nothing needs attention», and only the alert row may say it.

    It used to be applied to any zero, which painted a shop that had sold nothing
    today in the same reassuring grey as an empty attention row — the page
    telling the owner the day was fine when it had simply been empty.
    """
    owner, password = _staff(db_session, "dash-zero", "owner")
    _session_as(client, owner, password)
    html = content_only(client.get("/admin").text)

    card = _today_sales_card(html)
    assert "is-clear" not in card, "a zero-sales day was reported as all clear"
    assert "0 فاکتور" in card

    # The alert row still may — that is the one place the class is honest.
    assert "action-item" in html
    assert "is-clear" in html


def test_the_page_prints_what_it_is_handed_and_decides_nothing_itself():
    dashboard = _read("dashboard")
    # No inline styling, like every other list page in the project.
    assert 'style="' not in dashboard
    # No branch on who is looking: a role-gated card was never built, so there is
    # nothing here to get wrong.
    for forbidden in ("staff_role", "guard.role", "session.get", "role_label =="):
        assert forbidden not in dashboard
    assert "{% for section in sections %}" in dashboard


def test_the_dashboard_hosts_no_action_at_all():
    """It reports and it links. The one mutation it used to carry turned out to
    duplicate a job that already ran, and it lives where the rule does now."""
    assert "<form" not in _read("dashboard")
    assert "action=" not in _read("dashboard")


# ── the deltas ───────────────────────────────────────────────────────────────

def test_a_change_is_only_reported_where_a_comparison_means_something():
    """
    A percentage against nothing or against a base so small the ratio is
    arithmetic is not a percentage — but the moment is exactly when the owner is
    reading, so each impossible ratio is replaced by the fact instead of
    silence: the arrival is stated, or the small base is named.
    """
    from services.dashboard import _delta

    # Yesterday was empty and today is not: the arrival is the news, not a
    # percentage against nothing.
    arrival = _delta(200, 0, label="دیروز", subject="فروشی")
    assert arrival == {"text": "دیروز فروشی نداشت", "good": True}
    # Both periods empty: the card's own figures already say it.
    assert _delta(0, 0, label="دیروز") is None
    # A red period standing on nothing has no comparison inside it.
    assert _delta(-5_000_000, 0, label="دیروز") is None
    # A base too small for a ratio: the base is named instead.
    small = _delta(200_000, 100, label="دیروز")
    assert small["text"] == "دیروز 100 ت بود" and small["good"] is None
    # A month that went into the red against a month that broke even: the base,
    # never a «۲۰۰۰٪ کمتر» catastrophe that never happened.
    loss = _delta(-5_000_000, 200_000, label="ماه گذشته")
    assert loss["text"] == "ماه گذشته 200,000 ت بود" and loss["good"] is None

    fall = _delta(80, 100, label="دیروز")
    assert fall["good"] is False
    assert "20٪ کمتر" in fall["text"]

    rise = _delta(120, 100, label="دیروز")
    assert rise["good"] is True
    assert "20٪ بیشتر" in rise["text"]

    # Spending less moves the same way as earning more, and only the card knows.
    cheaper = _delta(80, 100, label="ماه گذشته", lower_is_better=True)
    assert cheaper["good"] is True

    # And for spending, the arrival of a cost is the bad news — the same
    # sentence, but the verdict flips with the card's own notion of good.
    cost_arrival = _delta(500_000, 0, label="ماه گذشته", subject="هزینه‌ای",
                          lower_is_better=True)
    assert cost_arrival == {"text": "ماه گذشته هزینه‌ای نداشت", "good": False}

    assert _delta(100, 100, label="دیروز") == {"text": "بدون تغییر نسبت به دیروز",
                                              "good": None}


def test_the_sales_card_says_what_it_is_being_compared_against(client, db_session):
    """A figure with no reference point is a level nobody can read."""
    from datetime import datetime, timedelta, timezone

    from models import Sale, SaleItem

    owner, password = _staff(db_session, "dash-delta", "owner")
    _session_as(client, owner, password)

    product = Product(name="شال")
    db_session.add(product)
    db_session.flush()

    def _sale(amount, at):
        sale = Sale(total_amount=amount, final_amount=amount, payment_method="card",
                    payment_confirmed=True, created_at=at)
        db_session.add(sale)
        db_session.flush()
        db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=1,
                                unit_price=amount, unit_cost=0, total_price=amount))

    from services.analytics import get_date_range

    day_start, _end = get_date_range("today")
    # Just after yesterday's midnight is inside the same-span-yesterday window on
    # any day and at any hour, which half an hour before today would not be: that
    # lands *after* the window's end whenever the current time is not near
    # midnight. `now` is inside today's window by definition.
    _sale(300_000, day_start - timedelta(days=1) + timedelta(minutes=1))
    _sale(600_000, datetime.now(timezone.utc))
    db_session.commit()

    card = _today_sales_card(content_only(client.get("/admin").text))
    assert "stat-delta" in card
    assert "همین بازه دیروز" in card
    assert "is-good" in card


def test_the_service_keeps_the_explicit_role_that_the_template_does_not():
    service = _read("service")
    # The gate is one place and it is stated per card, so adding a card means
    # saying who it is for rather than inheriting whatever the neighbour did.
    assert 'min_role="owner"' in service
    assert "def dashboard_overview" in service


def test_the_products_page_gained_no_second_definition_of_low_stock():
    """One query, one place — the page and the dashboard call the same helper."""
    from pathlib import Path
    products = (Path(__file__).resolve().parents[1] / "routers/products.py").read_text()
    assert "stock_alerts(db)" in products
    assert "coalesce(ProductVariant.reserved_quantity" not in products


# ── the downgrade card ───────────────────────────────────────────────────────
# These two assert on «کاهش سطح» as the dashboard draws it, so they live with
# the dashboard's tests rather than with the downgrade rule's: the rule can be
# correct while the card that reports it is not.

def _customer(db, *, tier="gold", days_ago=None):
    """A club member whose last purchase was (or was not) long enough ago."""
    from datetime import datetime, timedelta, timezone

    index = next(_customer_seq)
    customer = Customer(
        first_name="مشتری آزمایشی",
        phone=f"0936{index:07d}",
        referral_code=f"DSH{index}",
        tier=tier,
        last_purchase_date=(None if days_ago is None else
                            datetime.now(timezone.utc) - timedelta(days=days_ago)),
    )
    db.add(customer)
    db.commit()
    return customer


def test_the_downgrade_card_links_to_the_page_instead_of_running_it(client, db_session):
    owner, password = _staff(db_session, "dash-dg", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="gold", days_ago=300)

    html = client.get("/admin").text
    assert "/admin/tier-downgrades" in html
    # A count, and the fact that it waits for a person.
    assert f">{customer.id}<" not in html
    assert "با تأیید شما" in html
    # The dashboard no longer holds the mutation: it draws no form of its own.
    body = html[MAIN_OPEN.search(html).end():]
    assert "<form" not in body


def test_the_downgrade_card_says_the_rule_is_off_rather_than_reporting_nobody(client, db_session):
    """An empty list because nothing qualified and an empty list because the rule
    is switched off are different facts, and «۰» cannot tell them apart."""
    owner, password = _staff(db_session, "dash-dg-off", "owner")
    _session_as(client, owner, password)
    _customer(db_session, tier="gold", days_ago=400)
    db_session.add(Settings(key="tier_downgrade_months", value="0"))
    db_session.commit()

    html = client.get("/admin").text
    assert "قاعده خاموش است" in html
    # …and the page itself offers the switch rather than an empty promise.
    page = client.get("/admin/tier-downgrades").text
    assert "غیرفعال است" in page
    assert "تنظیمات" in page


# ── the sentences a bare number used to leave unsaid ─────────────────────────

def test_the_first_sale_after_a_quiet_yesterday_is_said_as_news(client, db_session):
    """Yesterday empty, today not: the arrival is stated, never a percentage
    against nothing and never silence — the exact moment the owner reads for."""
    from datetime import datetime, timezone

    from models import Sale, SaleItem

    owner, password = _staff(db_session, "dash-arrival", "owner")
    _session_as(client, owner, password)

    product = Product(name="شال")
    db_session.add(product)
    db_session.flush()
    sale = Sale(total_amount=600_000, final_amount=600_000, payment_method="card",
                payment_confirmed=True, created_at=datetime.now(timezone.utc))
    db_session.add(sale)
    db_session.flush()
    db_session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=1,
                            unit_price=600_000, unit_cost=0, total_price=600_000))
    db_session.commit()

    card = _today_sales_card(content_only(client.get("/admin").text))
    assert "همین بازه دیروز فروشی نداشت" in card
    assert "is-good" in card
    # The old wrongness — a ratio against a base of zero — cannot return.
    assert "٪ بیشتر" not in card


def test_the_birthday_card_names_the_window_that_makes_its_count_mean_something(client, db_session):
    """«۷ نفر» is a number waiting for «در ۷ روز آینده»; an off window says so."""
    from models import Settings
    from tests.test_customers import make_customer, month_day_in

    owner, password = _staff(db_session, "dash-birthday", "owner")
    _session_as(client, owner, password)

    make_customer(db_session, first_name="تولدی", birth_month_day=month_day_in(3),
                  birth_year=1360)
    html = content_only(client.get("/admin").text)
    periodic = html[html.index("کارهای دوره‌ای"):]
    assert "تولد در ۷ روز آینده" in periodic
    # One birthday due is not good news in the «nothing to do» sense.
    assert "is-clear" not in periodic

    # The store can switch the window off; then the card says *that* rather
    # than counting a set it emptied.
    db_session.add(Settings(key="birthday_sms_days_before", value="0"))
    db_session.commit()
    html = content_only(client.get("/admin").text)
    assert "پنجره تولد خاموش است — تنظیمات" in html


def test_every_delta_names_what_it_counts():
    """«نداشت» is said about sales, profit or expenses in the shop's grammar —
    a future card's delta must say which, or the sentence lies about the card."""
    import re

    service = _read("service")
    calls = re.findall(r"(?<!def )_delta\([^)]*\)", service)
    assert calls, "the deltas moved — this guard is looking at the wrong thing"
    unnamed = [call for call in calls if "subject=" not in call]
    assert not unnamed, unnamed


# ── the paper form ───────────────────────────────────────────────────────────

def test_the_dashboard_prints_as_the_days_own_brief():
    """The print affordance, the paper context and the paper pair are part of
    the page, not a browser-menu accident — and the paper pair is the theme's,
    so a dark palette's cards never reach the printer as grey rectangles."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates/admin/dashboard.html").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "static/css/style.css").read_text(encoding="utf-8")

    # The print action is the header partial's — one button for every page —
    # so the page no longer carries its own; the partial's pin below is the
    # witness it exists.
    assert 'onclick="window.print()"' not in html
    # The paper heading is the shared one: the page names the day and the
    # reader's role into `print_heading`, and the page-header partial draws it.
    assert "print_heading" in html and "today" in html
    assert "{% if print_heading %}" in (Path(__file__).resolve().parents[1] / "templates/partials/page_header.html").read_text(encoding="utf-8")
    # The action row is navigation; the figures and the reading stay on paper.
    assert 'class="card screen-only"' in html
    assert ".stats-grid { grid-template-columns: 1fr 1fr; }" in css
    # The block is bounded to its own @media close — the file carries other
    # print blocks further down, and an unbounded slice would pass on theirs.
    block = css[css.index("The dashboard on paper"):]
    block = block[:block.index("\n}")]
    # The cards themselves — not merely the body — are painted with paper:
    # a dark palette's card surfaces must never reach the printer as grey
    # rectangles just because the page background went white.
    assert ".card, .stat-card { background: var(--paper) !important" in block
    assert "--paper-ink" in block
    assert ".action-grid { display: none !important; }" in block
