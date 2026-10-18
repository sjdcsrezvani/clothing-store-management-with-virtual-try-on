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


def test_body_boost_is_installed_with_reentry_dedupe():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'hx-boost="true"' in base          # the whole shell navigates in place
    # htmx and the shell load blocking, ahead of every body script: boosted
    # body re-runs need the dedupe installed before the first boot binds.
    head = base[: base.index("<body")]
    for pair in ("/static/vendor/htmx.min.js", "/static/js/htmx-shell.js"):
        line = [l for l in head.splitlines() if pair in l][0]
        assert "defer" not in line, f"{pair} must be blocking (loads before body scripts)"
    shell = (ROOT / "static" / "js" / "htmx-shell.js").read_text(encoding="utf-8")
    # The re-entry dedupe: re-running an inline script must not add a second
    # document-level listener, or dialog gates open in chorus.
    assert "EventTarget.prototype.addEventListener" in shell
    assert "String(handler)" in shell


def test_flash_adoption_survives_boosted_arrivals():
    # Soon-toast adoption is DOM-driven and the shell replays it per arrival.
    toast = (ROOT / "static" / "js" / "toast.js").read_text(encoding="utf-8")
    assert "adopt: adoptServerFlash" in toast
    shell = (ROOT / "static" / "js" / "htmx-shell.js").read_text(encoding="utf-8")
    assert "RaykidToast.adopt" in shell


def test_the_shell_adopts_the_csrf_token_the_server_names():
    """Login and logout clear the session and mint a new CSRF token; a boosted
    swap never replaces the head, so the meta would keep the old token and
    every meta-fed request would answer 403 in silence. The server names the
    current token on every HTML answer and the shell writes it into the meta;
    a field injected before a rotation is refreshed at submit, not left stale."""
    shell = (ROOT / "static" / "js" / "htmx-shell.js").read_text(encoding="utf-8")
    assert "htmx:afterOnLoad" in shell
    assert "X-CSRF-Token" in shell
    assert "meta.content = fresh" in shell
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "current.value = meta.content" in base


def test_html_answers_name_the_session_csrf_token(client):
    """The header the shell adopts: every HTML answer carries the session's
    current token, and the page landed on after a login rotation carries the
    new one. The browser check that found this proved a stale meta answers
    403 on the very next POST."""
    page = client.get("/admin/login")
    before = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    assert page.headers.get("x-csrf-token") == before

    landed = client.post("/admin/login",
                         data={"username": "owner", "password": "test-admin-pass",
                               "csrf_token": before},
                         follow_redirects=True)
    after = re.search(r'name="csrf-token" content="([^"]+)"', landed.text).group(1)
    assert after != before                                  # the session rotated
    assert landed.headers.get("x-csrf-token") == after      # and the answer names it
