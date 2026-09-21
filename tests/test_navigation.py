"""The shell: which doors a role is handed, what each page is called, and the trail.

The sidebar used to be a fixed list of twenty-five links every role could see, and
the page it landed on answered with FastAPI's raw JSON. Fixing that raised three
questions these tests ask, mostly by opening every door rather than by reading the
list:

* is a door drawn only for somebody who can open it?
* is every page named once — the menu, the tab, the heading and the last crumb?
* does the page you are on light up exactly one item, and the right one?

The last one is why the matching moved into Python: in the browser it highlighted
two items on ``/sales/new`` and on ``/admin/settings/appearance`` and nothing at
all on the pages filed under a section.
"""
import re
from pathlib import Path

import pytest

from models import Campaign, Customer, Product, ProductVariant, Purchase, StaffUser
from services.navigation import (
    NAV_SECTIONS,
    PARENTS,
    TOPBAR_ACTIONS,
    active_key_for,
    home_for,
    navigation_for,
    page_title_for,
    topbar_key_for,
    trail_for,
)
from services.security import ROLE_ORDER
from tests import ui
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff
from tests.test_sales_money import _confirm_sale, _make_variant

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = (ROOT / "templates" / "base.html").read_text()
ERROR_HTML = (ROOT / "templates" / "admin" / "error.html").read_text()
STYLE_CSS = (ROOT / "static" / "css" / "style.css").read_text()

ROLES = ("cashier", "manager", "owner")
ALL_ITEMS = [item for section in NAV_SECTIONS for item in section["items"]]
LABELS = [item["label"] for item in ALL_ITEMS]

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F]")
LATIN = re.compile(r"[A-Za-z]")
TITLE_BLOCK = re.compile(r"\{% block title %\}(.*?)\{% endblock %\}", re.S)
HEADING = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.S)

# Every path the app actually serves, in the shape ``active_key_for`` must own.
# Two entries carry no owner on purpose: the till and the dashboard are the
# topbar's actions, and a section entry must not light up for them.
KNOWN_PATHS = (
    "/admin", "/admin/", "/sales/new", "/sales/", "/sales/invoice/7",
    "/admin/products", "/admin/products/add", "/admin/products/5",
    "/admin/variants/3/edit", "/admin/barcodes/print", "/admin/settings/tags",
    "/admin/purchases", "/admin/purchases/3", "/admin/purchases/3/edit",
    "/admin/purchases/3/print", "/admin/customers", "/admin/customers/5",
    "/admin/credit", "/admin/credit/5", "/admin/credit/5/statement",
    "/admin/campaigns", "/admin/campaigns/add", "/admin/campaigns/4",
    "/admin/campaigns/4/edit", "/admin/sms", "/admin/sms/send",
    "/admin/sms/history", "/admin/sms/templates/new", "/admin/sms/templates/3/edit",
    "/admin/birthdays", "/admin/follow-ups", "/admin/tier-up", "/admin/tier-downgrades",
    "/admin/try-on", "/admin/try-on/saved", "/admin/settings",
    "/admin/settings/appearance", "/admin/staff", "/admin/staff/2/contract",
    "/admin/payroll/9/receipt", "/admin/backups", "/admin/backups/download",
    "/admin/accounting", "/admin/accounting/export", "/admin/cashbox",
    "/admin/cashbox/sessions/7",
    "/admin/expenses", "/admin/checks", "/admin/analytics", "/admin/suppliers",
    "/admin/inventory-movements", "/admin/pos-reconciliation", "/admin/events",
    "/admin/logs", "/admin/owner-profile",
)

# The pages a role may open, by the least role that may open them.
REACHABLE = {
    "cashier": ("/sales/new", "/sales/", "/admin/cashbox"),
    "manager": ("/admin", "/admin/products", "/admin/credit", "/admin/sms",
                "/admin/settings/tags"),
    "owner": ("/admin/settings", "/admin/settings/appearance", "/admin/birthdays",
              "/admin/tier-up", "/admin/logs"),
}


def _template_files():
    return sorted(list((ROOT / "templates" / "admin").rglob("*.html"))
                  + list((ROOT / "templates" / "sales").rglob("*.html")))


def _sidebar(html):
    start = html.index('<nav class="sidebar-nav">')
    return html[start:html.index("</nav>", start)]


def _topbar(html):
    start = html.index('class="topbar-actions"')
    return html[start:html.index("</header>", start)]


def _nav_hrefs(html):
    return re.findall(r'<a href="([^"]+)" data-nav="', _sidebar(html))


def _active_keys(html):
    return re.findall(r'data-nav="([^"]+)" class="active" aria-current="page"', _sidebar(html))


def _current_in_topbar(html):
    return re.findall(r'class="(?:quick-sale|topbar-admin)"\s+aria-current="page"', _topbar(html))


def _h1(html):
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()


def _crumb_labels(html):
    match = re.search(r'<nav class="breadcrumb".*?</nav>', html, re.S)
    if not match:
        return []
    labels = re.findall(r"<(?:a|span)[^>]*>(.*?)</(?:a|span)>", match.group(0), re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", label)).strip() for label in labels]


def _make_path_data(db_session):
    """One record of each kind, so every detail page can be opened for real."""
    product, variant = _make_variant(db_session, price=100_000, stock=5)
    customer = Customer(phone="09120000009", first_name="سارا", referral_code="000009")
    purchase = Purchase(total_cost=5_000, note="تست")
    campaign = Campaign(name="کمپین تست", code="TESTNAV", discount_percent=10)
    staff = StaffUser(username="nav-contract", password_hash="x", role="cashier")
    db_session.add_all([customer, purchase, campaign, staff])
    db_session.commit()
    return {"product": product, "variant": variant, "customer": customer,
            "purchase": purchase, "campaign": campaign, "staff": staff}


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


def test_the_categories_are_the_five_the_shop_reads_by():
    assert [section["label"] for section in NAV_SECTIONS] == [
        "فروش", "کالا و انبار", "مشتریان و باشگاه", "مالی", "مدیریت",
    ]
    # No category is a junk drawer: the old «ابزارها» held nine unrelated things.
    assert max(len(section["items"]) for section in NAV_SECTIONS) <= 6
    # Suppliers sit with the job that uses them, not under the money.
    supplies = {item["key"] for item in
                next(s for s in NAV_SECTIONS if s["label"] == "کالا و انبار")["items"]}
    assert {"suppliers", "purchases"} <= supplies


def test_a_role_is_offered_exactly_the_doors_it_may_open():
    for role in ROLES:
        offered = {(section["label"], item["href"])
                   for section in navigation_for(role) for item in section["items"]}
        for section in NAV_SECTIONS:
            for item in section["items"]:
                wanted = ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(item["min_role"], 99)
                assert ((section["label"], item["href"]) in offered) is wanted, (role, item)


def test_a_section_with_nothing_left_in_it_is_not_printed():
    """Nobody is shown a heading with no items under it, or a blank one."""
    # «مالی» survives for a cashier because the till is theirs: the section is
    # drawn with one item («صندوق») rather than disappearing with the rest.
    assert [section["label"] for section in navigation_for("cashier")] == ["فروش", "مالی"]
    for role in ROLES:
        for section in navigation_for(role):
            assert section["items"], section["label"]
            assert section["label"]


def test_the_global_actions_are_the_topbars_and_not_a_section():
    """The till and the dashboard belong to no category, so they are in neither."""
    topbar_hrefs = {action["href"] for action in TOPBAR_ACTIONS}
    assert topbar_hrefs == {"/sales/new", "/admin"}
    assert not topbar_hrefs & {item["href"] for item in ALL_ITEMS}


# ── which page is current ────────────────────────────────────────────────────

def test_every_known_path_has_exactly_one_owner_and_one_name():
    for path in KNOWN_PATHS:
        key = active_key_for(path)
        title = page_title_for(path)
        assert title, path
        if path in ("/admin", "/admin/", "/sales/new"):
            assert key is None, path          # the topbar's own, not a section entry
        else:
            assert key in {item["key"] for item in ALL_ITEMS}, (path, key)


def test_the_till_and_the_dashboard_do_not_light_up_a_section_entry():
    """Both used to highlight «تاریخچه فروش» — the till is not the sales list."""
    assert active_key_for("/sales/new") is None
    assert active_key_for("/admin") is None
    assert active_key_for("/admin/") is None
    assert topbar_key_for("/sales/new") == "sales"
    assert topbar_key_for("/admin/") == "dashboard"


def test_an_owner_only_child_never_parents_to_a_page_its_roles_cannot_open():
    """A crumb is a link, so a parent must be openable by everyone who can open it."""
    role_of = {item["href"]: item for item in ALL_ITEMS}
    for prefix, key, _name in PARENTS:
        parent = next(item for item in ALL_ITEMS if item["key"] == key)
        child_role = next((item["min_role"] for item in ALL_ITEMS
                           if path_owns(prefix, item["href"])), None)
        if child_role is None:
            continue
        # The parent must be no stricter than the child: /admin/settings/tags is a
        # manager page and parents to the catalogue, not to the owner-only settings.
        assert ROLE_ORDER[parent["min_role"]] <= ROLE_ORDER[child_role], (prefix, key)
    assert role_of  # the map above is only meaningful if items exist


def path_owns(prefix, href):
    if prefix.endswith("/"):
        return href.startswith(prefix)
    return href == prefix or href.startswith(prefix + "/")


def test_the_trail_starts_at_a_section_and_ends_at_the_page():
    crumbs = trail_for("/admin/settings/appearance")
    assert [crumb["href"] for crumb in crumbs] == [None, "/admin/settings", None]
    assert crumbs[0]["label"] == "مدیریت"
    assert crumbs[-1]["label"] == "ظاهر فروشگاه"
    # A root of its own draws no breadcrumb at all.
    assert trail_for("/admin") == []
    assert trail_for("/sales/new") == []


def test_a_dynamic_title_overrides_the_crumb_it_ends():
    """A page that renames itself names its own crumb too."""
    crumbs = trail_for("/admin/customers/5", "سارا")
    assert [crumb["label"] for crumb in crumbs] == ["مشتریان و باشگاه", "مشتریان", "سارا"]


# ── the pages a role actually gets ───────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_every_door_the_sidebar_draws_opens_for_whoever_it_drew_it_for(client, db_session, role):
    """The whole point: a link on the page must not be a refusal in disguise."""
    user, password = _staff(db_session, f"nav-open-{role}", role)
    _session_as(client, user, password)

    hrefs = _nav_hrefs(client.get(home_for(role)).text)
    assert hrefs, f"the {role} sidebar drew nothing"

    for href in hrefs:
        response = client.get(href, follow_redirects=False)
        assert response.status_code not in (403, 404), (
            f"the {role} sidebar offered {href} and the page refused it")


@pytest.mark.parametrize("role", ROLES)
def test_exactly_one_item_is_current_on_every_page_a_role_can_open(client, db_session, role):
    data = _make_path_data(db_session)
    user, password = _staff(db_session, f"nav-current-{role}", role)
    _session_as(client, user, password)

    paths = list(REACHABLE[role]) + [
        f"/admin/customers/{data['customer'].id}",
        f"/admin/credit/{data['customer'].id}",
        f"/admin/products/{data['product'].id}",
        f"/admin/variants/{data['variant'].id}/edit",
        f"/admin/purchases/{data['purchase'].id}",
        f"/admin/campaigns/{data['campaign'].id}",
    ]
    for path in paths:
        page = client.get(path, follow_redirects=False)
        if page.status_code != 200:
            continue
        current = _active_keys(page.text)
        topbar = _current_in_topbar(page.text)
        assert len(current) + len(topbar) <= 1, (role, path, current, topbar)
        expected = active_key_for(page.request.url.path)
        if expected:
            assert current == [expected], (role, path, current, expected)
        elif page.request.url.path not in ("/admin", "/admin/"):
            assert current == [], (role, path, current)


@pytest.mark.parametrize("role,path,key", [
    ("owner", "/admin/settings/appearance", "settings"),
    ("owner", "/admin/birthdays", "sms"),
    ("owner", "/admin/follow-ups", "sms"),
    ("owner", "/admin/tier-up", "customers"),
    ("owner", "/admin/tier-downgrades", "customers"),
    ("manager", "/admin/barcodes/print", "products"),
    ("manager", "/admin/settings/tags", "products"),
    ("cashier", "/sales/", "sales-history"),
])
def test_the_pages_that_used_to_light_up_wrong_are_right_now(client, db_session, role, path, key):
    user, password = _staff(db_session, f"nav-fix-{key}-{role}", role)
    _session_as(client, user, password)
    page = client.get(path)
    assert page.status_code == 200, path
    assert _active_keys(page.text) == [key], path


def test_a_cashier_is_not_offered_a_manager_or_owner_door(client, db_session):
    user, password = _staff(db_session, "nav-cashier-side", "cashier")
    _session_as(client, user, password)
    sidebar = _sidebar(client.get("/sales/new").text)

    assert "تاریخچه فروش" in sidebar
    for label in ("تنظیمات", "تحلیل فروش", "پشتیبان‌ها", "گزارش عملیات", "مشتریان",
                  "محصولات و موجودی", "داشبورد", "کارکنان و حقوق", "تطبیق کارت‌خوان",
                  "حساب نسیه", "پرو مجازی", "تأمین‌کنندگان"):
        assert label not in sidebar, label


def test_a_manager_keeps_their_doors_and_loses_the_owners(client, db_session):
    user, password = _staff(db_session, "nav-manager-side", "manager")
    _session_as(client, user, password)
    sidebar = _sidebar(client.get("/admin").text)

    for label in ("تاریخچه فروش", "پرو مجازی", "محصولات و موجودی", "خرید از عمده‌فروش",
                  "تأمین‌کنندگان", "دفتر انبار", "مشتریان", "حساب نسیه", "کمپین پیامکی",
                  "پیامک", "صندوق", "هزینه‌ها", "چک‌ها", "سود و زیان", "تطبیق کارت‌خوان"):
        assert label in sidebar, label
    for label in ("تحلیل فروش", "پشتیبان‌ها", "گزارش عملیات", "تنظیمات", "کارکنان و حقوق",
                  "اطلاعات مالک", "دفتر رویدادها"):
        assert label not in sidebar, label


def test_an_owner_is_still_offered_every_door(client, db_session):
    """Gating must not quietly drop a page the owner had all along."""
    user, password = _staff(db_session, "nav-owner-side", "owner")
    _session_as(client, user, password)
    html = client.get("/admin").text

    for label in LABELS:
        assert label in _sidebar(html), label
    assert len(_nav_hrefs(html)) == len(ALL_ITEMS)


def test_the_children_the_sidebar_cannot_reach_still_name_a_parent():
    """Everything below a destination has an owner, or nothing would light up."""
    reachable = {item["href"].rstrip("/") for item in ALL_ITEMS}
    for path in KNOWN_PATHS:
        key = active_key_for(path)
        if key is None:
            continue
        assert key in {item["key"] for item in ALL_ITEMS}, path
    assert reachable


# ── one name per page ────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["manager", "owner"])
def test_the_menu_the_tab_the_heading_and_the_crumb_agree(client, db_session, role):
    user, password = _staff(db_session, f"nav-name-{role}", role)
    _session_as(client, user, password)

    for item in ALL_ITEMS:
        if ROLE_ORDER[role] < ROLE_ORDER[item["min_role"]]:
            continue
        page = client.get(item["href"])
        assert page.status_code == 200, item["href"]
        assert _h1(page.text) == item["label"], item["href"]
        labels = _crumb_labels(page.text)
        assert labels, item["href"]
        assert labels[-1] == item["label"], item["href"]
        # …and the tab, which the shell now names from the same registry.
        assert f"<title>{item['label']} — " in page.text, item["href"]


def test_a_breadcrumb_link_always_opens_for_the_role_shown_it(client, db_session):
    data = _make_path_data(db_session)
    owner, password = _staff(db_session, "nav-trail", "owner")
    _session_as(client, owner, password)

    paths = [f"/admin/products/{data['product'].id}",
             f"/admin/customers/{data['customer'].id}",
             f"/admin/purchases/{data['purchase'].id}",
             f"/admin/campaigns/{data['campaign'].id}",
             f"/admin/staff/{data['staff'].id}/contract",
             "/admin/sms/history", "/admin/tier-up", "/admin/settings/appearance"]
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200, path
        match = re.search(r'<nav class="breadcrumb".*?</nav>', page.text, re.S)
        assert match, path
        for href in re.findall(r'<a href="([^"]+)"', match.group(0)):
            assert client.get(href, follow_redirects=False).status_code not in (403, 404), (
                f"a crumb on {path} offered {href} and the page refused it")
        # The last crumb is where you already are, so it is not a link.
        assert "</a></nav>" not in match.group(0)


def test_a_root_of_its_own_draws_no_breadcrumb(client, db_session):
    owner, password = _staff(db_session, "nav-root", "owner")
    _session_as(client, owner, password)
    assert 'class="breadcrumb"' not in client.get("/admin").text


# ── the shell draws what it is handed ────────────────────────────────────────

def test_the_shell_draws_the_list_it_is_handed():
    """No destination is written into the nav, so a template edit cannot hand a
    role a link the role check would refuse."""
    nav_markup = _sidebar(BASE_HTML)
    assert 'href="/admin' not in nav_markup, nav_markup
    assert 'href="/sales' not in nav_markup, nav_markup
    assert "{% for section in nav_sections %}" in nav_markup
    assert "{% for item in section['items'] %}" in nav_markup
    # Which item is current is decided in Python, not by matching prefixes here.
    assert "item.key == active_nav_key" in nav_markup
    assert "indexOf" not in BASE_HTML
    assert "{% for action in topbar_actions %}" in BASE_HTML


def test_the_sidebar_sections_are_real_groups():
    """A heading over a list is a group; over bare links it reads as decoration."""
    nav_markup = _sidebar(BASE_HTML)
    assert '<ul aria-labelledby="nav-section-' in nav_markup
    assert 'id="nav-section-{{ loop.index }}"' in nav_markup
    assert "aria-current=\"page\"" in nav_markup


def test_the_login_page_has_no_doors_to_offer(client):
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert _nav_hrefs(page.text) == []
    assert "topbar-admin" not in page.text


# ── the topbar ───────────────────────────────────────────────────────────────

def test_the_topbar_is_where_the_till_and_the_dashboard_live(client, db_session):
    manager, password = _staff(db_session, "nav-top-manager", "manager")
    _session_as(client, manager, password)

    tank = client.get("/sales/new").text
    assert _current_in_topbar(tank), "the till is not marked current"
    assert _active_keys(tank) == [], "the till lit up a section entry"
    assert _active_keys(client.get("/admin").text) == []
    assert _current_in_topbar(client.get("/admin").text)


def test_a_cashier_gets_no_topbar_dashboard_button(client, db_session):
    user, password = _staff(db_session, "nav-top-cashier", "cashier")
    _session_as(client, user, password)
    assert "topbar-admin" not in client.get("/sales/new").text


def test_no_page_a_cashier_can_open_offers_an_admin_door(client, db_session):
    """The invoice and the sales list each carried a داشبورد button pointing at
    /admin — a refusal for the very role reading them.

    The rule is «no door that refuses», not «no door under /admin»: the till is
    a cashier's page now, so it is offered to them and opens for them, while
    everything else under /admin still turns them away.
    """
    _, variant = _make_variant(db_session, price=100_000, stock=5)
    basket = [{"variant_id": variant.id, "product_id": variant.product_id,
               "unit_price": 100_000, "quantity": 1, "total_price": 100_000}]
    assert _confirm_sale(client, basket).status_code == 200
    from models import Sale
    sale = db_session.query(Sale).order_by(Sale.id.desc()).first()

    cashier, password = _staff(db_session, "nav-invoice-cashier", "cashier")
    _session_as(client, cashier, password)

    for path in ("/sales/new", "/sales/", f"/sales/invoice/{sale.id}"):
        page = client.get(path)
        assert page.status_code == 200, path
        for href in sorted(set(re.findall(r'href="(/admin[^"]*)"', page.text))):
            # Followed, not merely read: a door drawn on a cashier's page has to
            # open for the cashier, which is the defect this test was born from.
            assert client.get(href).status_code == 200, (path, href)

    manager, password = _staff(db_session, "nav-invoice-manager", "manager")
    _session_as(client, manager, password)
    assert client.get("/admin").status_code == 200


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


def test_a_role_change_reaches_the_sidebar_without_a_new_login(client, db_session):
    """The guard is the authority, so the role it confirms is what the shell reads.

    Before this, a promoted manager kept a cashier's sidebar while the dashboard
    drew an owner's cards: one page showing two different viewers.
    """
    user, password = _staff(db_session, "nav-promote", "cashier")
    _session_as(client, user, password)
    assert "پشتیبان‌ها" not in _sidebar(client.get("/sales/new").text)

    user.role = "owner"
    db_session.commit()

    page = client.get("/sales/new").text
    assert "topbar-admin" in page
    assert "پشتیبان‌ها" in _sidebar(page)


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
    assert 'data-nav="settings"' not in _sidebar(text)
    assert 'data-nav="products"' in _sidebar(text)


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
    manager, password = _staff(db_session, "nav-refused-post", "manager")
    _session_as(client, manager, password)

    response = client.post("/admin/backup",
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
    # `--candy-dark` was the refusal's link colour; the derived ink is what text
    # uses now, and the hue is left to the bar beside it.
    for token in ("var(--card)", "var(--candy)", "var(--link-hover)",
                  "var(--ink)", "var(--ink-soft)", "var(--radius)"):
        assert token in section, token


# ── language ─────────────────────────────────────────────────────────────────

def test_no_emoji_survives_in_a_title_or_a_heading():
    """One icon language: the sprite. An emoji in a heading is a second one."""
    offenders = []
    for path in _template_files():
        text = path.read_text()
        for pattern in (TITLE_BLOCK, HEADING):
            for match in pattern.finditer(text):
                if EMOJI.search(match.group(1)):
                    offenders.append((path.name, re.sub(r"\s+", " ", match.group(1)).strip()))
    assert not offenders, offenders


def test_the_pages_are_named_in_persian():
    """The English eyebrows are gone, and nothing the shell names is Latin.

    The page-header eyebrow is retired — «تعهدات مالی» was the English
    «Financial commitments» written above the checks title, and the trail now
    says «مالی / چک‌ها» in its place — so what is worth guarding is not a list of
    phrases but that every name the shell prints is Persian. The section
    eyebrows a page still keeps are checked too.
    """
    text = "".join(path.read_text() for path in _template_files())
    for english in ("Financial commitments", "Reminder settings", "Issued checks",
                    "Products / Printing", "Store theme", '>Appearance<'):
        assert english not in text, english
    for persian in ("تنظیمات یادآوری", "چک‌های صادره", "محصولات و چاپ", "تم فروشگاه"):
        assert persian in text, persian
    for item in ALL_ITEMS:
        assert not LATIN.search(item["label"]), item["label"]
    for action in TOPBAR_ACTIONS:
        assert not LATIN.search(action["label"]), action["label"]
    for _prefix, _key, name in PARENTS:
        assert not LATIN.search(name), name


def test_a_role_is_named_once_wherever_it_is_shown():
    """«صندوقدار» is what the staff form assigns; the shell must not call it
    something else back to the person holding it."""
    from services.security import ROLE_LABELS
    assert ROLE_LABELS == {"cashier": "صندوقدار", "manager": "مدیر", "owner": "مالک"}
    staff_html = (ROOT / "templates" / "admin" / "staff.html").read_text()
    assert ">صندوقدار<" in staff_html


def test_every_page_uses_the_one_shared_header():
    """The three idioms are gone: a page composes the partial or it draws nothing."""
    for path in _template_files():
        text = path.read_text()
        composed = ui.HEADER_INCLUDE in text
        old_heading = ('class="page-heading' in text or 'class="page-header"' in text
                       or 'style="display: flex; align-items: center; gap: 0.6rem; margin-bottom' in text)
        assert composed or not old_heading, path.name
        # A page may not hand-write a back-to-dashboard button either.
        assert '<nav class="admin-nav"' not in text, path.name
        assert not re.search(r'<h2[^>]*>\s*(?:بازگشت|<)', text), path.name


def test_the_header_partial_keeps_the_contract_pages_rely_on():
    assert ui.PAGE_HEADING in ui.HEADER or "page-heading" in ui.HEADER
    assert 'class="admin-nav"' in ui.HEADER
    assert "<h1>" in ui.HEADER
    assert ui.BREADCRUMB_INCLUDE in ui.HEADER
    # The crumb is built from the same title the heading prints.
    assert "trail_for(request.url.path, page_title)" in ui.BREADCRUMB
    assert 'aria-current="page"' in ui.BREADCRUMB
