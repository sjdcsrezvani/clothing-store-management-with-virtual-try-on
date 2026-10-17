"""HTMX migration guard — the transport inventory only ever shrinks.

Every phase of the htmx migration deletes entries from FETCH_ALLOWLIST; the
close-out phase empties it of everything but the shell bridge itself. Two
tests keep the list honest in both directions: new hand-rolled fetch/XHR
code fails, and an entry whose file was converted fails until it is removed
— so the list can never rot into a lie.

The one tolerated speaker is static/js/htmx-shell.js: it is the sanctioned
bridge that sends the X-Requested-With marker by design. Everything else
must speak htmx.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent

_TRANSPORT = re.compile(r"\bfetch\s*\(|\bXMLHttpRequest\b")

# Files still allowed to speak fetch/XHR. Shrinks every htmx phase:
# P1 checkout, P2 poll islands, P3 list regions (list_nav), P4 boost,
# P5 phone app, P6 close-out.
FETCH_ALLOWLIST = {
    "static/js/htmx-shell.js",  # the bridge itself — permanent, by design
    "static/js/list_nav.js",
    "templates/admin/barcode_print.html",
    "templates/admin/pos_reconciliation.html",
    "templates/admin/product_form.html",
    "templates/admin/settings.html",
    "templates/admin/tryon.html",
    "templates/mobile/index.html",
    "templates/mobile/pair.html",
}


def _speaking_files() -> set:
    files = list((ROOT / "templates").rglob("*.html"))
    files += list((ROOT / "static" / "js").glob("*.js"))
    return {
        str(path.relative_to(ROOT))
        for path in files
        if _TRANSPORT.search(path.read_text(encoding="utf-8"))
    }


def test_no_new_hand_rolled_transport():
    fresh = _speaking_files() - FETCH_ALLOWLIST
    assert not fresh, f"use htmx, not fetch/XHR: {sorted(fresh)}"


def test_allowlist_never_rots():
    stale = FETCH_ALLOWLIST - _speaking_files()
    assert not stale, f"converted — remove from the list: {sorted(stale)}"


def test_htmx_foundation_in_place():
    vendor = (ROOT / "static" / "vendor" / "htmx.min.js").read_text(encoding="utf-8")
    assert 'version:"2.' in vendor
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "/static/vendor/htmx.min.js" in base
    assert "/static/js/htmx-shell.js" in base
    shell = (ROOT / "static" / "js" / "htmx-shell.js").read_text(encoding="utf-8")
    assert "htmx:configRequest" in shell
    assert "htmx:beforeSwap" in shell
