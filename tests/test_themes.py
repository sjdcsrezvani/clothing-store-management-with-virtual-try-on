from services.themes import (
    DEFAULT_THEME_ID,
    THEMES,
    contrast_ratio,
    custom_tokens,
    theme_preview,
)


def test_theme_catalog_has_ten_complete_presets():
    assert len(THEMES) == 10
    for theme_id, theme in THEMES.items():
        assert theme["name"]
        assert theme["mode"] in {"light", "dark-shell", "dark", "high-contrast"}
        tokens = theme["tokens"]
        for key in ("--bg", "--card", "--ink", "--ink-soft", "--rule", "--candy", "--sky", "--mint", "--sidebar-bg", "--sidebar-text"):
            assert key in tokens, (theme_id, key)


def test_unknown_theme_preview_falls_back_to_default():
    preview = theme_preview("missing-theme")
    assert preview["id"] == DEFAULT_THEME_ID
    assert preview["name"] == THEMES[DEFAULT_THEME_ID]["name"]


def test_custom_brand_derives_accessible_button_text():
    tokens = custom_tokens("#003D66", "#0F766E")
    assert tokens["--candy"] == "#003D66"
    assert tokens["--sky"] == "#0F766E"
    assert tokens["--primary-contrast"] == "#FFFFFF"
    assert contrast_ratio(tokens["--candy"], tokens["--primary-contrast"]) >= 4.5


def test_custom_brand_rejects_invalid_or_low_contrast_colors():
    try:
        custom_tokens("#12", "#0F766E")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid hex color was accepted")

    tokens = custom_tokens("#777777", "#888888")
    assert tokens["--primary-contrast"] == "#000000"
    assert tokens["--secondary-contrast"] == "#000000"


def test_owner_appearance_page_is_protected(client, db_session):
    from tests.test_roles import _staff, _session_as

    cashier, password = _staff(db_session, "theme-cashier", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403


def test_owner_can_persist_theme_and_shared_shell_loads_it(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _staff, _session_as
    from models import Settings

    owner, password = _staff(db_session, "theme-saver", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/admin/settings")
    response = client.post(
        "/admin/settings",
        data={"csrf_token": token, "ui_theme": "midnight-operations", "theme_custom_primary": "#C94B68", "theme_custom_secondary": "#197A8C"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "ui_theme", Settings.value == "midnight-operations").first()
    page = client.get("/admin/settings")
    assert 'data-theme="midnight-operations"' in page.text
    assert 'data-theme-mode="dark"' in page.text


def test_owner_can_load_appearance_gallery(client, db_session):
    from tests.test_roles import _staff, _session_as

    owner, password = _staff(db_session, "theme-owner", "owner")
    _session_as(client, owner, password)
    response = client.get("/admin/settings")
    assert response.status_code == 200
    assert response.text.count('data-theme-id="') == 10
    assert "ui-theme-input" in response.text
