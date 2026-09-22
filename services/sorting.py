"""Column sorting for ledger lists, one convention everywhere.

A query param is honoured only when it names a key the page allows — the same
rule the credit ledger's DEBT_SORTS and the SMS manager's TEMPLATE_SORTS
already enforce. Unknown keys and directions fall back to the page's default,
so a hand-typed ?sort=anything answers the default list, never an error.
"""
from __future__ import annotations

DIRECTIONS = ("asc", "desc")


def parse_sort(params, allowed: dict[str, str], default: str) -> tuple[str, str]:
    """Split ?sort=&dir= into a known key and direction.

    ``allowed`` maps key → default direction for that key (dates usually
    descend, names ascend). ``default`` is the key used when the query names
    nothing known.
    """
    get = params.get if hasattr(params, "get") else (lambda key, fallback="": fallback)
    key = (get("sort", "") or "").strip()
    if key not in allowed:
        key = default
    direction = (get("dir", "") or "").strip()
    if direction not in DIRECTIONS:
        direction = allowed[key]
    return key, direction


def toggle_direction(current_key: str, current_dir: str, key: str, default_dir: str) -> str:
    """The direction a header link should ask for: flip when already sorted
    by this key, otherwise the key's own default."""
    if key == current_key:
        return "asc" if current_dir == "desc" else "desc"
    return default_dir
