"""Where every page lives, what it is called, and who may open it.

One registry answers four questions that used to be answered in four places and
disagreed with each other:

* **What does the sidebar show this role?** — :func:`navigation_for`.
* **Which item is the current page under?** — :func:`active_key_for`. The shell
  used to match this in JavaScript with ``path.indexOf(href) === 0``, which
  highlighted two items on ``/sales/new`` and on ``/admin/settings/appearance``
  and nothing at all on the ten pages below, because the first matching prefix
  won and no entry owned a detail page.
* **What is this page called?** — :func:`page_title_for`. The menu, the browser
  title and the ``<h1>`` all print this one string, so «تحلیل فروش» can no
  longer be «تحلیل مالی» on its own page.
* **What is the trail back?** — :func:`trail_for`, drawn as a breadcrumb.

Every item states the ``min_role`` of the page behind it, so the sidebar cannot
offer a door that is bolted. A crumb is a link, so ``PARENTS`` only ever points
at a page the child's own roles can open: ``/admin/settings/tags`` is a manager
route and therefore parents to محصولات و موجودی, not to the owner-only تنظیمات
page, which would have put a refusing link in a manager's breadcrumb.

``icon`` is the sprite symbol in ``base.html`` and ``tone`` its colour class.
``key`` is the stable identifier the shell marks active and the tests use.
"""
from __future__ import annotations

from services.security import role_allows


def _item(href: str, label: str, icon: str, key: str,
          min_role: str = "manager", *, tone: str | None = None) -> dict:
    return {"href": href, "label": label, "icon": icon, "tone": tone or icon,
            "key": key, "min_role": min_role}


# The two global actions live in the topbar, not in a section: the till is where
# a cashier spends the day and the dashboard is a landing page, and neither is a
# member of a category. Keeping them here means their names and their roles come
# from the same registry as everything else.
TOPBAR_ACTIONS: tuple[dict, ...] = (
    _item("/sales/new", "فروش جدید", "cart", "sales", "cashier", tone="sale"),
    _item("/admin", "داشبورد", "dashboard", "dashboard", "manager"),
)

# Reading order for a shopkeeper: what I sell, what I stock, who buys, the money,
# then the administration. A section whose every item is hidden disappears with
# them, so nobody is shown a heading over nothing.
NAV_SECTIONS: tuple[dict, ...] = (
    {"label": "فروش", "items": (
        _item("/sales/", "تاریخچه فروش", "history", "sales-history", "cashier"),
        _item("/admin/try-on", "پرو مجازی", "dress", "tryon"),
    )},
    {"label": "کالا و انبار", "items": (
        _item("/admin/products", "محصولات و موجودی", "box", "products"),
        _item("/admin/purchases", "خرید از عمده‌فروش", "truck", "purchases"),
        _item("/admin/suppliers", "تأمین‌کنندگان", "supplier", "suppliers"),
        _item("/admin/inventory-movements", "دفتر انبار", "ledger", "inventory-movements"),
    )},
    {"label": "مشتریان و باشگاه", "items": (
        _item("/admin/customers", "مشتریان", "users", "customers"),
        _item("/admin/credit", "حساب نسیه", "credit", "credit"),
        _item("/admin/campaigns", "کمپین پیامکی", "campaign", "campaigns"),
        _item("/admin/sms", "پیامک", "sms", "sms"),
    )},
    {"label": "مالی", "items": (
        _item("/admin/cashbox", "صندوق", "cash", "cashbox"),
        _item("/admin/expenses", "هزینه‌ها", "expenses", "expenses"),
        _item("/admin/checks", "چک‌ها", "check", "checks"),
        _item("/admin/accounting", "سود و زیان", "accounting", "accounting"),
        _item("/admin/analytics", "تحلیل فروش", "chart", "analytics", "owner"),
        _item("/admin/pos-reconciliation", "تطبیق کارت‌خوان", "terminal", "pos-reconciliation"),
    )},
    {"label": "مدیریت", "items": (
        _item("/admin/staff", "کارکنان و حقوق", "staff", "staff", "owner"),
        _item("/admin/settings", "تنظیمات", "settings", "settings", "owner"),
        _item("/admin/backups", "پشتیبان‌ها", "backup", "backups", "owner"),
        _item("/admin/events", "دفتر رویدادها", "logs", "events", "owner"),
        _item("/admin/logs", "گزارش عملیات", "logs", "logs", "owner"),
        _item("/admin/owner-profile", "اطلاعات مالک", "users", "owner-profile", "owner"),
    )},
)

# Pages the sidebar cannot reach, and the item each one belongs under. The prefix
# ends with a slash so it can never shadow the destination itself:
# `/admin/products/` owns the detail and form pages while `/admin/products`
# stays the list.
PARENTS: tuple[tuple[str, str, str], ...] = (
    ("/admin/products/add", "products", "افزودن محصول"),
    ("/admin/products/", "products", "محصول"),
    ("/admin/variants/", "products", "ویرایش تنوع"),
    ("/admin/barcodes/print", "products", "چاپ تگ محصولات"),
    ("/admin/settings/tags", "products", "تنظیمات تگ و بارکد"),
    ("/admin/purchases/", "purchases", "خرید"),
    ("/admin/customers/", "customers", "پرونده مشتری"),
    ("/admin/credit/", "credit", "نسیه"),
    ("/admin/campaigns/add", "campaigns", "کمپین جدید"),
    ("/admin/campaigns/", "campaigns", "کمپین"),
    ("/admin/sms/send", "sms", "ارسال پیامک"),
    ("/admin/sms/history", "sms", "تاریخچه پیامک"),
    ("/admin/sms/templates/new", "sms", "قالب جدید"),
    ("/admin/sms/templates/", "sms", "ویرایش قالب"),
    ("/admin/birthdays", "sms", "پیامک تولد"),
    ("/admin/follow-ups", "sms", "پیگیری مشتریان"),
    ("/admin/tier-up", "customers", "ارتقای سطح"),
    ("/admin/tier-downgrades", "customers", "کاهش سطح"),
    ("/admin/settings/appearance", "settings", "ظاهر فروشگاه"),
    ("/admin/try-on/saved", "tryon", "تصاویر ذخیره‌شده"),
    ("/admin/staff/", "staff", "کارمند"),
    ("/admin/payroll/", "staff", "رسید پرداخت حقوق"),
    ("/admin/backups/download", "backups", "دانلود پشتیبان"),
    ("/admin/accounting/export", "accounting", "خروجی"),
    ("/sales/invoice/", "sales-history", "فاکتور فروش"),
)


def _owns(path: str, prefix: str) -> bool:
    """Whether ``path`` is ``prefix`` or something under it.

    The boundary matters: ``/admin/products`` must not claim ``/admin/products-x``
    and ``/admin/settings`` must not claim the appearance page it does not own.
    """
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _flat() -> list[tuple[dict, str]]:
    """Every item with the section it is printed under."""
    return [(item, section["label"])
            for section in NAV_SECTIONS for item in section["items"]]


def _by_key(key: str) -> dict | None:
    for item, _section in _flat():
        if item["key"] == key:
            return item
    for action in TOPBAR_ACTIONS:
        if action["key"] == key:
            return action
    return None


def _norm(path: str) -> str:
    """A path without its trailing slash, so ``/admin`` and ``/admin/`` agree.

    The dashboard is served at ``/admin/`` and linked at ``/admin``; without this
    the two spellings answered differently.
    """
    return path.rstrip("/") or "/"


def _exact_map() -> dict[str, tuple[str, str]]:
    """Every destination, keyed by its own path.

    Destinations match exactly. Only ``PARENTS`` matches by prefix — a landing
    page must not claim everything filed beneath it, or an unknown address under
    ``/admin`` would answer as the dashboard.
    """
    rows = {}
    for item, _section in _flat():
        rows[_norm(item["href"])] = (item["key"], item["label"])
    for action in TOPBAR_ACTIONS:
        rows[_norm(action["href"])] = (action["key"], action["label"])
    return rows


def _prefixes() -> list[tuple[int, str, str, str]]:
    """``(prefix length, prefix, owning key, page name)``, longest prefix first."""
    rows = [(len(prefix), prefix, key, name) for prefix, key, name in PARENTS]
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows


_EXACT = _exact_map()
_PREFIXES = _prefixes()
_TOPBAR_HREFS = {_norm(action["href"]) for action in TOPBAR_ACTIONS}


def navigation_for(role: str | None) -> list[dict]:
    """The sections this role may open, with the empty ones dropped."""
    sections = []
    for section in NAV_SECTIONS:
        items = [item for item in section["items"] if role_allows(role, item["min_role"])]
        if items:
            sections.append({"label": section["label"], "items": items})
    return sections


def topbar_for(role: str | None) -> list[dict]:
    """The global actions this role may use, in reading order."""
    return [action for action in TOPBAR_ACTIONS if role_allows(role, action["min_role"])]


def _match(path: str) -> tuple[str, str] | None:
    exact = _EXACT.get(_norm(path))
    if exact is not None:
        return exact
    for _length, prefix, key, name in _PREFIXES:
        if _owns(path, prefix):
            return key, name
    return None


def active_key_for(path: str) -> str | None:
    """Which sidebar item owns this path, or ``None`` for the topbar's pages.

    ``None`` is a real answer, not a failure: the till and the dashboard are the
    topbar's, and they must not light up a section entry as well.
    """
    if _norm(path) in _TOPBAR_HREFS:
        return None
    found = _match(path)
    return found[0] if found else None


def page_title_for(path: str, title: str | None = None) -> str | None:
    """What this page is called — the single name menu, title and heading share."""
    if title:
        return title
    found = _match(path)
    return found[1] if found else None


def topbar_key_for(path: str) -> str | None:
    """Which topbar action, if any, is the page being shown."""
    normalised = _norm(path)
    return next((action["key"] for action in TOPBAR_ACTIONS
                 if _norm(action["href"]) == normalised), None)


def page_icon_for(path: str) -> str | None:
    """The sprite symbol this page wears, taken from the item that owns it."""
    key = active_key_for(path)
    if key is None:
        key = next((a["key"] for a in TOPBAR_ACTIONS if _norm(a["href"]) == _norm(path)), None)
    item = _by_key(key) if key else None
    return item["icon"] if item else None


def trail_for(path: str, title: str | None = None) -> list[dict]:
    """The crumbs back to where this page lives.

    A section has no URL, so it is printed as plain context rather than given a
    link that would go nowhere. Returns an empty list when the page is a root of
    its own — a breadcrumb of one crumb tells the reader nothing.
    """
    name = page_title_for(path, title)
    key = active_key_for(path)
    item = _by_key(key) if key else None
    if item is None or name is None:
        return []

    section = next((label for entry, label in _flat() if entry["key"] == item["key"]), "")
    crumbs = [{"label": section, "href": None}]
    if name != item["label"]:
        crumbs.append({"label": item["label"], "href": item["href"]})
    crumbs.append({"label": name, "href": None})
    return crumbs


def home_for(role: str | None) -> str:
    """Where this role belongs: the dashboard if it may open one, else the till.

    A cashier used to land on ``/admin`` after logging in and be refused by the
    page they had just been sent to; the shop's own home for that role is the
    counter.
    """
    return "/admin" if role_allows(role, "manager") else "/sales/new"


def home_label_for(role: str | None) -> str:
    return "داشبورد" if home_for(role) == "/admin" else "فروش جدید"
