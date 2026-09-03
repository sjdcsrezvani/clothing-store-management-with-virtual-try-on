"""Single shared Jinja2Templates instance so every router gets the CSRF token
and store branding injected automatically."""
from fastapi.templating import Jinja2Templates

from services.security import csrf_context_processor
from services.store import store_context_processor
from services.themes import get_theme


def theme_context_processor(request) -> dict:
    return {"theme": get_theme()}

templates = Jinja2Templates(
    directory="templates",
    context_processors=[csrf_context_processor, store_context_processor, theme_context_processor],
)
