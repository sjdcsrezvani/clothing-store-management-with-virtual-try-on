"""Single shared Jinja2Templates instance so every router gets the CSRF token
and store branding injected automatically."""
from fastapi.templating import Jinja2Templates

from services.security import csrf_context_processor
from services.store import store_context_processor

templates = Jinja2Templates(
    directory="templates",
    context_processors=[csrf_context_processor, store_context_processor],
)
