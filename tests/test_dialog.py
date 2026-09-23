"""Dialog unification (Phase C): one pattern for every overlay surface.

The drawer keeps its tested script; the lightbox and the mobile capture
overlay join the same contract: dialog semantics, a fade instead of a
blink, Escape, and focus that returns to the opener.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_shell_mounts_the_dialog_helper_once():
    base = (ROOT / "templates" / "base.html").read_text()
    assert base.count("/static/js/dialog.js") == 1


def test_dialog_helper_served_with_the_full_contract(client):
    resp = client.get("/static/js/dialog.js")
    assert resp.status_code == 200
    for snippet in ("RaykidDialog", "data-dialog", "Escape", "Tab",
                    "aria-hidden", "opener", "focus"):
        assert snippet in resp.text, snippet


def test_drawer_overlay_fades_without_changing_its_coat():
    """The fade is new; the colour is not — the pixel probe measures the
    paper-ink coat, so only the tween may change."""
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert "body.sidebar-open .sidebar-overlay" in style
    assert "visibility: visible" in style
    assert "opacity: 1" in style
    assert "visibility: hidden" in style
    assert "opacity: 0" in style
    # The coat the probe photographs is untouched.
    assert "var(--paper-ink) 50%" in style


def test_lightboxes_are_dialogs_with_focus_return():
    for name in ("tryon.html", "tryon_saved.html"):
        source = (ROOT / "templates" / "admin" / name).read_text()
        assert 'data-dialog' in source, name
        assert 'role="dialog"' in source, name
        assert 'aria-modal="true"' in source, name
        assert "lightboxOpener" in source, name
        # Pins the keyboard-shell suite reads literally.
        assert "e.key === 'Escape'" in source, name
        assert "lightbox-close').focus()" in source, name


def test_mobile_overlay_is_a_dialog():
    mobile = (ROOT / "templates" / "mobile" / "index.html").read_text()
    assert 'role="dialog"' in mobile
    assert 'aria-modal="true"' in mobile
    assert "e.key === 'Escape'" in mobile
    assert "photoOpener" in mobile
    assert 'id="btn-cancel-photo"' in mobile


def test_dialog_motion_is_tokenized_and_guarded():
    motion = (ROOT / "static" / "css" / "motion.css").read_text()
    assert "[data-dialog]" in motion
    assert ".t-modal-card" in motion
    assert ".t-sheet" in motion
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", motion)
    for bad in ("rgba(", "hsla("):
        assert bad not in motion
