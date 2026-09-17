"""The shell without a mouse, and the states a mouse never sees.

Three families of defect, all of them found live while writing these, and all of
them invisible to every guard that existed before this file:

* a menu that was *translated* off screen instead of hidden, so a keyboard reader
  on a narrow window tabbed into a closed drawer and through every entry of a menu
  nobody could see — `transform` does not remove anything from the tab order;
* a state colour that was *inherited*: a focus ring chosen for a white card is a
  ring nobody can see on the near-black topbar (1.00:1 in Kids Boutique), a hover
  pill equal to the sidebar it sits on (1.10:1), a logout label painted in the
  same hue as the sidebar behind it (1.09:1), a disabled control faded until its
  own label washed out (1.85:1), and a remove button whose white label sat on a
  hard-coded red at 3.34:1;
* keyboard-only controls that were not controls: the lightbox's ✕ was a `<div>`
  with a click handler and the picture that opens it was an `<img>`.

What the guards below require is what the shop needs: every pair a person must
read, measured in all ten palettes rather than eyeballed in one; state colours
derived from the palette that owns them; no colour literal in an interaction rule;
and a shell that can be driven from the keyboard alone — Enter to open the menu,
Escape to close it, a skip link before it, and nothing clickable that is not
reachable.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.themes import (
    DEFAULT_THEME_ID,
    _BASE,
    THEMES,
    contrast_ratio,
    interaction_tokens,
    theme_preview,
)

ROOT = Path(__file__).resolve().parents[1]

SHELL = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
WITHOUT_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)

# The state colours, and the properties the stylesheet may paint them with. A
# theme that omits one of these does not fall back to a sibling token — the
# declaration is dropped and the element inherits, which is the failure this whole
# file exists for. So they are derived in `services/themes.py` and never written
# into `_BASE` for a palette to inherit by accident.
STATE_TOKENS = (
    "--ring", "--ring-brand", "--focus-ring", "--hover-surface", "--sidebar-hover",
    "--disabled-surface", "--disabled-ink", "--danger",
    "--link", "--link-hover", "--brand-fill", "--brand-fill-dark",
    "--success-fill", "--success-fill-dark",
    "--success-ink", "--info-ink", "--warning-ink",
)

# Every colour used as text, the hue it is derived from, and the alert background
# (if any) that belongs to it. This mirrors `interaction_tokens`: a palette's hue is
# a hue until it is turned into an ink that reads, and the ink has to read on all
# four surfaces a page is made of, on the strongest tint of its hue beneath it, and
# on its own alert. `--candy` as text read 3.36:1; `--mint-dark` on a tint of itself
# 3.64:1; `--sky-dark` 3.77:1; the warning banner printed `--sunshine` on its own
# pale gold at 1.84:1.
TEXT_SURFACES = ("--card", "--bg", "--field-bg", "--surface-soft")
INK_HOMES = (
    ("--link", "--candy", ("--danger-bg",)),
    ("--link-hover", "--candy", ("--danger-bg",)),
    ("--success-ink", "--mint", ("--success-bg",)),
    ("--info-ink", "--sky", ()),
    ("--warning-ink", "--sunshine", ("--warning-bg",)),
)

# Where a palette's hue belongs: a boundary, a tint, a gradient, a bar — never the
# text a person reads, and never the fill a label sits on. Those are the derived inks
# and the derived fills, because a hue is what a shop picked for its brand, not a
# legibility. `--persimmon` is the exception and is not in this list: it is the one
# hue the catalogue chose *to be read* — it is the attention colour, and its own
# guard measures it on the card and on a tint of itself in all ten palettes.
HUE_TOKENS = ("--candy", "--candy-dark", "--mint", "--mint-dark", "--sky", "--sky-dark",
              "--sunshine", "--lavender")
# The colours that are text: the derivatives that replaced those hues.
INK_TOKENS = ("--link", "--link-hover", "--success-ink", "--info-ink", "--warning-ink")

# Every colour a state paints text or a boundary with. A literal here is a colour
# chosen for one palette being worn by all ten.
STATE_PSEUDO = re.compile(r":(hover|focus|focus-visible|focus-within|disabled|active|checked|target)\b")
COLOUR_PROPERTY = re.compile(
    r"^(color|background|background-color|background-image|border-color|outline-color)$")
COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\b(?:white|black|red|blue|green|gray|grey|silver|maroon|navy|teal|olive|orange|purple|lime|aqua|fuchsia)\b", re.I)
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules() -> list[tuple[str, str]]:
    """Every `selector { declarations }` pair, media queries flattened: a rule
    inside `@media` is still a rule, and the innermost braces are the ones that
    matter."""
    return [(selector.strip(), body) for selector, body in RULE.findall(WITHOUT_COMMENTS)]


def _declarations(body: str) -> list[tuple[str, str]]:
    pairs = []
    for piece in body.split(";"):
        if ":" not in piece:
            continue
        name, _, value = piece.partition(":")
        pairs.append((name.strip().lower(), value.strip()))
    return pairs


def _brighten(colour: str, factor: float) -> str:
    """`filter: brightness(f)` on a flat colour, in sRGB, the way a browser
    composites it — so a hover's own numbers can be measured rather than trusted."""
    channels = [min(255, round(int(colour[index:index + 2], 16) * factor))
                for index in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(channels)


def _mix(first: str, second: str, amount: float) -> str:
    channels = [round(int(first[index:index + 2], 16) * (1 - amount)
                      + int(second[index:index + 2], 16) * amount) for index in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(channels)


# ── The states, measured in all ten palettes ──────────────────────────────────
#
# Each entry: what a person has to read, the floor, and how to get the pair out of
# a palette. The floors are the ones a state needs to be a state: 3:1 for a ring
# and for the boundary of a control (WCAG 1.4.11), 4.5:1 for text, 1.2:1 for two
# surfaces that only have to be told apart — a hovered row is not a label.

FLOORS: tuple[tuple[str, float, object], ...] = (
    ("the focus ring on the surfaces a field or a card is drawn on", 3.0,
     lambda t: min(contrast_ratio(t["--ring"], t[s]) for s in ("--card", "--field-bg", "--bg"))),
    ("the focus ring on the topbar and the sidebar", 3.0,
     lambda t: min(contrast_ratio(t["--ring-brand"], t[s])
                   for s in ("--topbar-start", "--topbar-end", "--sidebar-bg", "--sidebar-active"))),
    ("a hovered row against the card it lies on", 1.2,
     lambda t: contrast_ratio(t["--hover-surface"], t["--card"])),
    ("a hovered row against the page behind the card", 1.2,
     lambda t: contrast_ratio(t["--hover-surface"], t["--bg"])),
    ("the hovered sidebar item against the sidebar", 1.2,
     lambda t: contrast_ratio(t["--sidebar-hover"], t["--sidebar-bg"])),
    ("the sidebar's own label on that hovered item", 4.5,
     lambda t: contrast_ratio(t["--sidebar-text"], t["--sidebar-hover"])),
    ("that label on the item that is current", 4.5,
     lambda t: contrast_ratio(t["--sidebar-text"], t["--sidebar-active"])),
    ("the sign-out label, in both of its fills", 4.5,
     lambda t: min(contrast_ratio(t["--sidebar-text"], t["--sidebar-hover"]),
                   contrast_ratio(t["--sidebar-text"], _mix(t["--sidebar-bg"], t["--candy"], 0.12)))),
    ("a disabled label on the disabled surface", 4.5,
     lambda t: contrast_ratio(t["--disabled-ink"], t["--disabled-surface"])),
    ("a disabled glyph on a card — the calendar's own day", 4.5,
     lambda t: contrast_ratio(t["--disabled-ink"], t["--card"])),
    ("a destructive button's label on its fill", 4.5,
     lambda t: contrast_ratio(t["--danger"], t["--button-text"])),
    ("the attention colour on the tint its badges wear", 4.5,
     lambda t: contrast_ratio(t["--persimmon"], _mix(t["--card"], t["--persimmon"], 0.20))),
    ("the attention colour on the paler tint it hovers to", 4.5,
     lambda t: contrast_ratio(t["--persimmon"], _mix(t["--card"], t["--persimmon"], 0.08))),
    ("a ghost button's label on the field it hovers over", 4.5,
     lambda t: contrast_ratio(t["--ink"], t["--field-bg"])),
    ("a link on every surface one can be read on", 4.5,
     lambda t: min(contrast_ratio(t["--link"], t[s])
                   for s in ("--card", "--bg", "--field-bg", "--surface-soft"))),
    ("that link where it hovers", 4.5,
     lambda t: min(contrast_ratio(t["--link-hover"], t[s])
                   for s in ("--card", "--bg", "--field-bg", "--surface-soft"))),
    ("brand-coloured text on the strongest tint of the brand beneath it", 4.5,
     lambda t: contrast_ratio(t["--link"], _mix(t["--card"], t["--candy"], 0.18))),
    ("a label on the brand fill, both ends of its gradient", 4.5,
     lambda t: min(contrast_ratio(t[fill], t["--button-text"])
                   for fill in ("--brand-fill", "--brand-fill-dark"))),
    ("a label on the success fill, both ends of its gradient", 4.5,
     lambda t: min(contrast_ratio(t[fill], t["--button-text"])
                   for fill in ("--success-fill", "--success-fill-dark"))),
)


def test_every_state_reads_in_all_ten_palettes():
    """Measured, palette by palette, rather than looked at on the default one.

    Every one of these failed somewhere before it was derived: the ring was
    1.00:1 on the Kids Boutique topbar, the hovered menu item 1.10:1 against its
    sidebar, the sign-out label 1.09:1 against the fill it sat on, the disabled
    label 1.85:1 once the whole control was faded to 62%, and the basket's remove
    button 3.34:1 with the white label its hover swapped in.
    """
    assert len(THEMES) == 10
    failures = []
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        for what, floor, measure in FLOORS:
            value = measure(tokens)
            if value < floor:
                failures.append(f"{theme_id}: {what} reads {value:.2f}:1, floor {floor}")
    assert not failures, "\n".join(failures)


def test_the_state_colours_are_derived_from_the_palette_not_inherited():
    """A state colour written into `_BASE` is one every palette inherits, whether
    or not it fits — which is exactly how the missing `--persimmon` survived: it
    fell back to `:root` and painted a plausible colour nobody had chosen.

    So none of these live in `_BASE`, all ten palettes compute their own, and the
    derivation follows the surfaces: move a palette's card and its ring moves with
    it. A custom brand gets its own too, from the two colours the shop picked.
    """
    for token in STATE_TOKENS:
        assert token not in _BASE, f"{token} is inherited by every palette instead of derived"

    for theme_id in THEMES:
        tokens = THEMES[theme_id]["tokens"]
        assert set(STATE_TOKENS) <= set(tokens), theme_id

    # The derivation reads the palette rather than returning a constant: move the
    # seed the ring is built from and the ring follows it.
    moved = dict(THEMES[DEFAULT_THEME_ID]["tokens"])
    reference = interaction_tokens(moved)["--ring"]
    moved["--sky"] = "#B03060"
    assert interaction_tokens(moved)["--ring"] != reference, (
        "the ring ignored the palette colour it is derived from")
    # …and it is a value of its own in each of the ten, not one shared constant.
    assert len({THEMES[theme]["tokens"]["--hover-surface"] for theme in THEMES}) > 1
    assert len({THEMES[theme]["tokens"]["--ring"] for theme in THEMES}) > 1

    # A shop's own two colours: the ring follows the brand, not the default palette.
    custom = theme_preview("custom-brand", {"primary": "#003D66", "secondary": "#0F766E"})["tokens"]
    assert custom["--ring"] != THEMES[DEFAULT_THEME_ID]["tokens"]["--ring"]
    for what, floor, measure in FLOORS:
        assert measure(custom) >= floor, f"custom brand: {what} reads {measure(custom):.2f}:1"


def test_the_sidebar_tells_a_hovered_item_from_the_one_that_is_current():
    """Two states that were drawn with one colour, and the colour was almost the
    sidebar itself: measured 1.10:1 in Kids Boutique, 1.37:1 in Atelier.

    A palette guard alone cannot see this — it can measure `--sidebar-hover` all
    day while the stylesheet goes on painting `--sidebar-active` underneath a
    hover — so the rule reads which token the hover actually uses, and requires the
    current page to be marked by something that is not its fill.
    """
    stylesheet = dict(_rules())
    hovered = dict(_declarations(stylesheet[".sidebar-nav a:hover"]))
    assert hovered.get("background", "").strip() == "var(--sidebar-hover)", (
        "the sidebar's hover paints something other than the state colour derived for it")

    current = dict(_declarations(stylesheet[".sidebar-nav a.active, .sidebar-nav a.active:hover"]))
    assert current.get("font-weight") == "800" or "box-shadow" in current, (
        "the current page is marked by its fill alone, which is all but invisible")
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        assert tokens["--sidebar-hover"] != tokens["--sidebar-active"], (
            f"{theme_id} draws a hovered item and the current one in the same colour")


# Every place a declaration can be written: the stylesheet, a template's own
# `<style>` block, and a `style="…"` attribute on an element. One list, so the rule
# below can look at all of them the same way.
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
STYLE_ATTRIBUTE = re.compile(r'style="([^"]*)"')


def _declaration_chunks(source: str) -> list[str]:
    chunks = [body for _selector, body in _rules_of(source)]
    chunks += STYLE_BLOCK.findall(source) + STYLE_ATTRIBUTE.findall(source)
    return chunks


def _rules_of(source: str) -> list[tuple[str, str]]:
    return [(selector.strip(), body) for selector, body in RULE.findall(
        re.sub(r"/\*.*?\*/", "", source, flags=re.S))]


def _template_sources() -> list[tuple[str, str]]:
    return [(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8"))
            for path in sorted((ROOT / "templates").rglob("*.html"))]


def test_every_colour_text_is_read_in_reads_where_it_lands():
    """The floors, for the inks that replaced the palette hues as text.

    Each is derived against the surfaces text is actually read on: the four the
    page is made of, the strongest tint of its own hue that a badge paints under
    it, and the alert background that belongs to it — the warning banner is pale
    gold, and gold text on it read 1.84:1 in every palette.
    """
    failures = []
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        for ink, hue, alerts in INK_HOMES:
            surfaces = [tokens[name] for name in TEXT_SURFACES + alerts]
            surfaces.append(_mix(tokens["--card"], tokens[hue], 0.18))
            for surface in surfaces:
                value = contrast_ratio(tokens[ink], surface)
                if value < 4.5:
                    failures.append(f"{theme_id}: {ink} on {surface} reads {value:.2f}:1")
    assert not failures, "\n".join(failures)


def test_the_brand_hue_is_never_the_text_a_person_reads():
    """`--candy` is the colour a shop picked for its brand, and it was also the
    colour of every link, figure and chip label in the app — 3.36:1 on the card in
    Kids Boutique, 4.12:1 as `--candy-dark` in Midnight. The same for the other
    three voices: `--mint-dark`, `--sky-dark` and `--sunshine` were read as they
    came out of the palette, and none of them reads everywhere it was used. The
    hues keep their place in borders, tints, gradients and bars; the text is a
    derived ink.
    """
    pattern = re.compile(r"(?<![-\w])color:\s*var\((%s)\)" % "|".join(HUE_TOKENS))
    offenders = []
    for selector, body in _rules():
        for name, value in _declarations(body):
            if name in {"color", "fill"} and re.search(r"var\((%s)\)" % "|".join(HUE_TOKENS), value):
                offenders.append(f"style.css: {selector} {{ {name}: {value} }}")
    for relative, source in _template_sources():
        for match in pattern.finditer(source):
            line = source[:match.start()].count("\n") + 1
            offenders.append(f"{relative}:{line}: {match.group(0)}")
    assert not offenders, (
        "these paint text in the brand hue itself, which is not a colour chosen to be "
        "read — use the derived ink:\n" + "\n".join(offenders))

    # The link rule itself is the one that matters most, and it is named here so a
    # later edit cannot quietly put the hue back.
    link = dict(_declarations(dict(_rules())["a"]))
    assert link.get("color", "").strip() == "var(--link)", link


def test_the_inks_paint_text_and_the_hues_paint_the_rest():
    """The two halves of the palette have one job each, and a mechanical edit — this
    one, for instance, which swept `color:` with a string replace and took two
    `border-color:` declarations with it — is exactly how they get swapped. An ink is
    text: it has been measured against the surfaces text is read on, not as a
    boundary a person has to spot.
    """
    inks = re.compile(r"var\((%s)\)" % "|".join(INK_TOKENS))
    offenders = []
    for selector, body in _rules():
        for name, value in _declarations(body):
            if name == "color" or not name.endswith("-color"):
                continue
            if inks.search(value):
                offenders.append(f"style.css: {selector} {{ {name}: {value} }}")
    for relative, source in _template_sources():
        for match in re.finditer(r"[-a-z]color:\s*var\((?:%s)\)" % "|".join(INK_TOKENS), source):
            line = source[:match.start()].count("\n") + 1
            offenders.append(f"{relative}:{line}: {match.group(0)}")
    assert not offenders, (
        "an ink is painting a boundary rather than text — the palette's own hue belongs "
        "there:\n" + "\n".join(offenders))
    # …and the guard is looking at declarations at all.
    assert any(name.endswith("-color") for _selector, body in _rules()
               for name, _value in _declarations(body))


def test_a_label_is_never_painted_on_the_raw_brand_hue():
    """The same rule for fills: a white label on `--candy` measured 3.34:1 on the
    light palettes and 2.10:1 on the dark one, because the hue was picked for a
    brand and not for a label. A labelled surface is `--brand-fill`,
    `--success-fill` or `--danger`, each derived against the label it carries.
    """
    fills = re.compile(r"var\((%s)\)" % "|".join(HUE_TOKENS))
    offenders = []
    chunks = [("style.css", body) for _selector, body in _rules()]
    for relative, source in _template_sources():
        chunks += [(relative, chunk) for chunk in _declaration_chunks(source)]
    for where, chunk in chunks:
        pairs = dict(_declarations(chunk))
        background = " ".join(value for name, value in _declarations(chunk)
                              if name.startswith("background"))
        if not fills.search(background):
            continue
        if pairs.get("color", "").strip() in {"var(--button-text)", "#fff", "#ffffff", "white"}:
            offenders.append(f"{where}: a label on {background.strip()[:60]}")
    assert not offenders, (
        "these put a label on the brand hue instead of the fill derived for it:\n"
        + "\n".join(offenders))


def test_no_colour_literal_survives_in_an_interaction_rule():
    """A state that names its own colour is a state that only fits one palette.

    Three were live: `tbody tr:hover { background: rgba(255,107,138,0.04) }` (a
    pink that vanished on the dark palettes and was a hard-coded brand colour on
    the light ones), `.sidebar-logout:hover { background: #ffe3e1 }` (a light pink
    under a dark sidebar), and `.btn-remove:hover { background: #FF4757 }` (a red
    under a white label). Each looked deliberate and was wrong for nine palettes.
    """
    offenders = []
    for selector, body in _rules():
        if not STATE_PSEUDO.search(selector):
            continue
        for name, value in _declarations(body):
            if not COLOUR_PROPERTY.match(name):
                continue
            if COLOUR_LITERAL.search(value):
                offenders.append(f"{selector} {{ {name}: {value} }}")
    assert not offenders, (
        "these interaction states paint a colour of their own instead of the theme's:\n"
        + "\n".join(offenders))


def test_no_rule_takes_a_focus_ring_away_without_replacing_it():
    """`outline: none` is only honest when the ring it removes is drawn some other
    way in the same rule — otherwise the one cue a keyboard reader has is gone."""
    offenders = []
    for selector, body in _rules():
        pairs = _declarations(body)
        kills = [value for name, value in pairs if name == "outline" and value in {"none", "0"}]
        if not kills:
            continue
        replaced = any(name == "box-shadow" and "--focus-ring" in value for name, value in pairs) \
            or any(name == "outline-color" for name, value in pairs)
        if not replaced:
            offenders.append(selector)
    assert not offenders, (
        "these rules remove the outline a keyboard reader follows and draw nothing "
        "in its place:\n" + "\n".join(offenders))


def test_a_filled_button_changes_on_hover_without_repainting_its_label():
    """A filled button's hover moves the control; it does not repaint the surface its
    label is read against.

    `filter: brightness(1.08)` on a primary button raised the candy fill and took
    the white label from 4.5:1 down to 2.32:1 on the dark palette. Darkening instead
    is not the answer either, and that is the point of measuring rather than
    reasoning: `brightness` multiplies the label too, so 0.92 took the same label to
    4.31:1 where it had been 4.48:1. The cue is the lift `.btn:hover` draws for every
    button — a state a colour-blind reader keeps as well.
    """
    filled = {".btn-primary", ".btn-success", ".btn-danger"}
    checked = 0
    for selector, body in _rules():
        name = selector.split(":")[0].strip()
        if name not in filled or ":hover" not in selector:
            continue
        checked += 1
        for declaration, value in _declarations(body):
            assert declaration not in {"color", "background", "background-color",
                                       "background-image", "filter"}, (
                f"{selector} repaints `{declaration}` under its label: {value}")
    # The three of them are read, even if a hover is later added back: a guard that
    # looked at nothing would pass.
    assert checked == 0, f"{checked} filled buttons now have a hover of their own"

    # …and the hover those buttons rely on is a real change, not a no-op.
    lift = dict(_rules())
    assert ".btn:hover" in lift, "the shared button hover is gone"
    declarations = dict(_declarations(lift[".btn:hover"]))
    assert declarations.get("transform") or declarations.get("box-shadow"), (
        "`.btn:hover` changes nothing a reader can see")
    # A filled button's label and fill are still the pairs the palette guard
    # measures, in all ten, so nothing below them moved either.
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        for fill in ("--candy", "--candy-dark", "--mint", "--mint-dark", "--danger"):
            assert contrast_ratio(tokens["--button-text"], tokens[fill]) > 1, (theme_id, fill)


# ── The shell, driven from the keyboard ───────────────────────────────────────

PAGES = ("/admin/", "/sales/new", "/admin/customers", "/admin/sms", "/admin/cashbox",
         "/admin/try-on", "/admin/try-on/saved", "/admin/settings/appearance",
         "/admin/mobile")


class _Interactive:
    """Every click handler in a document, and what it is attached to."""

    TAG = re.compile(r"<([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?)*)\s*/?>")
    ATTRIBUTE = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")

    @classmethod
    def walk(cls, document: str) -> list[dict]:
        found = []
        for tag, raw in cls.TAG.findall(document):
            attributes = {name.lower(): (a or b or c)
                          for name, a, b, c in cls.ATTRIBUTE.findall(raw)}
            found.append({"tag": tag.lower(), "attributes": attributes})
        return found


def _signed_in(client, db_session, role: str):
    from tests.test_roles import _session_as, _staff

    user, password = _staff(db_session, f"keyboard-{role}", role)
    _session_as(client, user, password)
    return user


def test_every_click_handler_sits_on_something_a_keyboard_can_reach(client, db_session):
    """A `<div onclick>` is invisible to a keyboard, and so is an `<img onclick>`.

    Both were live on the try-on pages: the ✕ that closes the lightbox was a div,
    and the picture that opens it was an image with a click handler, so full size
    was reachable with a mouse only. The rule is the markup's own: if it is clicked
    it has to be a button, a link, a form control, or say what it is with `role`
    and take focus with `tabindex`.
    """
    _signed_in(client, db_session, "owner")
    reachable = {"button", "a", "input", "select", "textarea", "summary"}
    offenders, checked, controls = [], 0, 0
    for path in PAGES:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, (path, response.status_code)
        for element in _Interactive.walk(response.text):
            attributes = element["attributes"]
            if element["tag"] in reachable and attributes.get("type") != "hidden":
                controls += 1
            if "onclick" not in attributes:
                continue
            checked += 1
            if element["tag"] in reachable:
                continue
            if "role" in attributes and "tabindex" in attributes:
                continue
            if element["tag"] == "div" and attributes.get("class") == "lightbox-scrim":
                # A convenience for the mouse, not the only way out: this is the
                # scrim's click-to-dismiss, and the same document has to offer the
                # keyboard the ✕ button and Escape (asserted in its own test below).
                assert "lightbox-close" in response.text, (
                    f"{path}: the scrim is click-only and nothing else dismisses it")
                continue
            offenders.append(f"{path}: <{element['tag']} onclick=…> is not in the tab order")
    assert checked >= 5, f"only {checked} click handlers found — the walk is not looking at the shell"
    assert controls >= 300, f"only {controls} controls walked — the pages are not being read"
    assert not offenders, "\n".join(offenders)


def test_the_lightbox_is_a_dialog_a_keyboard_can_work():
    """It opens from a button, closes with Escape, and puts the keyboard inside.

    Read from the two pages that own it: an image and a div with handlers passed
    every other check in the suite, because nothing had ever asked whether the
    thing being clicked could be clicked without a mouse.
    """
    openers = closers = 0
    for name in ("tryon.html", "tryon_saved.html"):
        source = (ROOT / "templates" / "admin" / name).read_text(encoding="utf-8")
        for element in _Interactive.walk(source):
            attributes = element["attributes"]
            classes = (attributes.get("class") or "").split()
            if "lightbox-open" in classes:
                assert element["tag"] == "button", f"{name}: the picture is a <{element['tag']}>"
                openers += 1
            if "lightbox-close" in classes:
                assert element["tag"] == "button", f"{name}: the ✕ is a <{element['tag']}>"
                closers += 1
        assert "e.key === 'Escape'" in source, f"{name}: Escape does not close the lightbox"
        assert "lightbox-close').focus()" in source, (
            f"{name}: opening the lightbox leaves the keyboard out on the page behind it")
    assert (openers, closers) == (2, 2), (openers, closers)


def test_the_skip_link_is_the_first_thing_the_keyboard_reaches(client, db_session):
    """Without it the first Tab is the menu button, and a reader walks the whole
    sidebar — up to 22 entries on the owner's menu — before the page they opened."""
    _signed_in(client, db_session, "owner")
    document = client.get("/admin/", follow_redirects=False).text
    body = document.split("<body", 1)[1]

    skip = re.search(r'<a class="skip-link" href="#([\w-]+)"', body)
    assert skip, "the shell has no skip link"
    target = skip.group(1)
    assert re.search(rf'<main[^>]*id="{target}"[^>]*tabindex="-1"', body), (
        f"#{target} is not a focusable main region for the skip link to land on")

    first = next((element for element in _Interactive.walk(body)
                  if element["tag"] in ("a", "button", "input", "select", "textarea")
                  and "hidden" not in element["attributes"]
                  and element["attributes"].get("type") != "hidden"), None)
    assert first is not None, "no focusable control in the shell"
    assert first["attributes"].get("class") == "skip-link", (
        f"the first focusable control is <{first['tag']} {first['attributes']}>, not the skip link")


def test_the_closed_drawer_is_out_of_the_tab_order():
    """`transform: translateX(110%)` moves a panel off the screen without removing
    it from the tab order, so a keyboard reader on a narrow window walked through
    the entries of a menu that was not there. Hiding it is the fix; the open state
    has to bring it back.
    """
    match = re.search(r"@media\s*\(max-width:\s*900px\)\s*\{", WITHOUT_COMMENTS)
    assert match, "the narrow-screen block that draws the drawer is gone"
    depth, start = 1, match.end()
    index = start
    while depth and index < len(WITHOUT_COMMENTS):
        depth += {"{": 1, "}": -1}.get(WITHOUT_COMMENTS[index], 0)
        index += 1
    narrow = WITHOUT_COMMENTS[start:index - 1]

    closed = re.search(r"\.app-sidebar\s*\{([^}]*)\}", narrow)
    assert closed, "the drawer's closed state is not in the narrow-screen block"
    assert "visibility: hidden" in closed.group(1), (
        "the closed drawer is only moved off screen, so its entries are still tabbable")
    opened = re.search(r"sidebar-open\s+\.app-sidebar\s*\{([^}]*)\}", narrow)
    assert opened and "visibility: visible" in opened.group(1), (
        "the open drawer is never brought back into the tab order")


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                               reason="a JavaScript engine is needed to drive the shell")

# The shell's own script, run against a document small enough to record what it
# does: a toggle that is clicked, a key that is pressed, and what the body and the
# button look like afterwards. Written as a harness rather than an assertion on the
# source because "Escape closes the menu" is a behaviour, and a grep for the word
# Escape would pass a handler that forgets to close anything.
SHELL_HARNESS = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[2], 'utf8');
const script = source.match(/\(function \(\) \{[\s\S]*?\n    \}\)\(\);/)[0];

function element(id, classes) {
  return {
    id, className: id, classes: new Set(classes), attributes: {}, listeners: {}, focused: 0,
    scrollTop: 0, offsetTop: 0, offsetHeight: 10, clientHeight: 40,
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    focus() { this.focused += 1; },
    fire(type, event) { (this.listeners[type] || []).forEach(handler => handler(event || {})); },
  };
}

const toggle = element('menu-toggle', []);
const body = element('body', []);
body.classList = {
  toggle(name, on) { on ? body.classes.add(name) : body.classes.delete(name); },
  contains(name) { return body.classes.has(name); },
};
const calls = { raf: 0, stored: {} };
const document = {
  body,
  listeners: {},
  getElementById: id => (id === 'menu-toggle' ? toggle : null),
  querySelector: selector => (selector === '.app-sidebar' || selector === '.sidebar-nav'
    ? element(selector, []) : null),
  querySelectorAll: () => [{ addEventListener() {} }],
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
  fire(type, event) { (this.listeners[type] || []).forEach(handler => handler(event || {})); },
};
global.document = document;
global.sessionStorage = { getItem: () => null, setItem: (key, value) => { calls.stored[key] = value; } };
global.requestAnimationFrame = () => { calls.raf += 1; };

new Function(script)();

const report = { afterClick: null, afterEscape: null, afterOtherKey: null };
toggle.fire('click', {});
report.afterClick = {
  open: body.classes.has('sidebar-open'),
  expanded: toggle.attributes['aria-expanded'],
  label: toggle.attributes['aria-label'],
};
document.fire('keydown', { key: 'Escape' });
report.afterEscape = {
  open: body.classes.has('sidebar-open'),
  expanded: toggle.attributes['aria-expanded'],
  label: toggle.attributes['aria-label'],
  focused: toggle.focused,
};
toggle.fire('click', {});
document.fire('keydown', { key: 'x' });
report.afterOtherKey = { open: body.classes.has('sidebar-open') };
document.fire('keydown', { key: 'Escape' });
report.secondEscape = { open: body.classes.has('sidebar-open'), focused: toggle.focused };
console.log(JSON.stringify(report));
"""


@needs_node
def test_the_shell_opens_and_closes_from_the_keyboard_alone(tmp_path):
    """Enter opens the menu, Escape closes it, and the button says which it will
    do next. The drawer's only other exit was a click on the overlay behind it, so
    a keyboard reader who opened the menu could not get out of it.
    """
    harness = tmp_path / "shell.js"
    harness.write_text(SHELL_HARNESS, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(harness), str(ROOT / "templates" / "base.html")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    assert report["afterClick"]["open"] is True
    assert report["afterClick"]["expanded"] == "true"
    assert report["afterClick"]["label"] != report["afterEscape"]["label"], (
        "the menu button says the same thing open and closed")
    assert report["afterEscape"]["open"] is False
    assert report["afterEscape"]["expanded"] == "false"
    assert report["afterEscape"]["focused"] == 1, (
        "Escape closed the drawer but left the keyboard nowhere in particular")
    assert report["afterOtherKey"]["open"] is True, "a key that is not Escape closed the drawer"
    assert report["secondEscape"]["open"] is False
    assert report["secondEscape"]["focused"] == 2


def test_the_shell_still_carries_what_the_palette_guard_expects():
    """The rules above are read out of the stylesheet and the shell; this states
    that what they read is still there, so none of them passes on an empty file."""
    assert "--focus-ring" in CSS and "--ring-brand" in CSS
    assert ".skip-link:focus" in WITHOUT_COMMENTS
    assert DEFAULT_THEME_ID in THEMES
    assert ".app-topbar :focus-visible" in WITHOUT_COMMENTS
