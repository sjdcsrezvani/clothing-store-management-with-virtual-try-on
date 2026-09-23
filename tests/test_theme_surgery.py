"""Theme surgery (Phase 5): four palettes out, four in, Kashi by default.

The gallery stays ten cards; retired shops are told where they moved.
"""
from pathlib import Path

from services.themes import (
    DEFAULT_THEME_ID,
    RETIRED_THEME_MAP,
    THEMES,
    get_theme,
    migrate_retired_theme,
)
from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_is_ten_with_the_new_set():
    assert len(THEMES) == 10
    assert set(THEMES) == {
        "kashi-tile", "kids-boutique", "operations-light", "pos-focus",
        "blush-maternal", "amber-till", "midnight-operations", "night-bazaar",
        "high-contrast", "custom-brand",
    }
    assert DEFAULT_THEME_ID == "kashi-tile"
    for theme_id in ("premium-navy", "atelier", "ocean-commerce", "forest-ledger"):
        assert theme_id not in THEMES


def test_gallery_leads_with_kashi_then_kids(client, db_session):
    from tests.test_roles import _session_as, _staff
    user, password = _staff(db_session, "gallery-owner", "owner")
    _session_as(client, user, password)
    html = client.get("/admin/settings/appearance").text
    assert html.count('data-theme-id="') == 10
    assert html.index('data-theme-id="kashi-tile"') < html.index('data-theme-id="kids-boutique"')


def test_retired_shops_move_with_a_name_for_it():
    assert migrate_retired_theme("ocean-commerce") == ("pos-focus", "ocean-commerce")
    assert migrate_retired_theme("premium-navy") == ("kashi-tile", "premium-navy")
    assert migrate_retired_theme("kashi-tile") == ("kashi-tile", None)
    assert migrate_retired_theme("banana") == ("banana", None)


def test_retired_shop_sees_the_notice(client, db_session):
    from models import Settings
    from services.themes import THEME_SETTING_KEY, invalidate_theme_cache
    from tests.test_roles import _session_as, _staff
    user, password = _staff(db_session, "retired-owner", "owner")
    _session_as(client, user, password)
    db_session.add(Settings(key=THEME_SETTING_KEY, value="ocean-commerce"))
    db_session.commit()
    invalidate_theme_cache()
    try:
        html = client.get("/admin/settings/appearance").text
        assert "ocean-commerce" in html  # the notice names the move
        assert "بازنشسته شد" in html
        assert 'data-theme="pos-focus"' in client.get("/admin/settings").text
    finally:
        db_session.query(Settings).filter(Settings.key == THEME_SETTING_KEY).delete()
        db_session.commit()
        invalidate_theme_cache()


def test_accent_full_lives_on_storefronts_only():
    base = (ROOT / "templates" / "base.html").read_text()
    assert "page_accent_full" in base and "accent-full" in base
    for name in ("sales/checkout.html", "admin/products.html",
                 "admin/customers.html", "admin/customer_detail.html"):
        assert "page_accent_full" in (ROOT / "templates" / name).read_text(), name
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".accent-full .card::before" in style
    assert "var(--card-accent-full)" in style


def test_no_dead_selectors_for_retired_palettes():
    style = (ROOT / "static" / "css" / "style.css").read_text()
    for retired in ("premium-navy", "atelier", "ocean-commerce", "forest-ledger"):
        assert f'data-theme="{retired}"' not in style, retired


def test_till_has_its_own_name():
    assert THEMES["pos-focus"]["name"] == "Till"


def test_fresh_shop_wears_kashi(client, db_session):
    from tests.test_roles import _session_as, _staff
    user, password = _staff(db_session, "fresh-kashi", "owner")
    _session_as(client, user, password)
    assert 'data-theme="kashi-tile"' in client.get("/admin/settings").text
