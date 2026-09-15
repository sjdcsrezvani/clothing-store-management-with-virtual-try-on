"""Single shared Jinja2Templates instance so every router gets the CSRF token,
store branding and the customer-module flags injected automatically.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from services._common import (
    birthday_display,
    birthday_form_value,
    days_until_jalali_birthday,
    fmt,
    jalali_age,
    jalali_str,
)
from services.customers import customer_context_processor
from services.navigation import (
    active_key_for,
    home_for,
    home_label_for,
    navigation_for,
    page_icon_for,
    page_title_for,
    topbar_for,
    topbar_key_for,
    trail_for,
)
from services.security import ROLE_LABELS, csrf_context_processor
from services.store import store_context_processor
from services.themes import get_theme


def theme_context_processor(request) -> dict:
    return {"theme": get_theme()}


def navigation_context_processor(request) -> dict:
    """The shell, already filtered and decided for whoever is looking.

    The sidebar, the topbar's actions, which item is current, and what this page
    is called all come from :mod:`services.navigation`, so the menu, the browser
    title, the ``<h1>`` and the breadcrumb cannot disagree. The shell draws what
    it is handed, exactly as the dashboard does, so a template edit cannot add a
    link back for a role that may not open it.

    The role is read from the session, which :func:`services.security.require_role`
    keeps in step with the account it just admitted — the routes ask the account
    itself, and the guard writes the answer back for the pages that follow.
    """
    role = request.session.get("staff_role")
    path = request.url.path
    return {
        "nav_sections": navigation_for(role),
        "topbar_actions": topbar_for(role),
        "nav_home": home_for(role),
        "nav_home_label": home_label_for(role),
        # The single name this page is known by, unless the page overrides it for
        # a record it is showing (a customer's name, an invoice number).
        "page_title": page_title_for(path),
        "page_icon": page_icon_for(path),
        "active_nav_key": active_key_for(path),
        "active_topbar_key": topbar_key_for(path),
        "viewer_role_label": ROLE_LABELS.get(role or "", ""),
    }


def static_version_context_processor(request) -> dict:
    """Expose the stylesheet's mtime so the browser never caches stale CSS."""
    try:
        version = int(Path("static/css/style.css").stat().st_mtime)
    except OSError:
        version = 0
    return {"static_version": version}


templates = Jinja2Templates(
    directory="templates",
    context_processors=[
        csrf_context_processor,
        store_context_processor,
        customer_context_processor,
        theme_context_processor,
        static_version_context_processor,
        navigation_context_processor,
    ],
)

# Birthday helpers every template can reach without a route passing them along.
# `birthday_form_value` rebuilds a field value, so a stored MM-DD (with or
# without a year) always renders as something the date picker can read back.
templates.env.globals.update(
    # The two helpers every page needs. They were passed per route, which meant
    # any render that forgot one crashed the page — a date formatted with
    # `fmt` — so they live here once and a route can still override them.
    fmt=fmt,
    jalali_str=jalali_str,
    birthday_form_value=birthday_form_value,
    birthday_display=birthday_display,
    jalali_age=jalali_age,
    days_until_birthday=days_until_jalali_birthday,
    # The breadcrumb partial builds its own trail from the path and the title it
    # was handed, so a page that renames itself (a customer record) gets a trail
    # ending in that same name rather than a second, stale one.
    trail_for=trail_for,
)
