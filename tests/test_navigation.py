"""The shell a role is handed, and the page a refusal lands on.

The sidebar used to be a fixed list of twenty-five links: a cashier was shown
تنظیمات، پشتیبان‌ها and گزارش عملیات, and clicking any of them answered with
FastAPI's raw ``{"detail": …}`` because the app had no exception handler at all.
Two questions decide whether that is really fixed — is a door only drawn for
somebody who can open it, and does a refusal read like a page? — and these tests
ask both, by actually opening every door the sidebar draws.
"""
import re
from pathlib import Path

import pytest

from models import Sale
from services import dashboard
from services.navigation import NAV_SECTIONS, home_for, navigation_for, role_allows
from services.security import ROLE_ORDER
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _confirm_sale, _make_variant

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = (ROOT / "templates" / "base.html").read_text()
ERROR_HTML = (ROOT / "templates" / "admin" / "error.html").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()

ROLES = ("cashier", "manager", "owner")
ALL_ITEMS = [item for section in NAV_SECTIONS for item in section["items"]]

# A section label and an item label can read the same («مشتریان»), so the sidebar
# text is not a reliable way to count things; the markup is.
LABELS = [item["label"] for item in ALL_ITEMS]


def _sidebar(html: str) -> str:
    start = html.index('<nav class="sidebar-nav">')
    return html[start:html.index("</nav>", start)]


def _nav_hrefs(html: str) -> list[str]:
    return re.findall(r'<a href="([^"]+)" data-nav="', _sidebar(html))


# ── what the list says ───────────────────────────────────────────────────────

def test_every_sidebar_entry_states_a_role_that_exists():
    assert ALL_ITEMS
    for item in ALL_ITEMS:
        assert item["min_role"] in ROLE_ORDER, item
        assert item["key"], item
        assert item["href"].startswith(("/admin", "/sales")), item
    # …and no page is listed twice, because the active-state highlight picks one.
    hrefs = [item["href"] for item in ALL_ITEMS]
    keys = [item["key"] for item in ALL_ITEMS]
    assert len(hrefs) == len(set(hrefs))
    assert len(keys) == len(set(keys))


def test_a_role_is_offered_exactly_the_doors_it_may_open():
    for role in ROLES:
        offered = {(section["label"], item["href"])
                   for section in navigation_for(role) for item in section["items"]}
        for section in NAV_SECTIONS:
            for item in section["items"]:
                wanted = role_allows(role, item["min_role"])
                assert ((section["label"], item["href"]) in offered) is wanted, (role, item)


def test_a_section_with_nothing_left_in_it_is_not_printed():
    """Nobody is shown a heading with no items under it, or a blank one."""
    assert [section["label"] for section in navigation_for("cashier")] == ["فروشگاه"]
    for role in ROLES:
        for section in navigation_for(role):
            assert section["items"], section["label"]
            assert section["label"]


def test_one_definition_of_who_may_open_what():
    """The dashboard's cards and the sidebar must not answer this differently."""
    assert dashboard.role_allows is role_allows
    assert role_allows(None, "cashier") is False
    assert role_allows("nonsense", "cashier") is False
    assert role_allows("owner", "nonsense") is False
    assert role_allows("cashier", "cashier") is True
    assert role_allows("cashier", "manager") is False


# ── every door the sidebar draws actually opens ──────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_every_door_the_sidebar_draws_opens_for_whoever_it_drew_it_for(
        client, db_session, role):
    """The whole point: a link on the page must not be a refusal in disguise."""
    user, password = _staff(db_session, f"nav-open-{role}", role)
    _session_as(client, user, password)

    hrefs = _nav_hrefs(client.get(home_for(role)).text)
    assert hrefs, f"the {role} sidebar drew nothing"

    for href in hrefs:
        response = client.get(href, follow_redirects=False)
        assert response.status_code not in (403, 404), (
            f"the {role} sidebar offered {href} and the page refused it")


def test_a_cashier_is_not_offered_a_manager_or_owner_door(client, db_session):
    user, password = _staff(db_session, "nav-cashier-side", "cashier")
    _session_as(client, user, password)
    sidebar = _sidebar(client.get("/sales/new").text)

    assert "فروش جدید" in sidebar and "تاریخچه فروش" in sidebar
    for label in ("تنظیمات", "تحلیل فروش", "پشتیبان‌ها", "گزارش عملیات", "مشتریان",
                  "محصولات و موجودی", "داشبورد", "کارکنان", "تطبیق کارت‌خوان"):
        assert label not in sidebar, label


def test_a_manager_keeps_their_doors_and_loses_the_owners(client, db_session):
    user, password = _staff(db_session, "nav-manager-side", "manager")
    _session_as(client, user, password)
    sidebar = _sidebar(client.get("/admin").text)

    for label in ("محصولات و موجودی", "مشتریان", "کمپین پیامکی", "پیامک", "داشبورد",
                  "سود و زیان", "صندوق", "هزینه‌ها", "تأمین‌کنندگان", "چک‌ها",
                  "پرو مجازی", "تطبیق کارت‌خوان"):
        assert label in sidebar, label
    for label in ("تحلیل فروش", "پشتیبان‌ها", "گزارش عملیات", "تنظیمات", "کارکنان",
                  "اطلاعات مالک", "دفتر رویدادها", "ظاهر فروشگاه"):
        assert label not in sidebar, label


def test_an_owner_is_still_offered_every_door(client, db_session):
    """Gating must not quietly drop a page the owner had all along."""
    user, password = _staff(db_session, "nav-owner-side", "owner")
    _session_as(client, user, password)
    html = client.get("/admin").text
    sidebar = _sidebar(html)

    for label in LABELS:
        assert label in sidebar, label
    assert len(_nav_hrefs(html)) == len(ALL_ITEMS)


def test_the_shell_draws_the_list_it_is_handed():
    """No page above the till is written into the nav, so a template edit cannot
    hand a role a link the role check would refuse."""
    nav_markup = _sidebar(BASE_HTML)
    # The nav is generated in full — no destination is written into it by hand,
    # so a template edit cannot put a link back for a role that may not open it.
    assert 'href="/admin' not in nav_markup, nav_markup
    assert 'href="/sales' not in nav_markup, nav_markup
    assert "{% for section in nav_sections %}" in nav_markup
    assert "{% for item in section['items'] %}" in nav_markup
    # The topbar's dashboard button is the one hand-written admin link, and it
    # carries its own check — a guard is what matters, not the absence of a path.
    assert "{% if nav_home == '/admin' %}" in BASE_HTML


def test_the_login_page_has_no_doors_to_offer(client):
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert _nav_hrefs(page.text) == []
    assert "topbar-admin" not in page.text


# ── the topbar ───────────────────────────────────────────────────────────────

def test_a_cashier_gets_no_topbar_dashboard_button(client, db_session):
    user, password = _staff(db_session, "nav-top-cashier", "cashier")
    _session_as(client, user, password)
    assert "topbar-admin" not in client.get("/sales/new").text


def test_no_page_a_cashier_can_open_offers_an_admin_door(client, db_session):
    """The sidebar is not the only place a link is drawn. The invoice and the sales
    list each carried a داشبورد button pointing at /admin — a refusal for the very
    role reading them — so this opens every page the till grants, not just the shell."""
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    assert _confirm_sale(client, basket).status_code == 200
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()

    cashier, password = _staff(db_session, "nav-invoice-cashier", "cashier")
    _session_as(client, cashier, password)

    for path in ("/sales/new", "/sales/", f"/sales/invoice/{sale.id}"):
        page = client.get(path)
        assert page.status_code == 200, path
        # No hand-written door to the admin panel, on any page the till opens.
        assert 'href="/admin' not in page.text, path
        assert 'data-nav="/admin' not in page.text, path

    # …while whoever may open a dashboard keeps their button, and it works.
    manager, password = _staff(db_session, "nav-invoice-manager", "manager")
    _session_as(client, manager, password)
    for path in ("/sales/", f"/sales/invoice/{sale.id}"):
        assert 'href="/admin"' in client.get(path).text, path
    assert client.get("/admin").status_code == 200


def test_a_cashier_page_nav_keeps_its_own_way_forward(client, db_session):
    """Gating one button must not empty the footer nav."""
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    assert _confirm_sale(client, basket).status_code == 200

    cashier, password = _staff(db_session, "nav-forward", "cashier")
    _session_as(client, cashier, password)
    for path in ("/sales/", "/sales/new"):
        assert 'href="/sales/new"' in client.get(path).text, path


def test_the_topbar_button_is_the_dashboard_and_says_so(client, db_session):
    """It pointed at /admin while labelled «تنظیمات» — a wrong name for a page
    most people can open, and a refusal for those who cannot."""
    user, password = _staff(db_session, "nav-top-manager", "manager")
    _session_as(client, user, password)
    page = client.get("/admin").text
    assert 'class="topbar-admin"' in page
    assert "داشبورد</span>" in page


# ── where a login lands ──────────────────────────────────────────────────────

@pytest.mark.parametrize("role,expected", [
    ("cashier", "/sales/new"), ("manager", "/admin"), ("owner", "/admin"),
])
def test_a_login_lands_on_a_page_that_role_can_open(client, db_session, role, expected):
    user, password = _staff(db_session, f"nav-home-{role}", role)
    response = client.post("/admin/login", data={
        "username": user.username, "password": password,
        "csrf_token": csrf_token(client),
    }, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == expected
    assert home_for(role) == expected
    assert client.get(expected).status_code == 200


# ── the refusal is a page ────────────────────────────────────────────────────

def test_an_owner_only_page_answers_a_manager_with_a_page(client, db_session):
    manager, password = _staff(db_session, "nav-refused", "manager")
    _session_as(client, manager, password)

    response = client.get("/admin/settings", follow_redirects=False)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")

    text = response.text
    assert "این بخش برای شما باز نیست" in text
    assert "مدیر" in text                       # names the role that was refused
    assert "بازگشت به داشبورد" in text            # and offers a way out
    assert '{"detail"' not in text               # not the payload it used to be
    # The sidebar on the refusal is still the manager's own, not the owner's.
    assert 'href="/admin/settings" data-nav' not in text
    assert 'href="/admin/products" data-nav' in text


def test_a_wrong_address_under_admin_is_a_page_too(client, db_session):
    owner, password = _staff(db_session, "nav-missing", "owner")
    _session_as(client, owner, password)

    response = client.get("/admin/no-such-page-here", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "این صفحه پیدا نشد" in response.text
    # The framework's own English sentence is not printed at the shopkeeper.
    assert "Not Found" not in response.text


def test_a_refusal_on_a_form_post_is_a_page_as_well(client, db_session):
    """The reset button is the old example: a manager pressed it on the dashboard
    and got raw JSON back."""
    manager, password = _staff(db_session, "nav-refused-post", "manager")
    _session_as(client, manager, password)

    response = client.post("/admin/reset-database",
                           data={"csrf_token": csrf_token(client, "/sales/new")},
                           follow_redirects=False)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "این بخش برای شما باز نیست" in response.text


def test_the_api_keeps_its_json_contract(client):
    """The phone and any script depend on JSON; only the shop's pages changed."""
    response = client.get("/api/no-such-endpoint", follow_redirects=False)
    assert response.status_code in (403, 404)
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]


def test_the_refusal_page_offers_a_way_back_that_role_can_take():
    assert "error-page" in ERROR_HTML
    assert "{{ nav_home }}" in ERROR_HTML
    assert "{{ nav_home_label }}" in ERROR_HTML
    # No payload is built in the template; the page prints what it is handed.
    assert "response." not in ERROR_HTML


def test_the_error_page_styles_come_from_theme_tokens():
    section = STYLE_CSS[STYLE_CSS.index("/* Error page (403 / 404)"):]
    section = section[:section.index("/* Danger zone */")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section), section[:200]
    for token in ("var(--card)", "var(--candy)", "var(--candy-dark)",
                  "var(--ink)", "var(--ink-soft)", "var(--radius)"):
        assert token in section, token
