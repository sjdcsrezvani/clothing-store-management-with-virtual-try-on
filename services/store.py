"""Store profile (branding) — single source of truth for the shop's name,
tagline, Instagram handle and invoice footer. Values live in the `settings`
table so every shop can brand its own install; cached in memory for 60s and
invalidated when settings are saved.

Templates read `store.name`, `store.tagline`, `store.instagram`,
`store.footer` via a Jinja2 context processor (see services/templating.py).
"""
import time

from database import SessionLocal

_CACHE_TTL_SECONDS = 60
_cache = {"at": 0.0, "data": None}

DEFAULT_STORE = {
    "name": "رای کیدز",
    "tagline": "فروشگاه پوشاک کودک",
    "instagram": "",
    "footer": "",
}


def _load(db=None) -> dict:
    from models import Settings

    close = db is None
    if db is None:
        db = SessionLocal()
    try:
        data = dict(DEFAULT_STORE)
        for key in data:
            row = db.query(Settings).filter(Settings.key == f"store_{key}").first()
            if row and row.value:
                data[key] = row.value
        return data
    finally:
        if close:
            db.close()


def get_store(db=None) -> dict:
    """Store profile as a dict, cached for 60 seconds."""
    now = time.time()
    if _cache["data"] is None or now - _cache["at"] > _CACHE_TTL_SECONDS:
        _cache["data"] = _load(db)
        _cache["at"] = now
    return _cache["data"]


def invalidate_store_cache() -> None:
    _cache["data"] = None
    _cache["at"] = 0.0


def store_context_processor(request) -> dict:
    return {"store": get_store()}
