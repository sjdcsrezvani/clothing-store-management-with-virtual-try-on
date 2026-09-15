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
from services.navigation import home_for, home_label_for, navigation_for
from services.security import ROLE_LABELS, csrf_context_processor
from services.store import store_context_processor
from services.themes import get_theme


def theme_context_processor(request) -> dict:
    return {"theme": get_theme()}


def navigation_context_processor(request) -> dict:
    """The sidebar, already filtered for whoever is looking.

    The shell draws what it is handed, exactly as the dashboard does, so a
    template edit cannot add a link back for a role that may not open it. The
    role comes from the session — the same source the sidebar has always read —
    while the routes keep asking the account itself, which is the authority.
    """
    role = request.session.get("staff_role")
    return {
        "nav_sections": navigation_for(role),
        "nav_home": home_for(role),
        "nav_home_label": home_label_for(role),
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
)
