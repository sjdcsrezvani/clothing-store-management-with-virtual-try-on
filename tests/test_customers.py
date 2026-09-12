"""Customer management: the list, the profile, and the store-agnostic birthdays.

The record belongs to the customer; child details are a module a children's shop
keeps and an adult clothing shop switches off. These tests pin that the module
switch really removes the fields everywhere, that the birthday rules follow the
configured target, and that a customer with purchase history can be archived but
never silently deleted.
"""
import itertools
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jdatetime
import pytest
from sqlalchemy import create_engine, inspect, text

from models import Customer, Sale, Settings, StaffUser
from services._common import jtoday
from services.customers import invalidate_customer_cache
from services.discount import calculate_discounts
from services.tier import (
    birthday_occasion_due,
    check_birthday_eligible,
    get_customers_for_birthday_check,
    get_tier_config,
)
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]
_counter = itertools.count(1)


# ── helpers ──────────────────────────────────────────────────────────────────

def month_day_in(days: int) -> str:
    """A Persian MM-DD that is exactly `days` days from today."""
    day = jtoday() + jdatetime.timedelta(days=days)
    return f"{day.month:02d}-{day.day:02d}"


def make_customer(db, *, tier="silver", **kwargs) -> Customer:
    index = next(_counter)
    customer = Customer(
        phone=kwargs.pop("phone", f"0912000{index:04d}"),
        first_name=kwargs.pop("first_name", f"مشتری{index}"),
        last_name=kwargs.pop("last_name", "تستی"),
        referral_code=f"T{index:05d}",
        tier=tier,
        **kwargs,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_sale(db, customer, amount=1_000_000, *, days_ago=0, payment_method="card",
              confirmed=True, **kwargs) -> Sale:
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    sale = Sale(
        customer_id=customer.id,
        total_amount=amount,
        final_amount=amount,
        payment_method=payment_method,
        payment_confirmed=confirmed,
        created_at=created,
        **kwargs,
    )
    db.add(sale)
    # A real sale runs `update_customer_after_purchase`, which stamps the
    # customer's last purchase; the list's active/کم‌فعال filter reads that
    # column, so the helper has to stamp it too or the sale is invisible to it.
    customer.last_purchase_date = created
    db.commit()
    db.refresh(sale)
    return sale


def set_setting(db, key, value):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(Settings(key=key, value=str(value)))
    db.commit()
    invalidate_customer_cache()


def list_html(client, query=""):
    response = client.get(f"/admin/customers{query}")
    assert response.status_code == 200
    return response.text


# ── presentation contract ────────────────────────────────────────────────────

CUSTOMERS_HTML = (ROOT / "templates" / "admin" / "customers.html").read_text()
PROFILE_HTML = (ROOT / "templates" / "admin" / "customer_detail.html").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()


def test_list_uses_the_catalog_design_language():
    assert 'class="page-heading product-page-heading"' in CUSTOMERS_HTML
    assert 'class="eyebrow"' in CUSTOMERS_HTML
    # The nav lives in the heading, not at the foot of the page.
    assert CUSTOMERS_HTML.index("admin-nav") < CUSTOMERS_HTML.index("</div>\n\n{% if msg")
    assert 'class="admin-nav"' in CUSTOMERS_HTML
    assert "👥" not in CUSTOMERS_HTML  # the old emoji heading
    # No inline styling survives on the page.
    assert 'style="' not in CUSTOMERS_HTML


def test_list_table_is_scrollable_accessible_and_has_real_empty_states():
    assert 'class="table-scroll"' in CUSTOMERS_HTML
    assert CUSTOMERS_HTML.count('scope="col"') >= 10
    assert 'class="empty-state"' in CUSTOMERS_HTML
    # An empty store and an over-filtered list are different situations.
    assert "هنوز مشتری‌ای ثبت نشده است" in CUSTOMERS_HTML
    assert "مشتری با این فیلترها پیدا نشد" in CUSTOMERS_HTML
    assert 'class="product-pagination"' in CUSTOMERS_HTML
    assert "{{ total }} مشتری" in CUSTOMERS_HTML


def test_profile_page_design_language_and_sections():
    assert 'class="page-heading product-page-heading"' in PROFILE_HTML
    assert 'class="table-scroll"' in PROFILE_HTML
    assert PROFILE_HTML.count('scope="col"') >= 5
    assert 'class="empty-state"' in PROFILE_HTML
    assert 'style="' not in PROFILE_HTML
    for hook in ("سابقه خرید", "تخفیف‌ها و معرفی‌ها", "ویرایش پرونده", "بایگانی و حذف"):
        assert hook in PROFILE_HTML, hook


def test_new_colours_come_from_theme_tokens():
    section = STYLE_CSS[STYLE_CSS.index("/* ===================== Customer club"):]
    section = section[:section.index("High-contrast")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section), section[:200]
    for token in ("var(--card)", "var(--ink-soft)", "var(--candy-dark)", "var(--mint-dark)",
                  "var(--sunshine)", "var(--sky-dark)", "var(--rule)", "var(--surface-soft)"):
        assert token in section, token
    # Status must not rest on colour alone.
    assert 'html[data-theme-mode="high-contrast"] .status-badge' in STYLE_CSS


# ── list: filters ────────────────────────────────────────────────────────────

def test_search_covers_name_phone_referral_code_and_child_name(authed, db_session):
    target = make_customer(db_session, first_name="زهرا", last_name="کاظمی", child_name="آوا")
    make_customer(db_session, first_name="دیگر", last_name="کسی", child_name="بهار")

    assert "زهرا" in list_html(authed, "?search=زهرا")
    assert "آوا" in list_html(authed, f"?search={target.phone}")
    assert "زهرا" in list_html(authed, "?search=%D8%A2%D9%88%D8%A7")  # «آوا» as a child name
    assert "دیگر" not in list_html(authed, "?search=زهرا")


def test_child_name_is_not_searchable_when_the_module_is_off(authed, db_session):
    make_customer(db_session, first_name="زهرا", child_name="آوا")
    set_setting(db_session, "child_profile_enabled", "0")

    assert "زهرا" not in list_html(authed, "?search=%D8%A2%D9%88%D8%A7")


def test_status_filters_split_active_inactive_debtor_and_discount(authed, db_session):
    """Names avoid the KPI labels, which contain words like «فعال» and «بدهکار»."""
    active = make_customer(db_session, first_name="آرش")
    make_sale(db_session, active, days_ago=3)
    stale = make_customer(db_session, first_name="بهرام")
    make_sale(db_session, stale, days_ago=200)
    make_customer(db_session, first_name="پرویز", total_debt=500_000)
    make_customer(db_session, first_name="جمشید", referred_discount=30_000)

    def names(body):
        return {n for n in ("آرش", "بهرام", "پرویز", "جمشید") if n in body}

    assert names(list_html(authed, "?status=active")) == {"آرش"}
    # «بدون خرید ۹۰ روز» is literal: a customer who never bought qualifies too,
    # which is what the row badge and the KPI labels already say.
    assert names(list_html(authed, "?status=inactive")) == {"بهرام", "پرویز", "جمشید"}
    assert names(list_html(authed, "?status=debtor")) == {"پرویز"}
    assert names(list_html(authed, "?status=discount")) == {"جمشید"}
    assert names(list_html(authed)) == {"آرش", "بهرام", "پرویز", "جمشید"}


def test_tag_filter_matches_whole_tags_only(authed, db_session):
    tagged = make_customer(db_session, first_name="ویژه", tags="vip,wholesale")
    make_customer(db_session, first_name="عادی", tags="followup")

    body = list_html(authed, "?tag=vip")
    assert "ویژه" in body and "عادی" not in body
    # «ویژه (VIP)» is rendered as a chip, not as the raw key.
    assert "ویژه (VIP)" in body and ">vip<" not in body


def test_sorts_put_the_expected_customer_first(authed, db_session):
    small = make_customer(db_session, first_name="کمخرید", total_spent=100_000, total_purchases=1)
    big = make_customer(db_session, first_name="پرخرید", total_spent=9_000_000, total_purchases=9,
                        total_debt=400_000, total_points=900)

    def first_name_in(body):
        found = re.search(r'/admin/customers/(\d+)"', body)
        return found.group(1) if found else None

    assert first_name_in(list_html(authed, "?sort=purchase_desc")) == str(big.id)
    assert first_name_in(list_html(authed, "?sort=purchase_asc")) == str(small.id)
    assert first_name_in(list_html(authed, "?sort=count_desc")) == str(big.id)
    assert first_name_in(list_html(authed, "?sort=points")) == str(big.id)
    assert first_name_in(list_html(authed, "?sort=debt")) == str(big.id)


def test_sort_keys_from_the_old_page_still_work(authed, db_session):
    """Bookmarks to ?sort=purchase_desc and friends must not 500."""
    make_customer(db_session)
    for key in ("date", "tier", "purchase_desc", "purchase_asc", "points", "name",
                "debt", "last_purchase", "oldest", "count_desc", "nonsense"):
        assert authed.get(f"/admin/customers?sort={key}").status_code == 200


# ── list: paging ─────────────────────────────────────────────────────────────

def test_pagination_pages_at_25_and_reports_the_count(authed, db_session):
    for index in range(27):
        make_customer(db_session, first_name=f"مشتری جستجو {index}")

    first = list_html(authed)
    assert "27 مشتری" in first
    assert "صفحه 1 از 2" in first

    second = list_html(authed, "?page=2")
    assert "صفحه 2 از 2" in second
    # 27 customers → 25 rows on the first page, 2 on the second. Each row links
    # to its customer twice (name and «پرونده»), so count distinct ids.
    def row_ids(body):
        return set(re.findall(r'/admin/customers/(\d+)"', body))

    assert len(row_ids(first)) == 25
    assert len(row_ids(second)) == 2
    assert not row_ids(first) & row_ids(second)


def test_page_links_keep_a_filter_with_a_space_intact(authed, db_session):
    """The old links interpolated the search term raw, so a space broke them."""
    for index in range(27):
        make_customer(db_session, first_name=f"علی رضایی {index}")

    body = list_html(authed, "?search=" + "علی رضا".replace(" ", "+"))
    links = re.findall(r'href="(\?page=2[^"]*)"', body)
    assert links, "the second page should be linked"
    link = links[0].replace("&amp;", "&")
    assert " " not in link, link
    # The filter must survive the round trip, whether the space is written as
    # `+` or `%20` (Jinja's urlencode uses %20).
    assert parse_qs(urlsplit(link).query)["search"] == ["علی رضا"], link

    # The page itself must still find the rows.
    assert authed.get("/admin/customers?search=%D8%B9%D9%84%DB%8C+%D8%B1%D8%B6%D8%A7&page=2").status_code == 200


def test_page_number_beyond_the_end_clamps(authed, db_session):
    for index in range(27):
        make_customer(db_session, first_name=f"صفحه‌ای {index}")
    body = list_html(authed, "?page=99")
    assert "صفحه 2 از 2" in body
    assert len(set(re.findall(r'/admin/customers/(\d+)"', body))) == 2


# ── list: KPIs ───────────────────────────────────────────────────────────────

def test_kpi_cards_count_what_they_claim(authed, db_session):
    active = make_customer(db_session, first_name="فعال")
    make_sale(db_session, active, days_ago=5)
    make_customer(db_session, first_name="بدهکار", total_debt=250_000)
    make_customer(db_session, first_name="تازه", created_at=datetime.now(timezone.utc))
    set_setting(db_session, "birthday_target", "customer")
    make_customer(db_session, first_name="تولدی", birth_month_day=month_day_in(3), birth_year=1360)

    body = list_html(authed)
    assert "کل مشتریان" in body
    assert "بدهکاران (نسیه)" in body
    assert "تولد مشتریان" in body  # the label follows the configured target
    assert "250,000" in body


def test_kpi_birthday_label_follows_the_child_module(authed, db_session):
    make_customer(db_session)
    assert "تولد فرزندان" in list_html(authed)
    set_setting(db_session, "birthday_target", "both")
    assert "تولدها" in list_html(authed)


# ── archive & delete ─────────────────────────────────────────────────────────

def test_archived_customers_leave_the_list_until_asked_for(authed, db_session):
    customer = make_customer(db_session, first_name="بایگانی‌شونده")
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    response = authed.post(f"/admin/customers/{customer.id}/archive",
                           data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(customer)
    assert customer.is_archived is True

    assert "بایگانی‌شونده" not in list_html(authed)
    assert "بایگانی‌شونده" in list_html(authed, "?status=archived")
    # And it is a toggle, not a one-way door.
    authed.post(f"/admin/customers/{customer.id}/archive",
                data={"csrf_token": token}, follow_redirects=False)
    db_session.refresh(customer)
    assert customer.is_archived is False
    assert "بایگانی‌شونده" in list_html(authed)


def test_delete_is_refused_when_the_customer_has_purchases(authed, db_session):
    customer = make_customer(db_session, first_name="دارای خرید")
    make_sale(db_session, customer)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    response = authed.post(f"/admin/customers/{customer.id}/delete",
                           data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    # Both the customer and the sale survive: history stays attributable.
    assert db_session.query(Customer).filter(Customer.id == customer.id).first() is not None
    assert db_session.query(Sale).filter(Sale.customer_id == customer.id).count() == 1


def test_a_customer_without_purchases_can_still_be_deleted(authed, db_session):
    customer = make_customer(db_session, first_name="بدون خرید")
    customer_id = customer.id
    token = csrf_token(authed, f"/admin/customers/{customer_id}")

    response = authed.post(f"/admin/customers/{customer_id}/delete",
                           data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(Customer).filter(Customer.id == customer_id).first() is None


# ── profile ──────────────────────────────────────────────────────────────────

def test_profile_shows_history_totals_and_the_drift_flag(authed, db_session):
    customer = make_customer(db_session, first_name="پرونده‌دار", total_spent=999_999,
                             total_purchases=7, total_points=120)
    make_sale(db_session, customer, amount=1_500_000)

    body = list_html(authed, f"/{customer.id}")
    assert "سابقه خرید" in body
    assert "پرونده‌دار" in body
    assert "1,500,000" in body  # computed from the sales
    # The stored counters disagree, so the page says so instead of trusting them.
    assert "نامطابق" in body
    assert "999,999" in body


def test_profile_without_purchases_shows_an_empty_state(authed, db_session):
    customer = make_customer(db_session, first_name="تازه‌وارد")
    body = list_html(authed, f"/{customer.id}")
    assert "هنوز خریدی ثبت نشده" in body
    assert "نامطابق" not in body


def test_profile_404s_for_an_unknown_customer(authed):
    assert authed.get("/admin/customers/424242").status_code == 404


def test_profile_requires_a_manager(client, db_session):
    from services.security import hash_password

    customer = make_customer(db_session, first_name="محدود")
    cashier = StaffUser(username="cashier-c", password_hash=hash_password("role-pass"), role="cashier")
    db_session.add(cashier)
    db_session.commit()

    token = csrf_token(client)
    client.post("/admin/login", data={"username": "cashier-c", "password": "role-pass",
                                      "csrf_token": token}, follow_redirects=False)

    assert client.get("/admin/customers", follow_redirects=False).status_code == 403
    assert client.get(f"/admin/customers/{customer.id}", follow_redirects=False).status_code == 403


def test_profile_never_offers_delete_when_sales_exist(authed, db_session):
    customer = make_customer(db_session)
    make_sale(db_session, customer)
    body = list_html(authed, f"/{customer.id}")
    assert "حذف کامل مشتری" not in body
    assert "حذف ممکن نیست" in body


def test_profile_date_fields_are_prefilled_in_persian_digits(authed, db_session):
    """Every other date held in a form field is rendered by `jalali_str`.

    These two were the exception: Latin digits would silently change shape the
    first time the calendar was used, because the picker writes Persian ones.
    """
    customer = make_customer(db_session, birth_month_day="05-12", birth_year=1360,
                             child_name="آوا", child_birthday="02-03", child_birth_year=1400)
    body = list_html(authed, f"/{customer.id}")

    assert 'value="۱۳۶۰/۰۵/۱۲"' in body
    assert 'value="۱۴۰۰/۰۲/۰۳"' in body
    assert "1360/05/12" not in body

    # With no year on file the value stays month-day, still in Persian digits.
    yearless = make_customer(db_session, birth_month_day="07-02")
    body = list_html(authed, f"/{yearless.id}")
    assert 'value="۰۷-۰۲"' in body


def test_the_persian_digits_the_picker_writes_are_saved_back(authed, db_session):
    """The round trip the digit change depends on."""
    customer = make_customer(db_session, birth_month_day="05-12", birth_year=1360)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    authed.post(f"/admin/customers/{customer.id}/meta", data={
        "csrf_token": token,
        "birth_date": "۱۳۷۰/۰۸/۱۹",
    }, follow_redirects=False)

    db_session.refresh(customer)
    assert customer.birth_month_day == "08-19"
    assert customer.birth_year == 1370


# ── profile card save ────────────────────────────────────────────────────────

def test_meta_saves_note_tags_consent_and_both_birthdays(authed, db_session):
    customer = make_customer(db_session, child_name="آوا")
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    response = authed.post(f"/admin/customers/{customer.id}/meta", data={
        "csrf_token": token,
        "birth_date": "۱۳۶۰/۰۵/۱۲",
        "child_name": "آوا",
        "child_birth_date": "1400/02/03",
        "notes": "  فقط سایز ۳ می‌خرد  ",
        "tags": ["vip", "followup"],
        "sms_opt_in": "1",
    }, follow_redirects=False)

    assert response.status_code == 303
    db_session.refresh(customer)
    assert customer.birth_month_day == "05-12"
    assert customer.birth_year == 1360
    assert customer.child_birthday == "02-03"
    assert customer.child_birth_year == 1400
    assert customer.notes == "فقط سایز ۳ می‌خرد"
    assert customer.tags == "vip,followup"
    assert customer.sms_opt_in is True


def test_meta_reads_an_iso_birthday_the_same_way_the_rest_of_the_app_does(authed, db_session):
    customer = make_customer(db_session)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    authed.post(f"/admin/customers/{customer.id}/meta", data={
        "csrf_token": token,
        "birth_date": "2026-09-12",  # what a hand-typed ISO value looks like
    }, follow_redirects=False)

    db_session.refresh(customer)
    expected = jdatetime.date.fromgregorian(date=datetime(2026, 9, 12).date())
    assert customer.birth_month_day == f"{expected.month:02d}-{expected.day:02d}"
    assert customer.birth_year == expected.year
    assert customer.birth_year != 2026  # never stored as a Jalali year


def test_unticking_consent_and_clearing_tags_both_stick(authed, db_session):
    customer = make_customer(db_session, tags="vip", sms_opt_in=True)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    authed.post(f"/admin/customers/{customer.id}/meta", data={
        "csrf_token": token,
        "notes": "",
    }, follow_redirects=False)

    db_session.refresh(customer)
    assert customer.tags == ""
    assert not customer.sms_opt_in
    assert customer.notes is None


def test_meta_ignores_child_fields_when_the_module_is_off(authed, db_session):
    customer = make_customer(db_session)
    set_setting(db_session, "child_profile_enabled", "0")
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    authed.post(f"/admin/customers/{customer.id}/meta", data={
        "csrf_token": token,
        "child_name": "نباید ذخیره شود",
        "child_birth_date": "1400/01/01",
    }, follow_redirects=False)

    db_session.refresh(customer)
    assert customer.child_name is None
    assert customer.child_birthday is None
    assert customer.child_birth_year is None


# ── ages ─────────────────────────────────────────────────────────────────────

def test_age_is_correct_before_and_after_this_years_birthday(authed, db_session):
    """A birthday later this year must not count as another year older."""
    today = jtoday()
    later = today + jdatetime.timedelta(days=40)
    earlier = today - jdatetime.timedelta(days=40)

    older = make_customer(db_session, first_name="تولد گذشته", birth_year=today.year - 30,
                          birth_month_day=f"{earlier.month:02d}-{earlier.day:02d}")
    younger = make_customer(db_session, first_name="تولد نیامده", birth_year=today.year - 30,
                            birth_month_day=f"{later.month:02d}-{later.day:02d}")

    body = list_html(authed, "?search=%D8%AA%D9%88%D9%84%D8%AF")
    assert f"{older.id}" in body and f"{younger.id}" in body
    assert "۳۰ ساله" in body or "30 ساله" in body
    assert "۲۹ ساله" in body or "29 ساله" in body


def test_birthday_without_a_year_shows_the_day_and_no_age(authed, db_session):
    make_customer(db_session, first_name="بدون‌سال", birth_month_day="05-12")
    body = list_html(authed, "?search=%D8%A8%D8%AF%D9%88%D9%86")
    assert "۱۲ مرداد" in body
    assert "ساله" not in body


# ── birthday target matrix ───────────────────────────────────────────────────

@pytest.mark.parametrize("target,subjects", [
    ("customer", ("customer",)),
    ("child", ("child",)),
    ("both", ("customer", "child")),
])
def test_subjects_follow_the_configured_target(target, subjects, db_session):
    from services._common import birthday_subjects

    set_setting(db_session, "birthday_target", target)
    assert birthday_subjects(db_session) == subjects


def test_child_only_target_with_the_module_off_degrades_to_the_customer(db_session):
    from services._common import birthday_subjects

    set_setting(db_session, "birthday_target", "child")
    set_setting(db_session, "child_profile_enabled", "0")
    assert birthday_subjects(db_session) == ("customer",)


def test_birthday_discount_names_the_occasion_that_fired(db_session):
    config = get_tier_config(db_session)

    set_setting(db_session, "birthday_target", "customer")
    customer = make_customer(db_session, tier="gold", birth_month_day=month_day_in(2), birth_year=1360)
    assert birthday_occasion_due(customer, config, ("customer",)) == "customer"

    discounts = calculate_discounts(customer=customer, total_amount=1_000_000, db=db_session)
    assert discounts["birthday_discount"] > 0
    assert "تخفیف تولد شما" in " ".join(discounts["details"])


def test_a_childs_birthday_is_not_discounted_when_the_store_targets_the_customer(db_session):
    set_setting(db_session, "birthday_target", "customer")
    customer = make_customer(db_session, tier="gold", child_birthday=month_day_in(2), child_birth_year=1400)

    discounts = calculate_discounts(customer=customer, total_amount=1_000_000, db=db_session)
    assert discounts["birthday_discount"] == 0
    assert "تولد" not in " ".join(discounts["details"])


def test_child_birthday_still_discounts_for_a_childrens_shop(db_session):
    set_setting(db_session, "birthday_target", "child")
    customer = make_customer(db_session, tier="gold", child_birthday=month_day_in(2), child_birth_year=1400)

    discounts = calculate_discounts(customer=customer, total_amount=1_000_000, db=db_session)
    assert discounts["birthday_discount"] > 0
    assert "تخفیف تولد فرزند" in " ".join(discounts["details"])


def test_silver_customers_are_not_birthday_eligible(db_session):
    config = get_tier_config(db_session)
    customer = make_customer(db_session, tier="silver", birth_month_day=month_day_in(1))
    assert check_birthday_eligible(customer, config, ("customer",)) is False


def test_birthday_check_returns_the_occasion_and_skips_opt_outs(db_session):
    set_setting(db_session, "birthday_target", "both")
    set_setting(db_session, "birthday_sms_days_before", "7")

    mine = make_customer(db_session, tier="gold", first_name="خودم",
                         birth_month_day=month_day_in(3), birth_year=1360)
    their_child = make_customer(db_session, tier="gold", first_name="فرزندی",
                                child_birthday=month_day_in(2), child_birth_year=1400)
    opted_out = make_customer(db_session, tier="gold", first_name="انصراف",
                              birth_month_day=month_day_in(1), birth_year=1360, sms_opt_in=False)
    archived = make_customer(db_session, tier="gold", first_name="بایگانی",
                             birth_month_day=month_day_in(1), birth_year=1360, is_archived=True)
    not_due = make_customer(db_session, tier="gold", first_name="دور",
                            birth_month_day=month_day_in(60), birth_year=1360)

    result = get_customers_for_birthday_check(db_session, 7)
    entries = {customer.id: occasion for customer, _days, occasion in result["eligible"]}

    assert entries[mine.id] == "customer"
    assert entries[their_child.id] == "child"
    assert opted_out.id not in entries
    assert archived.id not in entries
    assert not_due.id not in entries
    assert result["blocked"] == 2  # the opt-out and the archived one


def test_one_message_per_customer_and_the_closest_birthday_wins(db_session):
    set_setting(db_session, "birthday_target", "both")
    child_sooner = make_customer(db_session, tier="gold", first_name="فرزندزودتر",
                                 birth_month_day=month_day_in(4), birth_year=1360,
                                 child_birthday=month_day_in(1), child_birth_year=1400)
    mine_sooner = make_customer(db_session, tier="gold", first_name="خودمزودتر",
                                birth_month_day=month_day_in(1), birth_year=1360,
                                child_birthday=month_day_in(4), child_birth_year=1400)
    tie = make_customer(db_session, tier="gold", first_name="مساوی",
                        birth_month_day=month_day_in(2), birth_year=1360,
                        child_birthday=month_day_in(2), child_birth_year=1400)

    result = get_customers_for_birthday_check(db_session, 7)
    occasions = {customer.id: occasion for customer, _days, occasion in result["eligible"]}

    # One message per customer, whichever birthday is nearest — and on a tie the
    # customer's own birthday takes priority.
    assert len([entry for entry in result["eligible"] if entry[0].id == child_sooner.id]) == 1
    assert occasions[child_sooner.id] == "child"
    assert occasions[mine_sooner.id] == "customer"
    assert occasions[tie.id] == "customer"


def test_birthday_sms_payload_never_greets_the_wrong_person():
    from services.sms import birthday_sms_vars

    child = birthday_sms_vars("سجاد", "آوا", "child")
    assert child["var2"] == "آوا"
    assert child["var3"] == "فرزند شما"

    own = birthday_sms_vars("سجاد", "آوا", "customer")
    assert own["var2"] == "سجاد"
    assert own["var3"] == "شما"

    # A child's birthday with no name on file reads like it always did.
    assert birthday_sms_vars("سجاد", "", "child")["var2"] == "فرزند شما"


def test_birthday_sms_route_sends_with_the_stored_pattern(authed, db_session):
    """The old route read the pattern from the wrong config and sent nothing."""
    set_setting(db_session, "birthday_target", "customer")
    set_setting(db_session, "birthday_sms_days_before", "7")
    set_setting(db_session, "sms_pattern_birthday", "تولدت مبارک {var2}")
    due = make_customer(db_session, tier="gold", birth_month_day=month_day_in(2), birth_year=1360)

    token = csrf_token(authed, "/admin")
    response = authed.post("/admin/check-birthdays", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert "birthday_msg=1" in response.headers["location"]
    assert db_session.query(Settings).filter(
        Settings.key.like(f"birthday_sms_{due.id}_%")
    ).count() == 1


def test_birthday_sms_route_reports_a_missing_pattern(authed, db_session):
    set_setting(db_session, "birthday_target", "customer")
    make_customer(db_session, tier="gold", birth_month_day=month_day_in(2), birth_year=1360)
    token = csrf_token(authed, "/admin")

    response = authed.post("/admin/check-birthdays", data={"csrf_token": token}, follow_redirects=False)
    assert "birthday_err=pattern" in response.headers["location"]


# ── the child module ─────────────────────────────────────────────────────────

def _create_customer_step(client, phone="09129998877"):
    """The cashier's new-customer form — the one that collects child details."""
    token = csrf_token(client, "/sales/new")
    response = client.post("/sales/lookup-customer",
                           data={"phone": phone, "csrf_token": token})
    assert response.status_code == 200
    assert "مشتری جدید" in response.text
    return response.text


def test_child_block_is_absent_from_every_form_when_the_module_is_off(authed, db_session):
    set_setting(db_session, "child_profile_enabled", "0")

    registration = authed.get("/customers/lookup").text
    checkout = _create_customer_step(authed)
    profile_customer = make_customer(db_session, first_name="بدون‌فرزند")
    profile = list_html(authed, f"/{profile_customer.id}")

    for name, body in (("registration", registration), ("checkout", checkout),
                       ("profile", profile)):
        assert "child_birthday" not in body, name
        assert "child_birth_date" not in body, name
        assert "نام فرزند" not in body, name
        # The birthday picker that only exists for the child is gone with it.
        assert 'id="child_birthday"' not in body, name


def test_child_block_is_present_when_the_module_is_on(authed, db_session):
    registration = authed.get("/customers/lookup").text
    assert "child_birthday" in registration
    assert "نام فرزند" in registration

    checkout = _create_customer_step(authed, phone="09129998866")
    assert 'class="persian-date-input" data-pdp-max="today"' in checkout
    assert "نام فرزند" in checkout

    customer = make_customer(db_session, first_name="با‌فرزند")
    profile = list_html(authed, f"/{customer.id}")
    assert "child_birth_date" in profile
    assert "تاریخ تولد فرزند" in profile


def test_registration_does_not_store_child_data_when_the_module_is_off(client, authed, db_session):
    set_setting(db_session, "child_profile_enabled", "0")

    token = csrf_token(client)
    client.post("/customers", data={
        "csrf_token": token,
        "phone": "09121112233",
        "first_name": "بزرگسال",
        "last_name": "بدون فرزند",
        "child_name": "نباید ذخیره شود",
        "child_birthday": "1400/01/01",
    }, follow_redirects=False)

    customer = db_session.query(Customer).filter(Customer.phone == "09121112233").first()
    assert customer is not None
    assert customer.child_name is None
    assert customer.child_birthday is None


def test_update_child_refuses_when_the_module_is_off(authed, db_session):
    customer = make_customer(db_session, child_name="قبلی", child_birthday="01-01")
    set_setting(db_session, "child_profile_enabled", "0")

    token = csrf_token(authed, "/customers/lookup")
    response = authed.post(f"/customers/{customer.id}/update-child", data={
        "csrf_token": token,
        "child_name": "جدید",
        "child_birthday": "1401/02/02",
    })

    assert "پرونده فرزند ندارد" in response.text
    db_session.refresh(customer)
    assert customer.child_name == "قبلی"
    assert customer.child_birthday == "01-01"


def test_turning_the_module_off_keeps_the_data_on_file(authed, db_session):
    customer = make_customer(db_session, child_name="آوا", child_birthday="03-20", child_birth_year=1400)
    set_setting(db_session, "child_profile_enabled", "0")
    set_setting(db_session, "child_profile_enabled", "1")

    db_session.refresh(customer)
    assert customer.child_name == "آوا"
    assert customer.child_birthday == "03-20"


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_toggles_persist_the_unticked_state(client, authed, db_session):
    """An unchecked box posts nothing, so the form needs its hidden companion."""
    token = csrf_token(client, "/admin/settings")
    client.post("/admin/settings", data={
        "csrf_token": token,
        # The hidden companion the form always posts for an unticked box.
        "child_profile_enabled": "0",
    }, follow_redirects=False)

    row = db_session.query(Settings).filter(Settings.key == "child_profile_enabled").first()
    assert row is not None and row.value == "0"

    token = csrf_token(client, "/admin/settings")
    client.post("/admin/settings", data={
        "csrf_token": token,
        # Hidden companion first, then the ticked box — the last value wins.
        "child_profile_enabled": ["0", "1"],
    }, follow_redirects=False)

    db_session.expire_all()
    row = db_session.query(Settings).filter(Settings.key == "child_profile_enabled").first()
    assert row.value == "1"


def test_turning_the_child_module_off_cannot_leave_a_child_only_target(client, authed, db_session):
    token = csrf_token(client, "/admin/settings")
    client.post("/admin/settings", data={
        "csrf_token": token,
        "birthday_target": "child",
    }, follow_redirects=False)
    db_session.expire_all()
    assert db_session.query(Settings).filter(Settings.key == "birthday_target").first().value == "child"

    token = csrf_token(client, "/admin/settings")
    client.post("/admin/settings", data={
        "csrf_token": token,
        "birthday_target": "child",
        "child_profile_enabled": "0",
    }, follow_redirects=False)

    db_session.expire_all()
    assert db_session.query(Settings).filter(Settings.key == "birthday_target").first().value == "customer"


def test_settings_page_exposes_both_toggles(authed):
    body = authed.get("/admin/settings").text
    assert 'name="birthday_target"' in body
    assert 'name="child_profile_enabled"' in body
    assert 'value="customer"' in body and 'value="child"' in body and 'value="both"' in body


def test_defaults_keep_the_childrens_shop_behaviour(client, db_session):
    """No settings rows: today's behaviour (child birthdays, child profile on)."""
    assert db_session.query(Settings).filter(Settings.key == "birthday_target").first() is None
    from services._common import birthday_subjects, child_profile_enabled

    assert child_profile_enabled(db_session) is True
    assert birthday_subjects(db_session) == ("child",)


# ── campaign consent ─────────────────────────────────────────────────────────

def test_campaign_send_skips_opted_out_and_archived_customers(authed, db_session):
    from models import Campaign

    campaign = Campaign(name="کمپین", code="CMP1", discount_percent=10, min_purchase=0)
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)

    set_setting(db_session, "sms_pattern_campaign", "کمپین {var2}")
    willing = make_customer(db_session, tier="diamond", first_name="راضی", phone="09121110001")
    unwilling = make_customer(db_session, tier="diamond", first_name="ناراضی", phone="09121110002",
                              sms_opt_in=False)
    archived = make_customer(db_session, tier="diamond", first_name="بایگانی", phone="09121110003",
                             is_archived=True)

    token = csrf_token(authed, "/admin/campaigns")
    response = authed.post(f"/admin/campaigns/{campaign.id}/send",
                           data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 200
    assert "1 مشتری الماس در صف" in response.text
    assert "انصراف" in response.text
    markers = db_session.query(Settings).filter(Settings.key.like("campaign_sms_%")).all()
    assert len(markers) == 1
    assert str(willing.id) in markers[0].key
    assert str(unwilling.id) not in markers[0].key
    assert str(archived.id) not in markers[0].key


# ── migration ────────────────────────────────────────────────────────────────

def test_new_customer_columns_are_added_without_changing_existing_rows(tmp_path):
    from main import _apply_missing_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, phone VARCHAR(15), "
            "referral_code VARCHAR(10))"
        ))
        conn.execute(text(
            "INSERT INTO customers (id, phone, referral_code) VALUES (1, '09120000000', 'OLD001')"
        ))

    _apply_missing_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("customers")}
    assert {"birth_month_day", "birth_year", "notes", "tags", "sms_opt_in",
            "is_archived", "child_birth_year"} <= columns

    with engine.connect() as conn:
        row = conn.execute(text("SELECT sms_opt_in, is_archived FROM customers WHERE id=1")).one()
    # An existing customer stays opted in and unarchived — not silently dropped
    # out of the birthday and campaign sends.
    assert row[0] == 1
    assert row[1] == 0


def test_opt_in_helpers_treat_null_as_consent(db_session):
    from services.customers import marketing_opt_in

    assert marketing_opt_in(make_customer(db_session, sms_opt_in=None)) is True
    assert marketing_opt_in(make_customer(db_session, sms_opt_in=True)) is True
    assert marketing_opt_in(make_customer(db_session, sms_opt_in=False)) is False


# ── links from the list and the profile ──────────────────────────────────────

def test_list_and_profile_are_linked_both_ways(authed, db_session):
    customer = make_customer(db_session, first_name="پیوندی")
    assert f'/admin/customers/{customer.id}"' in list_html(authed)

    body = list_html(authed, f"/{customer.id}")
    assert 'href="/admin/customers"' in body
    assert f'href="/admin/credit/{customer.id}"' in body


def test_discount_buttons_redirect_back_to_the_admin_profile(authed, db_session):
    customer = make_customer(db_session, referred_discount=30_000)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    response = authed.post(f"/customers/{customer.id}/use-referred-discount", data={
        "csrf_token": token,
        "next": f"/admin/customers/{customer.id}",
    }, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/admin/customers/{customer.id}?msg=")
    db_session.refresh(customer)
    assert customer.has_used_referred_discount is True


def test_discount_buttons_refuse_an_external_next(authed, db_session):
    customer = make_customer(db_session, referred_discount=30_000)
    token = csrf_token(authed, f"/admin/customers/{customer.id}")

    response = authed.post(f"/customers/{customer.id}/use-referred-discount", data={
        "csrf_token": token,
        "next": "https://example.com/steal",
    }, follow_redirects=False)

    assert response.status_code == 200  # falls back to the customer panel
    assert "example.com" not in response.text
