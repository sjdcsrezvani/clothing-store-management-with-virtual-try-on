"""The sidebar as data: one list, and the role each door asks for.

The sidebar used to be twenty-five hand-written links in ``base.html``, three of
them wrapped in their own owner check and every other one open to all comers. So
a cashier was shown تحلیل فروش، تنظیمات، پشتیبان‌ها and گزارش عملیات, and
clicking any of them produced FastAPI's raw ``{"detail": …}`` in the browser,
because those pages are owner-only.

The list now lives here, beside the roles, and the shell draws only what this
module says the viewer may open. Every item states the ``min_role`` of the page
behind it, so the sidebar cannot offer a door that is bolted: a test opens each
drawn destination as the role it was drawn for and fails if any of them refuses.
That is the same contract the dashboard's cards keep, and it is why an item
without a role is a mistake rather than a convenience.

``icon`` is the sprite symbol in ``base.html`` and ``tone`` its colour class —
the same string for every item but the first, where the cart glyph wears the
sale's own colour.
"""
from __future__ import annotations

from services.security import role_allows


def _item(href: str, label: str, icon: str, key: str,
          min_role: str = "manager", *, tone: str | None = None) -> dict:
    return {"href": href, "label": label, "icon": icon, "tone": tone or icon,
            "key": key, "min_role": min_role}


# Reading order, and the same order the sidebar has always had. A section whose
# every item is hidden disappears with them, so nobody is shown a heading with
# nothing under it.
NAV_SECTIONS: tuple[dict, ...] = (
    {"label": "فروشگاه", "items": (
        _item("/sales/new", "فروش جدید", "cart", "sales", "cashier", tone="sale"),
        _item("/sales/", "تاریخچه فروش", "history", "sales-history", "cashier"),
        _item("/admin/products", "محصولات و موجودی", "box", "products"),
        _item("/admin/purchases", "خرید از عمده‌فروش", "truck", "purchases"),
        _item("/admin/inventory-movements", "دفتر انبار", "ledger", "inventory-movements"),
    )},
    {"label": "مشتریان", "items": (
        _item("/admin/customers", "مشتریان", "users", "customers"),
        _item("/admin/credit", "حساب نسیه", "credit", "credit"),
        _item("/admin/campaigns", "کمپین پیامکی", "campaign", "campaigns"),
        _item("/admin/sms", "پیامک", "sms", "sms"),
    )},
    {"label": "گزارش و مالی", "items": (
        _item("/admin", "داشبورد", "dashboard", "dashboard"),
        _item("/admin/analytics", "تحلیل فروش", "chart", "analytics", "owner"),
        _item("/admin/accounting", "سود و زیان", "accounting", "accounting"),
        _item("/admin/cashbox", "صندوق", "cash", "cashbox"),
        _item("/admin/expenses", "هزینه‌ها", "expenses", "expenses"),
        _item("/admin/suppliers", "تأمین‌کنندگان", "supplier", "suppliers"),
        _item("/admin/checks", "چک‌ها", "check", "checks"),
    )},
    {"label": "ابزارها", "items": (
        _item("/admin/try-on", "پرو مجازی", "dress", "tryon"),
        _item("/admin/backups", "پشتیبان‌ها", "backup", "backups", "owner"),
        _item("/admin/staff", "کارکنان", "staff", "staff", "owner"),
        _item("/admin/owner-profile", "اطلاعات مالک", "users", "owner-profile", "owner"),
        _item("/admin/events", "دفتر رویدادها", "logs", "events", "owner"),
        _item("/admin/settings/appearance", "ظاهر فروشگاه", "palette", "appearance", "owner"),
        _item("/admin/settings", "تنظیمات", "settings", "settings", "owner"),
        _item("/admin/pos-reconciliation", "تطبیق کارت‌خوان", "terminal", "pos-reconciliation"),
        _item("/admin/logs", "گزارش عملیات", "logs", "logs", "owner"),
    )},
)


def navigation_for(role: str | None) -> list[dict]:
    """The sections this role may open, with the empty ones dropped."""
    sections = []
    for section in NAV_SECTIONS:
        items = [item for item in section["items"] if role_allows(role, item["min_role"])]
        if items:
            sections.append({"label": section["label"], "items": items})
    return sections


def home_for(role: str | None) -> str:
    """Where this role belongs: the dashboard if it may open one, else the till.

    A cashier used to land on ``/admin`` after logging in and be refused by the
    page they had just been sent to; the shop's own home for that role is the
    counter.
    """
    return "/admin" if role_allows(role, "manager") else "/sales/new"


def home_label_for(role: str | None) -> str:
    return "داشبورد" if home_for(role) == "/admin" else "فروش جدید"
