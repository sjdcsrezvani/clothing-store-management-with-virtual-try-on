"""کاهش سطح: a rule with no clock behind it.

Taking a level away from a customer is the one thing in this app that cannot be
undone from the page that caused it, so these tests are mostly about what must
*not* happen: nothing at all by itself, nothing to somebody who has bought
recently, nothing to whom the form did not name, and nothing that is not written
down.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

from models import AdminLog, Customer, Settings
from services.tier import (
    apply_tier_downgrades,
    downgrade_candidates,
    tier_downgrade_rule,
    tier_up_marker_key,
)
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff

ROOT = Path(__file__).resolve().parents[1]
_sequence = iter(range(10_000))


def _customer(db, *, tier="gold", days_ago=None, spent=0, archived=False, bought=False,
              name="مشتری آزمایشی"):
    """A club member whose last purchase was (or was not) long enough ago."""
    index = next(_sequence)
    customer = Customer(
        first_name=name,
        phone=f"0935{index:07d}",
        referral_code=f"DG{index}",
        tier=tier,
        last_purchase_date=None if days_ago is None else datetime.now(timezone.utc) - timedelta(days=days_ago),
        total_spent=spent,
        is_archived=archived,
    )
    db.add(customer)
    db.commit()
    return customer


def _post_apply(client, ids):
    data = {
        "csrf_token": csrf_token(client, "/admin/tier-downgrades"),
        "customer_ids": [str(value) for value in ids],
    }
    return client.post("/admin/tier-downgrades/apply", data=data, follow_redirects=False)


# ── who the rule reaches ─────────────────────────────────────────────────────

def test_only_customers_who_have_stopped_buying_are_offered(db_session):
    idle = _customer(db_session, tier="gold", days_ago=250)
    fresh = _customer(db_session, tier="diamond", days_ago=10)

    rows = downgrade_candidates(db_session)["rows"]
    assert [row["customer"].id for row in rows] == [idle.id]
    assert rows[0]["from_tier"] == "gold" and rows[0]["to_tier"] == "silver"
    assert fresh.id not in [row["customer"].id for row in rows]


def test_a_recent_purchase_protects_the_tier_whatever_they_have_spent(db_session):
    """The bug the old rule had, stated as a test.

    It compared **lifetime** spend, and only after the customer had bought
    recently — so with «حداقل مبلغ خرید» set, buying yesterday was not enough to
    keep a level if the lifetime figure was below the bar. Today a purchase in
    the window protects the tier on its own.
    """
    db_session.add(Settings(key="tier_downgrade_amount", value="50000000"))
    db_session.add(Settings(key="tier_downgrade_months", value="6"))
    db_session.commit()
    modest = _customer(db_session, tier="gold", days_ago=1, spent=0)

    plan = downgrade_candidates(db_session)
    assert plan["enabled"] is True
    assert modest.id not in [row["customer"].id for row in plan["rows"]]


def test_somebody_who_never_bought_is_offered_and_says_so(db_session):
    never = _customer(db_session, tier="gold", days_ago=None)
    rows = downgrade_candidates(db_session)["rows"]
    assert [row["customer"].id for row in rows] == [never.id]
    assert rows[0]["never_bought"] is True
    assert "هیچ خریدی" in rows[0]["reason"]
    # …and they sort above a merely quiet customer, because they are the quietest.
    quiet = _customer(db_session, tier="diamond", days_ago=400)
    rows = downgrade_candidates(db_session)["rows"]
    assert [row["customer"].id for row in rows] == [never.id, quiet.id]


def test_a_zero_window_switches_the_rule_off_rather_than_listing_everybody(db_session):
    _customer(db_session, tier="gold", days_ago=400)
    db_session.add(Settings(key="tier_downgrade_months", value="0"))
    db_session.commit()

    plan = downgrade_candidates(db_session)
    assert plan == {"rows": [], "skipped_archived": 0, "months": 0, "enabled": False,
                    "months_label": "۰"}
    assert tier_downgrade_rule(db_session)["enabled"] is False


def test_archived_customers_are_counted_and_left_out(db_session):
    _customer(db_session, tier="gold", days_ago=400)
    _customer(db_session, tier="gold", days_ago=400, archived=True)

    plan = downgrade_candidates(db_session)
    assert len(plan["rows"]) == 1
    assert plan["skipped_archived"] == 1


def test_silver_is_the_floor_so_it_is_never_a_candidate(db_session):
    _customer(db_session, tier="silver", days_ago=900)
    assert downgrade_candidates(db_session)["rows"] == []


# ── nothing happens by itself ────────────────────────────────────────────────

def test_there_is_no_longer_a_nightly_sweep():
    """The scheduler used to demote customers at 01:00 with nobody watching."""
    scheduler = (ROOT / "services" / "scheduler.py").read_text()
    # The file *explains* why no such pass lives there; what has to be true is
    # that no line of code could run one.
    code = "\n".join(line for line in scheduler.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "downgrade" not in code
    assert "check_tier_downgrade" not in code
    assert "run_downgrade_check_now" not in code

    tier = (ROOT / "services" / "tier.py").read_text()
    # The old pair, replaced by the rule/plan/apply trio.
    assert "def check_tier_downgrade" not in tier
    assert "def get_customers_for_downgrade_check" not in tier
    # And the setting that described a rule the code did not implement.
    assert "downgrade_amount" not in tier


def test_opening_the_page_demotes_nobody(client, db_session):
    owner, password = _staff(db_session, "dg-viewer", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="gold", days_ago=300)

    page = client.get("/admin/tier-downgrades")
    assert page.status_code == 200
    assert customer.first_name in page.text
    # The page says the thing it exists to say.
    assert "خودکار اجرا نمی‌شود" in page.text

    db_session.expire_all()
    assert db_session.query(Customer).filter(Customer.id == customer.id).one().tier == "gold"
    assert db_session.query(AdminLog).filter(AdminLog.action == "tier_downgrade").count() == 0


def test_nothing_is_pre_ticked_because_this_page_takes_something_away(client, db_session):
    owner, password = _staff(db_session, "dg-unticked", "owner")
    _session_as(client, owner, password)
    _customer(db_session, tier="gold", days_ago=300)

    page = client.get("/admin/tier-downgrades").text
    assert page.count('class="downgrade-check"') == 1
    marker = page.index('class="downgrade-check"')
    assert "checked" not in page[marker:marker + 200]


# ── who the form may touch ───────────────────────────────────────────────────

def test_only_the_ticked_customers_are_demoted(client, db_session):
    owner, password = _staff(db_session, "dg-apply", "owner")
    _session_as(client, owner, password)
    wanted = _customer(db_session, tier="diamond", days_ago=300, name="مینا")
    untouched = _customer(db_session, tier="gold", days_ago=300, name="سارا")

    response = _post_apply(client, [wanted.id])
    assert response.status_code == 303
    assert "1 مشتری" in unquote(response.headers["location"])

    db_session.expire_all()
    assert db_session.query(Customer).filter(Customer.id == wanted.id).one().tier == "gold"
    assert db_session.query(Customer).filter(Customer.id == untouched.id).one().tier == "gold"


def test_a_purchase_between_the_page_and_the_button_saves_the_tier(client, db_session):
    """The whole reason the ids are re-checked instead of trusted.

    A form can sit open while the shop keeps trading, and buying in the meantime
    is exactly what the rule says protects the level.
    """
    owner, password = _staff(db_session, "dg-stale", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="diamond", days_ago=300, name="نگار")

    # The page is drawn…
    assert client.get("/admin/tier-downgrades").status_code == 200
    # …then they buy, and only then is the button pressed.
    row = db_session.query(Customer).filter(Customer.id == customer.id).one()
    row.last_purchase_date = datetime.now(timezone.utc)
    db_session.commit()

    response = _post_apply(client, [customer.id])
    assert "خرید کرده" in unquote(response.headers["location"])

    db_session.expire_all()
    assert db_session.query(Customer).filter(Customer.id == customer.id).one().tier == "diamond"


def test_an_empty_selection_changes_nothing(client, db_session):
    owner, password = _staff(db_session, "dg-empty", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="gold", days_ago=300)

    response = client.post(
        "/admin/tier-downgrades/apply",
        data={"csrf_token": csrf_token(client, "/admin/tier-downgrades")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    db_session.expire_all()
    assert db_session.query(Customer).filter(Customer.id == customer.id).one().tier == "gold"


def test_a_demotion_is_written_down_with_the_names(client, db_session):
    """This route used to redirect with a bare count and log nothing at all."""
    owner, password = _staff(db_session, "dg-audit", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="gold", days_ago=300, name="سارا")

    _post_apply(client, [customer.id])

    entry = db_session.query(AdminLog).filter(AdminLog.action == "tier_downgrade").one()
    assert "سارا" in entry.detail
    assert entry.staff_user_id == owner.id


def test_a_downgrade_lets_them_earn_their_welcome_back(client, db_session):
    """The tier-up marker is cleared, so climbing back up queues a fresh wish."""
    owner, password = _staff(db_session, "dg-marker", "owner")
    _session_as(client, owner, password)
    customer = _customer(db_session, tier="gold", days_ago=300)
    db_session.add(Settings(key=tier_up_marker_key(customer.id), value="gold"))
    db_session.commit()

    _post_apply(client, [customer.id])

    db_session.expire_all()
    assert db_session.query(Settings).filter(
        Settings.key == tier_up_marker_key(customer.id)).first() is None


def test_the_rule_is_applied_in_one_query_not_one_per_customer(db_session):
    from sqlalchemy import event

    from database import engine

    for index in range(15):
        _customer(db_session, tier="gold", days_ago=300, name=f"مشتری {index}")

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        plan = downgrade_candidates(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(plan["rows"]) == 15
    # The settings read is a fixed handful of lookups no matter how big the shop
    # is; what must not scale with the customer count is the customer query.
    customer_queries = [s for s in statements if "FROM customers" in s]
    assert len(customer_queries) == 1, f"expected one query, got {len(customer_queries)}"
    assert len(statements) <= 20, f"too many statements: {len(statements)}"


# ── the pages that reach it ──────────────────────────────────────────────────

def test_a_manager_is_refused_by_both_routes(client, db_session):
    manager, password = _staff(db_session, "dg-manager", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin/tier-downgrades", follow_redirects=False).status_code == 403
    assert client.post("/admin/tier-downgrades/apply", data={}, follow_redirects=False).status_code == 403


def test_the_settings_page_no_longer_offers_a_field_the_rule_ignores(client, db_session):
    owner, password = _staff(db_session, "dg-settings", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin/settings").text

    assert "tier_downgrade_months" in page
    assert "tier_downgrade_amount" not in page
    assert "/admin/tier-downgrades" in page
    assert "خودکار کاهش سطح نمی‌یابد" in page


@pytest.mark.parametrize("tier,expected", [("gold", "نقره‌ای"), ("diamond", "طلایی")])
def test_the_new_level_is_named_in_the_shop_s_own_words(db_session, tier, expected):
    _customer(db_session, tier=tier, days_ago=300)
    row = downgrade_candidates(db_session)["rows"][0]
    assert row["to_label"] == expected
