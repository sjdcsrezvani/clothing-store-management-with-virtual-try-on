import re
from pathlib import Path

from services.themes import (
    DEFAULT_THEME_ID,
    THEMES,
    contrast_ratio,
    custom_tokens,
    theme_preview,
)

ROOT = Path(__file__).resolve().parents[1]

# A custom property read without a fallback. `var(--tag-scale, 1)` is not one of
# these: the fallback makes it optional, so only the bare form has to exist.
BARE_VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
DECLARED = re.compile(r"(--[a-z0-9-]+)\s*:")
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _stylesheet() -> str:
    """style.css with its comments removed: the palette is documented at the top
    of the file in exactly the shape of a declaration, and a comment is not one."""
    return COMMENT.sub("", (ROOT / "static" / "css" / "style.css").read_text())


STYLE_CSS = _stylesheet()


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


# Values the shell needs before any theme is applied, and that no theme varies:
# the sidebar's width, the minimum touch target, and the text colour on a filled
# button (which `custom_tokens` supplies for Custom Brand). They live in `:root`
# alone, so they are the only properties a theme is allowed to leave out —
# everything else the stylesheet reads bare has to be in all ten.
GLOBAL_ONLY = {"--sidebar-width", "--touch-target", "--button-text"}


def _root_properties() -> set[str]:
    root = re.search(r":root\s*\{(.*?)\}", STYLE_CSS, re.S)
    assert root, "style.css no longer has a :root block"
    return set(DECLARED.findall(root.group(1)))


def _stylesheet_local_properties() -> set[str]:
    """Properties the stylesheet declares for itself outside `:root` — a section
    that needs one value in two places (the theme gallery's gap)."""
    without_root = re.sub(r":root\s*\{.*?\}", "", STYLE_CSS, count=1, flags=re.S)
    return set(DECLARED.findall(without_root))


def _template_sources() -> list[str]:
    return [COMMENT.sub("", path.read_text()) for path in sorted((ROOT / "templates").rglob("*.html"))]


def _template_properties() -> set[str]:
    """Properties templates declare: their own `:root`, their `<style>` blocks,
    and the ones a page sets inline on an element at render time."""
    found: set[str] = set()
    for source in _template_sources():
        found |= set(DECLARED.findall(source))
    return found


def test_theme_catalog_defines_every_property_the_stylesheet_reads():
    """An undefined custom property does not fall back to a sibling token — the
    declaration is dropped and the element inherits its parent's colour, so the
    page looks deliberate while showing the wrong thing. `--persimmon` went
    missing that way for eleven declarations and nothing failed. Every theme has
    to define every property the stylesheet reads bare.
    """
    exempt = GLOBAL_ONLY | _stylesheet_local_properties()
    reads = {name for name in BARE_VAR.findall(STYLE_CSS) if name not in exempt}
    # The parser has to be finding the reads at all, and persimmon has to be one
    # of them, or this test would pass on an empty set.
    assert "--persimmon" in reads
    assert "--card" in reads and "--ink-soft" in reads
    for theme_id, theme in THEMES.items():
        missing = sorted(reads - set(theme["tokens"]))
        assert not missing, (theme_id, missing)


def test_no_template_reads_a_property_nothing_defines():
    """The same rule for the markup, where a page can also carry its own palette
    or set a property inline (the barcode preview's frame, the gallery's gap)."""
    theme_level = set.intersection(*[set(theme["tokens"]) for theme in THEMES.values()])
    known = theme_level | _root_properties() | _stylesheet_local_properties() | _template_properties()
    unknown: set[str] = set()
    for source in _template_sources():
        unknown |= set(BARE_VAR.findall(source))
    assert not unknown - known, sorted(unknown - known)


def _mix_toward(colour: str, background: str, share: float) -> str:
    """`color-mix(in srgb, colour share, background)` in sRGB, the way Chrome
    resolves the badge tints these tests are about."""
    channels = []
    for index in (1, 3, 5):
        channel = int(colour[index:index + 2], 16) * share + int(background[index:index + 2], 16) * (1 - share)
        channels.append(round(channel))
    return "#%02X%02X%02X" % tuple(channels)


def test_persimmon_is_legible_in_every_theme_on_its_card_and_its_own_badge_tint():
    """Persimmon is the attention colour — money owed, an overdue payment, a
    failed message, text past its limit — so it is read as small text in all
    three roles a theme can be: light surfaces, a dark workspace, and the
    high-contrast palette. The state badges paint it on a 20% tint of itself,
    which is the harder surface of the two, so both are measured.
    """
    assert len(THEMES) == 10
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        assert "--persimmon" in tokens, theme_id
        card = tokens["--card"]
        assert contrast_ratio(tokens["--persimmon"], card) >= 4.5, theme_id
        tint = _mix_toward(tokens["--persimmon"], card, 0.20)
        assert contrast_ratio(tokens["--persimmon"], tint) >= 4.5, (theme_id, tint)


def test_persimmon_is_not_the_brand_colour_in_any_theme():
    """It must not read as a button or a link: an overdue debt painted in
    `--candy` would look clickable, which is why the slot exists."""
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        assert tokens["--persimmon"].upper() != tokens["--candy"].upper(), theme_id
        assert tokens["--persimmon"].upper() != tokens["--candy-dark"].upper(), theme_id


def test_the_dark_and_high_contrast_themes_lift_or_deepen_the_attention_hue():
    """A light theme's persimmon on a dark card is muddy; the two themes with
    inverted or extreme surfaces carry their own value, and both were chosen for
    their own background rather than inherited from `_BASE`."""
    base = THEMES[DEFAULT_THEME_ID]["tokens"]["--persimmon"]
    assert THEMES["midnight-operations"]["tokens"]["--persimmon"] != base
    assert THEMES["high-contrast"]["tokens"]["--persimmon"] != base
    dark = THEMES["midnight-operations"]["tokens"]["--persimmon"]
    assert contrast_ratio(dark, THEMES["midnight-operations"]["tokens"]["--bg"]) >= 4.5


def test_owner_appearance_page_is_protected(client, db_session):
    from tests.test_roles import _staff, _session_as

    cashier, password = _staff(db_session, "theme-cashier", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403


def test_shared_shell_and_theme_settings_do_not_render_or_upload_logo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    base = (root / "templates/base.html").read_text()
    settings = (root / "templates/admin/settings.html").read_text()
    assert "logo_path" not in base
    assert "/static/logo.png" not in base
    assert "theme_logo" not in settings
    assert "store_logo_path" not in settings
    assert "multipart/form-data" not in settings



def test_owner_can_persist_theme_and_shared_shell_loads_it(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _staff, _session_as
    from models import Settings

    owner, password = _staff(db_session, "theme-saver", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/admin/settings/appearance")
    response = client.post(
        "/admin/settings/appearance",
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
    page = client.get("/admin/settings/appearance")
    assert page.status_code == 200
    assert page.text.count('data-theme-id="') == 10
    assert "ui-theme-input" in page.text


def test_general_settings_links_to_appearance_page(client, db_session):
    from tests.test_roles import _staff, _session_as

    owner, password = _staff(db_session, "theme-settings-link", "owner")
    _session_as(client, owner, password)
    response = client.get("/admin/settings")
    assert response.status_code == 200
    assert "/admin/settings/appearance" in response.text
    assert 'data-theme-id="' not in response.text
