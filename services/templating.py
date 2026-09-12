"""Single shared Jinja2Templates instance so every router gets the CSRF token,
store branding and the customer-module flags injected automatically.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from services._common import (
    birthday_display,
    birthday_form_value,
    days_until_jalali_birthday,
    jalali_age,
)
from services.customers import customer_context_processor
from services.security import csrf_context_processor
from services.store import store_context_processor
from services.themes import get_theme


def theme_context_processor(request) -> dict:
    return {"theme": get_theme()}


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
    ],
)

# Birthday helpers every template can reach without a route passing them along.
# `birthday_form_value` rebuilds a field value, so a stored MM-DD (with or
# without a year) always renders as something the date picker can read back.
templates.env.globals.update(
    birthday_form_value=birthday_form_value,
    birthday_display=birthday_display,
    jalali_age=jalali_age,
    days_until_birthday=days_until_jalali_birthday,
)
