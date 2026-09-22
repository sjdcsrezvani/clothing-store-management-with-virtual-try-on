import re
from pathlib import Path

from database import Base, engine
from main import app
from services.themes import (
    DEFAULT_THEME_ID,
    THEME_SETTING_KEY,
    THEMES,
    _BASE,
    _hex,
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
# the sidebar's width and the minimum touch target. They live in `:root` alone, so
# they are the only properties a theme is allowed to leave out — everything else
# the stylesheet reads bare has to be in all ten.
#
# They are also the only properties a *page* may read without its theme defining
# them, and only while that page loads the stylesheet: `:root` is a rule in
# style.css, so it reaches a document that links it and nothing else. Everything
# else a page reads has to come from its own palette or from the page itself.
# `--button-text` used to sit in this set, and the phone capture tool read it on a
# document that loads no stylesheet, so its buttons printed their labels in the
# inherited ink rather than white. It is a theme token now, in `_BASE`.
GLOBAL_ONLY = {"--sidebar-width", "--touch-target"}


def _root_properties() -> set[str]:
    root = re.search(r":root\s*\{(.*?)\}", STYLE_CSS, re.S)
    assert root, "style.css no longer has a :root block"
    return set(DECLARED.findall(root.group(1)))


_LOCAL_PROPERTIES: set[str] | None = None


def _stylesheet_local_properties() -> set[str]:
    """Properties the stylesheet declares for itself outside `:root` — a section
    that needs one value in two places (the theme gallery's gap). A pure function
    of a static file, so it is computed once; re-substituting the whole 200KB
    stylesheet for every rendered document cost more than the requests it
    guarded."""
    global _LOCAL_PROPERTIES
    if _LOCAL_PROPERTIES is None:
        without_root = re.sub(r":root\s*\{.*?\}", "", STYLE_CSS, count=1, flags=re.S)
        _LOCAL_PROPERTIES = set(DECLARED.findall(without_root))
    return _LOCAL_PROPERTIES


_TEMPLATE_SOURCES: list[str] | None = None


def _template_sources() -> list[str]:
    global _TEMPLATE_SOURCES
    if _TEMPLATE_SOURCES is None:
        _TEMPLATE_SOURCES = [COMMENT.sub("", path.read_text()) for path in sorted((ROOT / "templates").rglob("*.html"))]
    return _TEMPLATE_SOURCES


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


# A custom property read with a fallback. The fallback is what the element gets
# when nothing supplies the property, so it may legitimately be a default — a
# measure, a zoom set by the page — and it may never be a colour. A colour in
# there is a hard-coded colour wearing the theme's clothes: it answers for every
# palette that does not define the property, on every page, and the page looks
# deliberate while doing it. `var(--surface-2, #f4f4f5)` painted the checkout's
# terminal chip light grey on the dark till, and nothing failed, because the
# fallback always answered.
FALLBACK_VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*,\s*([^()]*(?:\([^()]*\))?[^()]*)\)")
COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", re.I)


def test_no_colour_hides_in_a_fallback_for_a_property():
    """A fallback may be a default; it may not be a colour.

    Two of these were live: one property that no theme and no stylesheet block
    ever declared, so the chip had carried a light grey through every palette,
    and one read defaulting to another token. Both rendered, neither failed.
    """
    offenders = [f"{name} falls back to {fallback.strip()}"
                 for name, fallback in FALLBACK_VAR.findall(STYLE_CSS)
                 if COLOUR_LITERAL.search(fallback)]
    assert not offenders, (
        "a colour is answering for a property the theme may not define — read the "
        "token instead, or define it in the catalogue:\n" + "\n".join(offenders))
    # The parser has to be finding fallbacks at all, or this passes on nothing.
    assert FALLBACK_VAR.search(STYLE_CSS)


def test_every_property_read_with_a_fallback_is_supplied_somewhere():
    """A fallback is a default for an optional value, not a licence to read a
    property that does not exist.

    The property still has to come from somewhere: every palette, the stylesheet
    itself, or a page at render time (the tag printer measures its own sheets, so
    the preview sets the frame and its zoom inline). Nothing but a runtime value
    is excused, and the excused ones are read out of the templates rather than
    listed here, so a page that stops setting one stops excusing it.
    """
    supplied = (set(THEMES[DEFAULT_THEME_ID]["tokens"])
                | _stylesheet_local_properties() | _template_properties())
    reads = {name for name, _fallback in FALLBACK_VAR.findall(STYLE_CSS)}
    unknown = sorted(reads - supplied)
    assert not unknown, (
        "these properties are read with a fallback and nothing supplies them, so "
        "the fallback is silently the value on every page:\n" + "\n".join(unknown))


def test_no_template_reads_a_property_nothing_defines():
    """The same rule for the markup, where a page can also carry its own palette
    or set a property inline (the barcode preview's frame, the gallery's gap)."""
    theme_level = set.intersection(*[set(theme["tokens"]) for theme in THEMES.values()])
    known = theme_level | _root_properties() | _stylesheet_local_properties() | _template_properties()
    unknown: set[str] = set()
    for source in _template_sources():
        unknown |= set(BARE_VAR.findall(source))
    assert not unknown - known, sorted(unknown - known)


HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# Hex literals in a themed page that are not a colour *of that page*, each with
# the reason it is allowed. The test below fails if one of these stops matching,
# so an exemption cannot quietly outlive what it was written for.
NOT_A_COLOUR: dict[str, tuple[tuple[str, str], ...]] = {
    "templates/admin/barcode_print.html": ((
        "background: #fff",
        "the A4 sheet handed to the printer is paper, not a surface: white in every theme",
    ),),
    "templates/admin/product_form.html": ((
        'placeholder="#4AA3DF"',
        "the example text a shop types into a colour-code field, not a colour of the page",
    ),),
    "templates/admin/variant_form.html": ((
        'placeholder="#4AA3DF"',
        "the same example on the single-variant form",
    ),),
    "static/js/app.js": ((
        "#abc",
        "a comment explaining how a short colour code expands",
    ),),
}


def _colour_sources() -> list[tuple[str, str]]:
    """Every file that paints something: the templates and the scripts beside
    them, as (path relative to the project, text)."""
    paths = list((ROOT / "templates").rglob("*.html")) + list((ROOT / "static" / "js").glob("*.js"))
    return [(str(path.relative_to(ROOT)), path.read_text()) for path in sorted(paths)]


def test_no_page_of_the_shell_carries_its_own_colours():
    """A themed page reads its colours from the active theme, so a shop that runs
    the dark or high-contrast palette does not meet a pink chart, a beige panel or
    a white card in a corner of it. Every hex literal is a colour that cannot
    follow the theme, so a page has to say why it needs one — and there is no
    longer a page excused from this: the phone capture tool was the last one, and
    it reads the shop's palette too.
    """
    offenders: list[str] = []
    for relative, source in _colour_sources():
        excused = tuple(needle for needle, _ in NOT_A_COLOUR.get(relative, ()))
        for number, line in enumerate(source.splitlines(), 1):
            if any(needle in line for needle in excused):
                continue
            for match in HEX_COLOUR.findall(line):
                offenders.append(f"{relative}:{number}: {match} — {line.strip()[:90]}")
    assert not offenders, "\n".join(offenders)


def test_the_colour_exemptions_still_excuse_something():
    """An allowlist that has outlived its reason is how a rule quietly dies."""
    for relative, entries in NOT_A_COLOUR.items():
        source = (ROOT / relative).read_text()
        for needle, reason in entries:
            assert reason, (relative, needle)
            assert needle in source, f"{relative} no longer contains {needle!r}"


# A colour does not have to be a hex to be frozen: `rgba(0,0,0,0.3)` inside a
# box-shadow is the same refusal to ask the palette, and the hex scan cannot see
# it. The channels start with a digit — that is what makes it a literal — so the
# pattern does not match a function the page computes from tokens
# (`rgba(channels.join(', ') + ', ' + alpha)` in the chart renderer, or a
# `color-mix` over a token).
FUNCTION_COLOUR = re.compile(r"\brgba?\(\s*[0-9.]|\bhsla?\(\s*[0-9.]")


def test_no_page_paints_with_a_frozen_function_form_colour():
    """The hex rule's blind spot: a colour literal in function form.

    The try-on pages shadowed their images with `rgba(0,0,0,0.1)` and the
    analytics tooltip with `rgba(0,0,0,0.3)` — shadows no theme owned, painted
    identically on Midnight and on paper-white, while the stylesheet's own
    shadows read `--shadow` and follow the palette. Every template and script
    is scanned here, the same files the hex rule reads, for a function-form
    colour with literal channels.
    """
    offenders: list[str] = []
    for relative, source in _colour_sources():
        for number, line in enumerate(source.splitlines(), 1):
            for _match in FUNCTION_COLOUR.findall(line):
                offenders.append(
                    f"{relative}:{number}: a frozen colour in function form — "
                    f"{line.strip()[:90]} — read the palette's own shadow or mix it "
                    "from a token")
    assert not offenders, "\n".join(offenders)


def test_the_chart_renderer_draws_with_the_active_theme():
    """The offline canvas renderer is a fallback for a page whose chart library
    did not load, and it used to paint a palette of its own — so the shop on the
    dark theme got bars in the daylight colours. It reads the tokens instead.
    """
    script = (ROOT / "static" / "js" / "charts.js").read_text()
    assert "themeTones" in script and "window.themeTones" in script
    for token in ("--candy", "--sky", "--sunshine", "--mint", "--lavender", "--persimmon", "--rule", "--ink-soft"):
        assert token in script, token
    assert "palette = ['#" not in script


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


def _shadow_composite(background: str, shadow: str) -> str:
    """What the background becomes once every rgba layer of the shadow has
    composited over it — the same algebra a browser applies to a box-shadow:
    each layer's alpha blends its colour into what is already there."""
    channels = list(_hex(background))
    for match in re.findall(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\)", shadow):
        r, g, b, alpha = int(match[0]), int(match[1]), int(match[2]), float(match[3])
        for index, value in enumerate((r, g, b)):
            channels[index] = round(value * alpha + channels[index] * (1 - alpha))
    return "#%02X%02X%02X" % tuple(channels)


def test_every_palette_lifts_with_a_shadow_designed_for_it():
    """Elevation is part of a palette's surfaces, so the shadow pair is derived
    per palette and no pair is inherited from `_BASE` — the same doctrine as
    the interaction tokens.

    The wrongness this measures was live: Midnight inherited the light palettes'
    blur and separated card from backdrop by 1.017:1 — a shadow the eye cannot
    find — while its hover state *lightened* the dark background (1.010:1,
    hover weaker than rest). The guard measures each palette's pair against its
    own backdrop, composited the way the browser composites it, and requires:

    * a hover elevation at least as strong as rest — hover must never soften;
    * the rest shadow must separate a card from the page (≥ 1.05:1) and a
      raised card from the one beneath it (≥ 1.02:1);
    * the hover elevation must separate by ≥ 1.20:1 against the page, the
      separation the light palettes have always shown;
    * a dark palette's rim must be visible on its own card — its elevation is
      an edge, and an invisible edge is no edge;
    * the light blur must be tinted from the palette's own ink, not a neutral
      grey chosen for someone else's surfaces.
    """
    HOVER_FLOOR, REST_PAGE_FLOOR, REST_CARD_FLOOR = 1.20, 1.05, 1.02
    offenders: list[str] = []
    for theme_id, theme in THEMES.items():
        tokens = theme_preview(theme_id)["tokens"]
        missing = [name for name in ("--shadow", "--shadow-hover", "--shadow-overlay")
                   if name not in tokens]
        if missing:
            offenders.append(f"{theme_id}: no elevation of its own — {', '.join(missing)} missing")
            continue
        rest, hover = tokens["--shadow"], tokens["--shadow-hover"]
        overlay = tokens["--shadow-overlay"]
        bg, card = tokens["--bg"], tokens["--card"]
        mode = theme["mode"]

        rest_on_bg = contrast_ratio(bg, _shadow_composite(bg, rest))
        rest_on_card = contrast_ratio(card, _shadow_composite(card, rest))
        hover_on_bg = contrast_ratio(bg, _shadow_composite(bg, hover))
        overlay_on_bg = contrast_ratio(bg, _shadow_composite(bg, overlay))
        if hover_on_bg + 0.02 < rest_on_bg:
            offenders.append(f"{theme_id}: hover elevation {hover_on_bg:.3f} is weaker than rest {rest_on_bg:.3f}")
        # The overlay rung is the depth of what floats above the page — a modal,
        # a dropdown picker, the lightbox. It must clear the page by more than
        # hover does: the third rung of a real ladder, not the hover value
        # reused, and never weaker than the rung beneath it.
        if mode != "high-contrast" and overlay_on_bg + 0.02 < hover_on_bg:
            offenders.append(
                f"{theme_id}: overlay elevation {overlay_on_bg:.3f} does not clear "
                f"hover {hover_on_bg:.3f} — the ladder's third rung is missing")

        if mode in ("light", "dark-shell"):
            # A light palette's elevation is a blur, and the blur is that
            # palette's own ink — the exact tint the derivation builds, not a
            # neutral grey chosen for someone else's surfaces.
            ink = tokens["--ink"]
            for name, value in (("--shadow", rest), ("--shadow-hover", hover),
                                ("--shadow-overlay", overlay)):
                rgba = re.search(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,", value)
                if not rgba:
                    offenders.append(f"{theme_id}: {name} is not a tinted blur — {value[:50]}")
                    continue
                tint = "#%02X%02X%02X" % tuple(int(rgba.group(i)) for i in (1, 2, 3))
                if tint.upper() != ink.upper():
                    offenders.append(f"{theme_id}: {name} tints from {tint}, not the palette's own ink {ink}")
            if rest_on_bg < REST_PAGE_FLOOR:
                offenders.append(f"{theme_id}: rest shadow separates {rest_on_bg:.3f} from the page (floor {REST_PAGE_FLOOR})")
            if rest_on_card < REST_CARD_FLOOR:
                offenders.append(f"{theme_id}: rest shadow separates {rest_on_card:.3f} between cards (floor {REST_CARD_FLOOR})")
            if hover_on_bg < HOVER_FLOOR:
                offenders.append(f"{theme_id}: hover elevation separates {hover_on_bg:.3f} from the page (floor {HOVER_FLOOR})")
        elif mode == "dark":
            # A dark palette lifts with an edge: a rim that reads on the card
            # it draws around, plus a deepening blur that darkens the card's
            # own vicinity. An invisible rim is no edge; a blur drawn toward
            # the page background separates nothing from it.
            def rim_and_blur(value: str) -> tuple[str | None, str | None]:
                hex_layer = re.search(r"#[0-9A-Fa-f]{6}", value)
                blur_layer = re.search(r"rgba\([^)]*\)", value)
                return (hex_layer.group(0) if hex_layer else None,
                        blur_layer.group(0) if blur_layer else None)

            rest_rim, rest_blur = rim_and_blur(rest)
            hover_rim, hover_blur = rim_and_blur(hover)
            overlay_rim, overlay_blur = rim_and_blur(overlay)
            if not rest_rim or not rest_blur or not hover_rim or not hover_blur \
                    or not overlay_rim or not overlay_blur:
                offenders.append(f"{theme_id}: dark elevation needs a rim and a deepening blur — {rest[:50]}")
            else:
                rest_edge = contrast_ratio(card, rest_rim)
                hover_edge = contrast_ratio(card, hover_rim)
                overlay_edge = contrast_ratio(card, overlay_rim)
                if rest_edge < 1.15:
                    offenders.append(f"{theme_id}: dark rim {rest_rim} reads {rest_edge:.3f} on its own card (floor 1.15)")
                if hover_edge + 0.02 < rest_edge:
                    offenders.append(f"{theme_id}: hover rim {hover_rim} is weaker than rest rim {rest_rim}")
                if overlay_edge <= hover_edge:
                    offenders.append(f"{theme_id}: overlay rim {overlay_rim} does not rise above hover rim {hover_rim}")
                rest_deep = contrast_ratio(card, _shadow_composite(card, rest_blur))
                hover_deep = contrast_ratio(card, _shadow_composite(card, hover_blur))
                overlay_deep = contrast_ratio(card, _shadow_composite(card, overlay_blur))
                if rest_deep < 1.05:
                    offenders.append(f"{theme_id}: deepening blur separates {rest_deep:.3f} from the card")
                if hover_deep + 0.02 < rest_deep:
                    offenders.append(f"{theme_id}: hover blur deepens less than rest")
                if overlay_deep <= hover_deep:
                    offenders.append(f"{theme_id}: overlay blur deepens no more than hover — a modal would sit at hover depth")
        else:
            # The high-contrast palette's designed idiom: a hard ring, never a
            # blur — and the ring must actually read against its page. The
            # ladder is in the ring's width: 1px at rest, 2px on hover, 3px for
            # what floats above the page.
            for name, value in (("--shadow", rest), ("--shadow-hover", hover),
                                ("--shadow-overlay", overlay)):
                if re.search(r"rgba\(", value):
                    offenders.append(f"{theme_id}: {name} blurs where the palette demands a ring — {value[:50]}")
            rings = re.findall(r"#[0-9A-Fa-f]{6}", rest + " " + hover + " " + overlay)
            if not rings or any(contrast_ratio(bg, ring) < 1.05 for ring in rings):
                offenders.append(f"{theme_id}: a ring the page cannot see — {rest[:50]}")
            widths = [int(m) for m in re.findall(r"0 0 0 (\d+)px", rest + " " + hover + " " + overlay)]
            if len(widths) != 3 or widths != sorted(widths) or len(set(widths)) != 3:
                offenders.append(
                    f"{theme_id}: the ring ladder is not 1px < 2px < 3px — {widths}")

    # The derivation owns the ladder: `_BASE` may not carry a shadow literal
    # that every palette's own elevation would silently overwrite — a dead value
    # pretending to be the design.
    assert "--shadow" not in _BASE and "--shadow-hover" not in _BASE
    assert "--shadow-overlay" not in _BASE

    # The stylesheet's fallback stays a bare marker: it must not carry a colour
    # the themes are meant to own, and it must keep the three names alive for a
    # page that loads the stylesheet before its palette.
    root = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for name in ("--shadow", "--shadow-hover", "--shadow-overlay"):
        assert f"{name}: var(--{name[2:]}" not in root, name
    assert re.search(r"--shadow:\s*0\s+0\s+0\s+transparent", root), "the :root fallback vanished"
    assert re.search(r"--shadow-hover:\s*0\s+0\s+0\s+transparent", root), "the :root fallback vanished"
    assert re.search(r"--shadow-overlay:\s*0\s+0\s+0\s+transparent", root), "the :root fallback vanished"

    assert not offenders, "\n".join(offenders)


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


def test_a_page_after_the_form_switch_wears_the_new_palette(client, db_session):
    """The shell caches its theme between renders; the switch must still be live.

    The cache exists so a page render reads the settings table once instead of
    three times — the store profile keeps the same shape. Its one way to be
    wrong is staleness: the owner saves a new palette, and the next page they
    open still wears the old one because nothing told the cache. So the guard
    walks the owner's real sequence — a page first (which warms the cache with
    the old theme), then the form POST, then a page that must carry the new
    palette immediately.
    """
    from tests.conftest import csrf_token
    from tests.test_roles import _staff, _session_as

    owner, password = _staff(db_session, "theme-switch-live", "owner")
    _session_as(client, owner, password)

    # Warm the cache with whatever palette the shop starts on, then switch.
    assert 'data-theme="kashi-tile"' in client.get("/admin/settings").text
    token = csrf_token(client, "/admin/settings/appearance")
    response = client.post(
        "/admin/settings/appearance",
        data={"csrf_token": token, "ui_theme": "midnight-operations",
              "theme_custom_primary": "#C94B68", "theme_custom_secondary": "#197A8C"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/admin/settings")
    assert 'data-theme="midnight-operations"' in page.text, \
        "the shell kept wearing the old palette after the form saved a new one"


def test_the_shell_theme_cache_keeps_its_discipline():
    """The cache must be exactly as wide as its honesty allows.

    Only the shell path may read it (a caller handing in a session gets a live
    read, so a caller mid-transaction never sees yesterday's palette), and every
    writer must invalidate it by name — the appearance form here, and any writer
    the future adds. The appearance route is pinned by name so the invalidation
    next to it cannot be deleted while this test still passes for the wrong
    reason.
    """
    import inspect

    from services import themes as themes_module

    source = inspect.getsource(themes_module.get_theme)
    assert "if db is None" in source, "the cache must apply to the shell path only"
    # The cache read has to sit *inside* that guard: a caller handing in a
    # session must never be answered from memory.
    body = source[source.index("global _SHELL_THEME_CACHE"):]
    assert body.index("if db is None:") < body.index('_SHELL_THEME_CACHE["data"] is not None'), \
        "the cache must be consulted only after the explicit-session branch is decided"

    admin_source = (ROOT / "routers" / "admin.py").read_text(encoding="utf-8")
    start = admin_source.index("async def admin_update_appearance")
    appearance = admin_source[start:admin_source.index("\n@router.", start)]
    assert "invalidate_theme_cache()" in appearance, \
        "the appearance form must drop the shell's cached theme when it saves"

    assert themes_module._SHELL_THEME_TTL <= 120, \
        "a palette switch must reach every page within a couple of minutes even " \
        "if a writer forgets to invalidate"


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


# ── the whole shell, every role, every theme ─────────────────────────────────
#
# `--persimmon` was read by eleven declarations and defined by no theme for a
# whole release. The pages that used it looked deliberate while printing an
# overdue debt in the ordinary ink colour, and nothing failed — the bug had to
# be *seen* on a page before anyone could know about it. The tests above close
# that hole by reading the sources: the stylesheet, the templates, the renderer.
# This one closes it on what the shop actually receives — it opens every address
# the shell serves, as every role, with each of the ten themes active, and
# resolves every custom property the rendered page reads against the palette that
# theme supplies. The stylesheet's `:root` is deliberately not allowed to answer
# for a theme: it is the fallback for a page rendered with no theme at all, so if
# it counted as a definition here, nine themes could drop a colour and every page
# would keep rendering a plausible one — which is precisely how the bug survived.
#
# A read nothing defines is not a fallback to a sibling token: the declaration is
# invalid at computed-value time, so the property inherits its parent's value and
# the page renders in a colour nobody chose. That is an invisible failure, which
# is why it is checked as a build step rather than trusted to a designer's eye.
#
# The matrix is real traffic through the real app — one request per address, per
# role, per theme, no sample of the palettes — so it is also where a page that
# crashes for a role, or one that loads without the theme it was asked for, or
# one that answers differently depending on the palette, fails the build.

ROLES = ("cashier", "manager", "owner")

# Addresses that are not pages of the shell, each with the reason it is not one.
# The test fails if one of them starts drawing a page — and if one stops being
# served at all — so an excuse cannot outlive the thing it excuses.
NOT_A_PAGE: dict[str, str] = {
    "/admin/accounting/export": "the سود و زیان export is a CSV for the shop's books program",
    "/admin/analytics/export": "the تحلیل فروش export is a CSV of one tab's figures",
    "/admin/backups/download": "the download is the backup file itself",
    "/admin/try-on/download": "the try-on engine answers with its own response",
    "/admin/collections": "a shortcut that redirects to the invoices it collects",
    "/admin/sales": "a shortcut that redirects to the sales history it counts",
    "/admin/setup": "the demo build's first-run wizard, and this is the owner's private build",
    "/sales/api/barcode/{barcode}": "the scanner's lookup, which answers in JSON",
    "/admin/try-on/saved/{img_id}/download": "the saved photo itself, not a page",
}

# Addresses the matrix does not open at all, each with the reason. The till's
# long poll holds the request open for two seconds waiting for a card terminal and
# answers in JSON, so opening it once per role per theme would add a minute to the
# suite and still would not draw a page. The guard still insists the app serves
# it, so a renamed route cannot leave a stale excuse behind.
UNPOLLED: dict[str, str] = {
    "/sales/terminal-status": "the till's long poll: two seconds of waiting, and JSON when it answers",
}

# The one thing a document loads from the shell. `:root` is a rule inside this
# file, so a page that does not link it cannot resolve anything the stylesheet
# declares — and the phone capture tool is deliberately self-contained.
STYLESHEET_LINK = "/static/css/style.css"

# Statuses that carry a document. A refusal and a not-found are pages as well: the
# Persian 403 and 404 the shell serves are drawn like any other page, with the
# sidebar, the breadcrumb and the theme — so they are colour-checked too.
HTML_ANSWERS = (200, 403, 404)

# The one address that needs a different record of a kind the seed already has: an
# invoice that has been finalised has no editable form, the page redirects away
# from it, so this address is pointed at the draft kept beside it.
DRAFT_EDIT = "/admin/purchases/{purchase_id}/edit"


def _served_addresses() -> list[str]:
    """Every GET the shell serves, taken from the app's own route table.

    Read from the schema rather than written out by hand, so a page added
    tomorrow is in the matrix without anyone remembering to add it here. The
    guard below states out loud which pages the walk is expected to find, so a
    walk that stops seeing them fails instead of checking an empty list.
    """
    return sorted(path for path, operations in app.openapi()["paths"].items()
                  if "get" in operations and path.startswith(("/admin", "/sales")))


def _filled_addresses(ids: dict[str, object]) -> list[tuple[str, str]]:
    """``(the address as the route table spells it, the same address with real ids)``."""
    filled, unresolved = [], []
    for template in _served_addresses():
        if template in UNPOLLED:
            continue
        page_ids = dict(ids)
        if template == DRAFT_EDIT:
            page_ids["purchase_id"] = ids["draft_purchase_id"]
        address = re.sub(r"\{(\w+)\}",
                         lambda match: str(page_ids.get(match.group(1), match.group(0))), template)
        (filled if "{" not in address else unresolved).append((template, address))
    assert not unresolved, (
        "the seed has no id for " + ", ".join(address for _template, address in unresolved))
    return filled


def _activate_theme(db, theme_id: str) -> None:
    """Put the shop on one of the ten palettes, the way the appearance page does."""
    from models import Settings

    from services.themes import invalidate_theme_cache

    row = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
    if row is None:
        db.add(Settings(key=THEME_SETTING_KEY, value=theme_id))
    else:
        row.value = theme_id
    db.commit()
    # The shell caches its theme for a page's lifetime; a walk that flips the
    # palette under it must drop that cache exactly as the form does.
    invalidate_theme_cache()


def _page_colours(html: str) -> tuple[set[str], set[str]]:
    """``(the properties a page's own markup reads, the ones it declares)``.

    Only the page's own `style` attributes and `<style>` blocks. The stylesheet is
    a separate matter: it is one file for all ten themes and the tests above
    check it against every one of them. A read that carries a fallback —
    `var(--tag-scale, 1)` — is deliberately not collected, because the fallback
    is exactly what makes a missing definition harmless there.
    """
    markup = COMMENT.sub("", " ".join(
        re.findall(r'style="([^"]*)"', html)
        + re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)))
    return set(BARE_VAR.findall(markup)), set(DECLARED.findall(markup))


# Words the shop must never read on its own page: the renderer's own vocabulary
# (`None`, `undefined`), a number that is not a number, and a template that never
# rendered. These are the visible faces of the same family as a token nobody
# defined — a page that draws something plausible out of nothing. Everything the
# reader can see is searched, which includes the value an input was given: a
# field holding `None` is read as a filled-in field, and saving the form writes
# the word into the database. A `<script>` or a `<style>` is not a page's text, so
# those are the only parts removed; searching text with the tags stripped would
# have hidden exactly the case that found this rule.
# Deliberately not `{{` and `}}`: a page that documents the placeholder syntax, and
# a `data-` attribute carrying the tag editor's own layout JSON, both print braces
# on purpose, and neither is distinguishable from a template that failed to render
# — which the recorder above already catches by name.
FAKE_TEXT = re.compile(r"\bNone\b|\bnan\b|\binf\b|\bInfinity\b|\bundefined\b"
                       r"|\[object Object\]")
SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _printed_artefacts(html: str) -> list[str]:
    """The renderer's own words, found anywhere the shop can read them."""
    words = sorted(set(FAKE_TEXT.findall(SCRIPT_OR_STYLE.sub(" ", html))))
    return [f"prints {words}, which is the renderer talking and not the shop"] if words else []


_PALETTES: dict[str, set[str]] = {}


def _palettes() -> dict[str, set[str]]:
    """What each palette supplies, built once for all three walks."""
    for theme_id in THEMES:
        _PALETTES.setdefault(theme_id, set(theme_preview(theme_id)["tokens"]))
    return _PALETTES


def _document_offenders(html: str, theme_id: str, palettes: dict[str, set[str]]) -> list[str]:
    """Everything wrong with one rendered document: its palette, and its words.

    What a page may read is the palette the theme itself supplies and — only if
    the document actually loads the stylesheet — the values the stylesheet
    declares for itself. Deliberately *not* the stylesheet's `:root` as a way to
    answer for a palette: that block is the fallback for a page rendered without a
    theme, and letting it answer for one is how `--persimmon` went missing from
    nine themes while every page kept rendering a plausible colour — the light
    theme's value, on a dark card, chosen by nobody. The phone capture tool is why
    the link is checked rather than assumed: it is handed a palette and nothing
    else, so `:root` was never available to it.
    """
    problems = []
    if f'data-theme="{theme_id}"' not in html:
        problems.append(f"loaded without the {theme_id} theme")
    allowed = palettes[theme_id]
    if STYLESHEET_LINK in html:
        allowed = allowed | GLOBAL_ONLY | _stylesheet_local_properties()
    reads, declares = _page_colours(html)
    unknown = reads - allowed - declares
    if unknown:
        problems.append(
            f"on {theme_id} reads {sorted(unknown)}, which that palette does not "
            "define — so the declaration is dropped and the colour is inherited, or "
            "a fallback from outside the theme answers for it")
    return problems + _printed_artefacts(html)


def _walk(client, db, accounts: dict, addresses: list[tuple[str, str]], themes: list[str] | None = None):
    """Open every address, as every role, on the given palettes.

    Returns ``(what is wrong, what each cell answered, how many documents were
    drawn)``. One function for all three shops, so the rules cannot differ
    between a shop with records and a shop without them. The matrix walks every
    palette; a state walk passes its own, smaller, list.
    """
    from tests.test_roles import _session_as

    palettes = _palettes()
    answers: dict[tuple[str, str], tuple[int, bool]] = {}
    offenders: list[str] = []
    rendered = 0
    for role in ROLES:
        _session_as(client, *accounts[role])
        for theme_id in (themes if themes is not None else list(THEMES)):
            _activate_theme(db, theme_id)
            for template, address in addresses:
                response = client.get(address, follow_redirects=False)
                drawn = (response.status_code in HTML_ANSWERS
                         and "text/html" in response.headers.get("content-type", ""))
                answer = (response.status_code, drawn)
                if response.status_code >= 500:
                    offenders.append(f"{role} {address} answered {response.status_code}")
                if (role, template) in answers and answers[(role, template)] != answer:
                    offenders.append(
                        f"{role} {address} answered {answer} on {theme_id} and "
                        f"{answers[(role, template)]} on another theme")
                answers.setdefault((role, template), answer)
                if not drawn:
                    continue
                rendered += 1
                offenders.extend(f"{role} {address} {problem}" for problem in
                                 _document_offenders(response.text, theme_id, palettes))
    return offenders, answers, rendered


def _one_of_everything(db_session, staff: dict) -> dict[str, object]:
    """One record of each kind, so every address can be opened for real.

    The seed is chosen for the states it puts on the page, not for volume, because
    that is where the colours are: a page rendered only in its calm state leaves
    the markup of its alert state unchecked. The invoice line naming the نسیه debt
    is drawn only for an unpaid credit sale, the category bar on سود و زیان only
    when expenses are categorised, the wage only when it was paid on the card. A
    paid invoice and a debt-free shop would have left all of those unrendered, so
    a missing colour would have shipped with the matrix passing — which is exactly
    how `--persimmon` survived a release. Hence: a نسیه past its due date, a debt
    over the customer's limit, a message that failed, a check that is overdue, a
    shift counted short, a wage paid on the card.
    """
    from datetime import datetime, timedelta, timezone

    from models import (Campaign, CashSession, CashSessionEntry, CheckRecord, Customer,
                        Expense, GeneratedImage, Product, ProductVariant, Purchase,
                        PurchaseItem, Sale, SaleItem, SalaryPayment, SmsMessage,
                        SmsTemplate, Supplier)

    now = datetime.now(timezone.utc)
    product = Product(name="کتانی تست", category="کفش")
    supplier = Supplier(name="عمده‌فروشی تست", phone="02112345678")
    customer = Customer(phone="09120000001", first_name="سارا", last_name="تست",
                        referral_code="MATRIX01", tier="gold", credit_limit=1_000_000,
                        total_debt=1_500_000, total_spent=2_480_000, total_purchases=2,
                        total_points=120, birth_month_day="05-12", birth_year=1370,
                        last_purchase_date=now - timedelta(days=45))
    campaign = Campaign(name="کمپین زمستانه", code="MATRIX10", discount_percent=10, is_active=True)
    template = SmsTemplate(
        key="matrix-follow-up", name="پیگیری تست", category="custom",
        body="دلمان برایتان تنگ شده؛ {var1} جان منتظرتان هستیم.",
        variables='[{"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"}]',
        trigger_key="purchase", is_active=True)
    db_session.add_all([product, supplier, customer, campaign, template])
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=980_000, cost_price=420_000,
                             stock_quantity=1, reorder_point=5, size="۳۸", color="مشکی",
                             barcode="MATRIX-BARCODE")
    db_session.add(variant)
    db_session.flush()

    # Two invoices: one نسیه past its due date (the ageing pill, the debt card),
    # one settled on the card (the top sellers, the drawer's own takings).
    credit_sale = Sale(customer_id=customer.id, total_amount=1_500_000, final_amount=1_500_000,
                       payment_method="credit", payment_confirmed=True,
                       credit_due_date=now - timedelta(days=45),
                       created_at=now - timedelta(days=50))
    card_sale = Sale(customer_id=customer.id, total_amount=980_000, final_amount=980_000,
                     payment_method="card", payment_confirmed=True,
                     created_at=now - timedelta(days=2))
    db_session.add_all([credit_sale, card_sale])
    db_session.flush()
    db_session.add_all([
        SaleItem(sale_id=credit_sale.id, product_id=product.id, variant_id=variant.id,
                 quantity=1, unit_price=1_500_000, unit_cost=420_000, total_price=1_500_000),
        SaleItem(sale_id=card_sale.id, product_id=product.id, variant_id=variant.id,
                 quantity=1, unit_price=980_000, unit_cost=420_000, total_price=980_000),
    ])

    purchase = Purchase(supplier_id=supplier.id, total_cost=4_200_000, note="فاکتور تست",
                        purchase_date=now - timedelta(days=10), amount_paid=4_200_000)
    # …and one still being assembled, because a finalised invoice has no editable
    # form: the only way to reach that page is with a draft.
    draft = Purchase(supplier_id=supplier.id, total_cost=1_260_000, note="پیش‌نویس تست",
                     is_draft=True, purchase_date=now - timedelta(days=1))
    db_session.add_all([purchase, draft])
    db_session.flush()
    db_session.add_all([
        PurchaseItem(purchase_id=purchase.id, variant_id=variant.id,
                     product_id=product.id, quantity=5, unit_cost=420_000),
        PurchaseItem(purchase_id=draft.id, variant_id=variant.id,
                     product_id=product.id, quantity=3, unit_cost=420_000),
    ])

    # Two shifts: one closed with a shortage (the history and its statement), one
    # still open (the drawer's own page, and the dashboard's «صندوق باز مانده»).
    closed_shift = CashSession(
        cashier_user_id=staff["cashier"].id, opening_balance=2_000_000,
        opened_at=now - timedelta(days=3), closed_at=now - timedelta(days=3) + timedelta(hours=8),
        expected_closing_balance=3_450_000, counted_closing_balance=3_400_000,
        manager_user_id=staff["manager"].id, variance=-50_000, status="closed")
    open_shift = CashSession(cashier_user_id=staff["cashier"].id, opening_balance=1_500_000,
                             opened_at=now - timedelta(hours=2), status="open")
    db_session.add_all([closed_shift, open_shift])
    db_session.flush()
    db_session.add(CashSessionEntry(cash_session_id=open_shift.id, entry_type="withdrawal",
                                    amount=250_000, reason="واریز به بانک",
                                    operator_user_id=staff["cashier"].id))

    # Money leaving the business in both payment methods and in categories: the
    # breakdown on سود و زیان has bars to draw, and the card-paid wage is the one
    # that must not be taken out of the drawer.
    rent = Expense(amount=800_000, category="اجاره", payment_method="cash")
    internet = Expense(amount=300_000, category="اینترنت", payment_method="card")
    wage = Expense(amount=11_500_000, category="حقوق", payment_method="cash")
    db_session.add_all([rent, internet, wage])
    db_session.flush()
    salary = SalaryPayment(staff_user_id=staff["cashier"].id, period_key="1405-05",
                           gross_amount=12_000_000, deductions=500_000, net_amount=11_500_000,
                           payment_method="cash", operator_user_id=staff["owner"].id,
                           expense_id=wage.id, cash_session_id=open_shift.id,
                           paid_at=now - timedelta(days=1), note="حقوق مرداد")
    db_session.add(salary)

    # A check past its date: the checks page counts it and prints its days late
    # in the attention colour.
    db_session.add(CheckRecord(supplier_id=supplier.id, provider_name="چاپخانه تست",
                               check_number="123456", amount_rials=5_000_000,
                               issue_at=now - timedelta(days=40), due_at=now - timedelta(days=5),
                               operator_user_id=staff["owner"].id, status="issued"))

    # One message that went out and one that failed: the history prints the
    # failure in the attention colour, and both carry the values behind them.
    db_session.add_all([
        SmsMessage(template_id=template.id, template_key=template.key, template_name=template.name,
                   customer_id=customer.id, phone=customer.phone,
                   body="دلمان برایتان تنگ شده؛ سارا جان منتظرتان هستیم.", status="sent",
                   kind="transactional", source="purchase", ref=f"sale:{card_sale.id}",
                   values_json='[{"token": "var1", "label": "نام مشتری", "value": "سارا"}]',
                   sent_at=now - timedelta(days=1)),
        SmsMessage(customer_id=customer.id, phone=customer.phone, body="یادآوری بدهی",
                   status="failed", error="پاسخ درگاه: اعتبار کافی نیست",
                   kind="transactional", source="manual"),
    ])

    image = GeneratedImage(customer_id=customer.id, image_path="generated/matrix.png",
                           prompt_used="پرو تست", created_at=now - timedelta(hours=3))
    db_session.add(image)
    db_session.commit()

    return {
        "customer_id": customer.id, "product_id": product.id, "variant_id": variant.id,
        "purchase_id": purchase.id, "draft_purchase_id": draft.id, "campaign_id": campaign.id,
        "staff_id": staff["cashier"].id, "payment_id": salary.id,
        "template_id": template.id, "session_id": closed_shift.id, "img_id": image.id,
        "sale_id": credit_sale.id, "barcode": variant.barcode,
    }


def test_the_matrix_still_finds_the_pages_it_was_written_for():
    """A test that opens nothing passes for the wrong reason.

    The addresses come from the app's schema, so this states out loud which pages
    the walk is expected to find — and that the schema still spells the
    placeholders the seed supplies, which is what keeps a new parameterised route
    from being quietly dropped from the matrix.
    """
    templates = _served_addresses()
    assert len(templates) >= 60, templates
    for address, reason in UNPOLLED.items():
        assert address in templates, address
        assert reason, address
    for address in ("/admin/", "/admin/cashbox", "/admin/settings/appearance",
                    "/admin/sms/send", "/sales/new", "/admin/mobile",
                    "/sales/invoice/{sale_id}", "/admin/cashbox/sessions/{session_id}"):
        assert address in templates, address
    assert {name for template in templates for name in re.findall(r"\{(\w+)\}", template)}
    for excuse, reason in NOT_A_PAGE.items():
        assert reason, excuse


def test_every_page_the_shell_serves_reads_only_colours_its_theme_defines(client, db_session):
    """Every address, as every role, on each of the ten palettes.

    Nothing is sampled and nothing is inferred: each cell of the matrix is a real
    request to the real page. A page is drawn with the theme that was asked for,
    answers the same way in all ten, and every custom property its own markup
    reads is defined by that palette, by one of the global values no theme varies,
    or by the page itself.
    """
    from tests.test_roles import _staff

    accounts = {role: _staff(db_session, f"matrix-{role}", role) for role in ROLES}
    ids = _one_of_everything(db_session, {role: user for role, (user, _password) in accounts.items()})
    addresses = _filled_addresses(ids)
    offenders, answers, rendered = _walk(client, db_session, accounts, addresses)

    # Coverage, so the matrix cannot shrink quietly: every address was asked for
    # as every role and every answer was the same in all ten themes…
    assert len(answers) == len(addresses) * len(ROLES), (len(answers), len(addresses))
    assert rendered == sum(1 for _key, (_status, drawn) in answers.items() if drawn) * len(THEMES)
    assert rendered > 0

    # …and for the owner every address is a page, or is listed above with the
    # reason it is not one. An excuse that has outlived its address fails too.
    served = {template for _role, template in answers}
    owner_pages = {template for (role, template), (status, drawn) in answers.items()
                   if role == "owner" and status == 200 and drawn}
    unexplained = sorted(served - owner_pages - set(NOT_A_PAGE))
    assert not unexplained, f"these addresses are not pages and nothing says why: {unexplained}"
    stale = sorted(set(NOT_A_PAGE) - (served - owner_pages))
    assert not stale, f"these addresses are excused from being pages and no longer need it: {stale}"

    assert not offenders, "\n".join(offenders[:40])


# ── The two shops the matrix never renders ────────────────────────────────────
#
# The matrix opens a shop holding one record of every kind, which is the only way
# to reach most of a page's markup — and that seed is why a colour nobody chose
# survived a release. It has the mirror image of that blind spot, though: a branch
# that draws only when there is *nothing* to draw is never rendered at all. So the
# same walk runs twice more, against a shop with no records and against the shop
# the app's own boot leaves behind on a database nobody has opened. Both are held
# to the same rules — the palette, the theme, and the words a page may print.
#
# It earned its keep before it was finished. The colour × size matrix grouped
# nothing, so SQLite answered it with one synthesised row: on a full shop the
# heatmap showed a single cell holding the total of everything, and on an empty one
# it printed «None» down the page. The seeded matrix and the hex scan could each
# see neither face of that. The product form had the second face: every optional
# field a product had not filled printed `None` into its value, so the shop's own
# shelf wrote the word into the database on the next save.


# The palettes a *state* walk visits. Its question is about the shop's records —
# what a page does with none of them — not about its colours, so it does not need
# the full matrix: every built-in theme is derived through the same `_complete`,
# which makes the ten token *name* sets identical, and what differs between them
# is the derivation the tokens go through. One theme per mode is what that
# derivation can actually change — light, dark, dark-shell, high-contrast — plus
# the one theme whose tokens a shop chooses: `custom-brand` computes its colours
# from the store's own two hues, so it is the likeliest to leave a token behind.
# The per-mode guard below keeps that claim honest: a theme whose mode or token
# names stop matching its sibling joins the sample, so the reduction cannot
# quietly stop checking a whole family of palettes.
STATE_THEMES = ["operations-light", "midnight-operations", "pos-focus",
                "high-contrast", "custom-brand"]


def test_the_state_sample_still_covers_what_differs_between_palettes():
    """The reduction stays true to what a palette can change.

    The sample is honest only while two claims hold: every mode has a theme in it,
    and every theme still defines the same token *names* its mode-mate does — the
    fact that made the reduction sound in the first place. A new theme joins
    neither automatically; this guard refuses to let the walk stay small when the
    catalogue stops matching it.
    """
    by_mode = {}
    for theme_id, theme in THEMES.items():
        by_mode.setdefault(theme["mode"], []).append(theme_id)
    unrepresented = sorted(mode for mode, members in by_mode.items()
                           if not set(members) & set(STATE_THEMES))
    assert not unrepresented, (
        "a derivation mode no state walk visits — add one of "
        + ", ".join(unrepresented) + " to STATE_THEMES")
    # …and the one theme whose tokens a shop chooses. Its mode is shared with
    # themes whose tokens nobody chose, so the mode check above would not notice
    # it leaving the sample — and it is the likeliest theme to leave a token
    # behind, which is exactly what the state walks exist to catch.
    assert "custom-brand" in STATE_THEMES, (
        "the theme computed from a shop's own colours must stay in the state sample")

    # Same token names within a mode: the property the reduced walk leans on. The
    # comparison is by *name* — every palette states its own values, and a value
    # a theme left undefined is what the per-document check catches.
    token_sets = {theme_id: set(theme["tokens"]) for theme_id, theme in THEMES.items()}
    for mode, members in by_mode.items():
        names = [set(token_sets[theme_id]) for theme_id in members]
        if all(names[0] == other for other in names[1:]):
            continue
        unvisited = sorted(set(members) - set(STATE_THEMES))
        assert not unvisited, (
            f"the {mode} themes no longer share one token-name set — the state walk "
            "assumed they did; visit " + ", ".join(unvisited) + " too or fix the derivation")


def _unheld_addresses() -> list[tuple[str, str]]:
    """``(the address as the route table spells it, the same address with an id
    nothing holds)`` — how a shop with no records is opened.

    An id that matches nothing is the honest way to ask the question. The page
    must answer with a page — its own empty state, or the Persian 404 — rather
    than a figure made up out of nothing to summarise.
    """
    return [(template, re.sub(r"\{\w+\}", "1", template))
            for template in _served_addresses() if template not in UNPOLLED]


def _assert_collections_draw_themselves(answers: dict, addresses: list[tuple[str, str]]) -> None:
    """A page whose job is to list records must draw with none of them.

    An address that takes no id is not waiting for data to exist: on a shop with
    nothing in it, it still has a page to be. What is left is the files, the
    shortcuts and the demo wizard, each excused by name in ``NOT_A_PAGE``.
    """
    silent = sorted(template for template, _address in addresses
                    if "{" not in template
                    and answers[("owner", template)] != (200, True)
                    and template not in NOT_A_PAGE)
    assert not silent, f"these pages cannot draw a shop with nothing in it: {silent}"


# Every call the app's own boot makes, and what the first-run pass does about it.
# A step that is neither replayed nor explained fails the guard below, so the pass
# cannot quietly drift into testing a first run the app no longer performs.
BOOT_STEPS = {
    "create_all": "replayed: the schema a new file is given",
    "upgrade": "replayed: the migrations that bring it to this build's revision",
    "_apply_missing_columns": "replayed: the additive columns `upgrade` leaves to it",
    "_backfill_buys_for": "replayed: it pins rows that predate the choice and finds none",
    "_migrate_unknown_customers": "replayed: it looks for one legacy row and finds none",
    "_seed_legacy_stock_movements": "replayed: it opens a movement for pre-ledger stock and finds none",
    "ensure_owner_account": "replayed: the owner a first run creates from ADMIN_PASSWORD",
    "backfill_legacy_events": "replayed: it backfills events for rows that predate them",
    "ensure_seeded": "replayed: the built-in message templates, inactive until the shop writes one",
    "validate_production_config": "not data: it reads the environment, and the app refused to start without it",
    "SessionLocal": "not a step: the session the boot opens to run the three below it",
    "commit": "not a step: it saves what those three wrote",
    "close": "not a step: it gives the session back",
    "create_task": "not data: it supervises the scheduler",
    "cancel": "not data: it stops the scheduler on shutdown",
}
BOOT_CALL = re.compile(r"^\s{4,}(?:[\w.]+\s*=\s*)?([\w.]+)\(", re.M)
IMPORT_AS = re.compile(r"from\s+[\w.]+\s+import\s+(\w+)\s+as\s+(\w+)")


def _boot_a_new_database(db) -> None:
    """Everything the app's own boot does to a database nobody has opened yet.

    The boot's own functions, in the boot's own order. The schema steps run
    against a database that already has the schema, which is what they are built
    for: `create_all` and the additive pass are idempotent, and `upgrade` answers
    with the revision it is already on.
    """
    from main import (_apply_missing_columns, _backfill_buys_for,
                      _migrate_unknown_customers, _seed_legacy_stock_movements)
    from migrations import upgrade
    from services.events import backfill_legacy_events
    from services.security import ensure_owner_account
    from services.sms_templates import ensure_seeded

    Base.metadata.create_all(bind=engine)
    upgrade(engine)
    _apply_missing_columns()
    _backfill_buys_for()
    _migrate_unknown_customers()
    _seed_legacy_stock_movements()
    # …and the three the boot runs on a session of its own.
    ensure_owner_account(db)
    backfill_legacy_events(db)
    ensure_seeded(db)
    db.commit()


def test_the_boot_this_pass_replays_is_the_whole_boot():
    """The replay is checked against the boot it claims to replay.

    Read from `lifespan` rather than kept by hand: a step added to the app's own
    start-up has to be replayed here or explained below, so the first-run pass
    cannot become a description of a first run that no longer happens.
    """
    import inspect

    source = (ROOT / "main.py").read_text(encoding="utf-8")
    lifespan = source.split("async def lifespan")[1].split("\napp = FastAPI")[0]
    aliases = {alias: real for real, alias in IMPORT_AS.findall(lifespan)}
    called = {aliases.get(name.split(".")[-1], name.split(".")[-1])
              for name in BOOT_CALL.findall(lifespan)}
    unexplained = sorted(called - set(BOOT_STEPS))
    assert not unexplained, (
        "the boot now does something the first-run pass does not — replay it, or say "
        "here why a database with no rows is not affected by it: " + ", ".join(unexplained))

    # A *call*, not a mention: the function's own imports name every step it
    # replays, so searching the source for the name would pass a step that is
    # imported and never run.
    body = inspect.getsource(_boot_a_new_database)
    missing = sorted(name for name, note in BOOT_STEPS.items()
                     if note.startswith("replayed") and not re.search(rf"\b{name}\(", body))
    assert not missing, (
        "the pass says it replays these and does not call them: " + ", ".join(missing))
    assert len([note for note in BOOT_STEPS.values() if note.startswith("replayed")]) >= 9
    for name, note in BOOT_STEPS.items():
        assert note.strip(), name


def test_every_page_survives_an_empty_shop(client, db_session):
    """Every address, as every role, on the palettes that differ — with nothing in the shop.

    A page that only draws because a record happens to exist is a page that breaks
    the first time the shop is new or the records are archived, and neither the
    seeded matrix nor any source scan can see that: the empty branch is markup
    nobody renders. What this asks of it is what the matrix asks of the other: a
    page (never a 5xx), the palette it was loaded with, and no word on it that the
    renderer should have kept to itself. The palettes are the per-mode sample,
    `STATE_THEMES` — the full ten run in the matrix above, and the guard beside
    the sample keeps it covering every derivation mode.
    """
    from tests.test_roles import _staff

    accounts = {role: _staff(db_session, f"empty-{role}", role) for role in ROLES}
    addresses = _unheld_addresses()
    offenders, answers, rendered = _walk(client, db_session, accounts, addresses,
                                         themes=STATE_THEMES)

    assert len(answers) == len(addresses) * len(ROLES), (len(answers), len(addresses))
    assert rendered > 0
    _assert_collections_draw_themselves(answers, addresses)
    assert not offenders, "\n".join(offenders[:40])


def test_every_page_survives_a_brand_new_install(client, db_session):
    """And the shop a first run leaves behind: the boot's own rows and nothing else.

    This is the state the app is in the first time the owner opens it — a schema,
    an owner account, the built-in templates switched off, and not one setting the
    shop has chosen. The empty shop above is what the test creates; this one is
    what the app creates, and it is the only pass that checks the app's own start
    of day renders as a shop.
    """
    from config import ADMIN_PASSWORD
    from models import StaffUser

    from tests.test_roles import _staff

    _boot_a_new_database(db_session)
    owner = db_session.query(StaffUser).filter(StaffUser.username == "owner").first()
    assert owner is not None, "the boot created no owner account"
    accounts = {
        "owner": (owner, ADMIN_PASSWORD),
        "manager": _staff(db_session, "fresh-manager", "manager"),
        "cashier": _staff(db_session, "fresh-cashier", "cashier"),
    }

    # The very first page, before anybody has chosen a palette: no theme is set
    # yet, so the default one has to be the one that answers.
    from tests.test_roles import _session_as

    _session_as(client, owner, ADMIN_PASSWORD)
    first = client.get("/admin/", follow_redirects=False)
    assert first.status_code == 200, first.status_code
    assert f'data-theme="{DEFAULT_THEME_ID}"' in first.text
    assert not _document_offenders(first.text, DEFAULT_THEME_ID, _palettes()), (
        "the first page a shop ever loads is already wrong")

    addresses = _unheld_addresses()
    offenders, answers, rendered = _walk(client, db_session, accounts, addresses,
                                         themes=STATE_THEMES)
    assert len(answers) == len(addresses) * len(ROLES), (len(answers), len(addresses))
    assert rendered > 0
    _assert_collections_draw_themselves(answers, addresses)
    assert not offenders, "\n".join(offenders[:40])


def test_the_shell_theme_read_is_one_query_and_the_cache_saves_the_rest(db_session):
    """The speed the cache buys, stated as a count rather than a hope.

    The whole reason the shell caches its theme is that a page render was paying
    for a settings read it did not need. So the claim is measured the way the
    other query-count guards are: with the cache cold, `get_theme` touches the
    database exactly once (one query answers theme, primary and secondary
    together); with it warm, not at all.
    """
    from sqlalchemy import event

    from services.themes import get_theme, invalidate_theme_cache

    invalidate_theme_cache()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", _record)
    try:
        get_theme()
        cold = len(statements)
        get_theme()
        warm = len(statements) - cold
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _record)

    assert cold == 1, f"expected one query for theme + custom colours, made {cold}"
    assert warm == 0, f"the warm cache must answer without the database, made {warm}"
    invalidate_theme_cache()
