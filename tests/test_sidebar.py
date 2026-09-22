"""Sidebar (Phase 3, revised): collapsible sections, nothing else.

Quick access and menu search were removed — they crowded the menu. What
remains is the registry's own list with collapsible sections.
"""
from pathlib import Path

from services.navigation import COLLAPSED_BY_DEFAULT
from tests.test_roles import _session_as, _staff

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "templates" / "base.html").read_text()


def test_pins_and_search_are_gone():
    assert "sidebar-pins" not in BASE
    assert "menu-search" not in BASE
    assert "data-pin" not in BASE
    assert "quick_access" not in BASE
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".sidebar-pins" not in style
    assert ".sidebar-search" not in style


def test_no_dead_quick_access_registry():
    nav = (ROOT / "services" / "navigation.py").read_text()
    assert "QUICK_ACCESS" not in nav
    assert "quick_access_for" not in nav
    templating = (ROOT / "services" / "templating.py").read_text()
    assert "quick_access" not in templating


def test_collapse_defaults_and_wiring(client, db_session):
    assert COLLAPSED_BY_DEFAULT == ("admin",)
    user, password = _staff(db_session, "pins-collapse", "owner")
    _session_as(client, user, password)
    html = client.get("/admin").text
    assert 'data-collapse="admin" data-collapsed-default' in html
    assert html.count("data-collapse=") >= 2  # every section collapses
    assert 'aria-expanded=' in html
    assert "sidebarCollapsed" in BASE


def test_brand_duplication_is_gone():
    assert "sidebar-brand" not in BASE
    assert "پنل مدیریت" not in BASE


def test_topbar_tips_are_labelled_and_hidden_from_at():
    assert BASE.count('class="topbar-tip"') == 1  # one per action, in the loop
    assert '<span class="topbar-tip" aria-hidden="true">{{ action.label }}</span>' in BASE
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".topbar-tip" in style
    assert "button.sidebar-collapse" in style
