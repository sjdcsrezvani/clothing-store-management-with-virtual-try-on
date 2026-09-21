from __future__ import annotations

import json
import re
import time
from typing import Any

from models import Settings

DEFAULT_THEME_ID = "operations-light"
THEME_SETTING_KEY = "ui_theme"
CUSTOM_PRIMARY_KEY = "theme_custom_primary"
CUSTOM_SECONDARY_KEY = "theme_custom_secondary"

# The shell theme changes only through the appearance form, so the value every
# page render reads is cached briefly and invalidated at that one writer —
# the same discipline the store profile keeps.
_SHELL_THEME_TTL = 60.0
_SHELL_THEME_CACHE: dict[str, Any] = {"data": None, "at": 0.0}
# What the Custom Brand colour pickers start on, and what a form that carries no
# colour falls back to. One source for both, so the page and the service cannot
# disagree about the brand a shop gets before it chooses one.
DEFAULT_CUSTOM_PRIMARY = "#C94B68"
DEFAULT_CUSTOM_SECONDARY = "#197A8C"

# `--persimmon` is the panel's attention hue: what a figure turns when it is not
# an error but must be looked at — money owed, a payment past its date, a message
# that failed, text past its SMS limit. It is deliberately neither `--candy` (the
# brand/primary, on every button and link) nor the alarm pair, so an overdue debt
# never reads as something clickable. It is chosen dark enough to be legible as
# text both on a card and on the 20% tint the state badges mix from it.
#
# Every theme must define it. Eleven declarations in style.css reference it, and
# an undefined custom property does not fall back to a sibling — the declaration
# becomes invalid at computed-value time and the element silently inherits its
# parent's colour, which is how this token went missing without anything looking
# broken. tests/test_themes.py now fails if a theme omits any property the
# stylesheet reads.
_BASE = {
    "--candy": "#C94B68",
    "--candy-dark": "#A83A54",
    "--sky": "#197A8C",
    "--sky-dark": "#125B69",
    "--sunshine": "#D69A1D",
    "--persimmon": "#AD3100",
    "--mint": "#21864B",
    "--mint-dark": "#176A3A",
    "--lavender": "#6C5CE7",
    "--bg": "#F5F7F8",
    "--card": "#FFFFFF",
    "--ink": "#1D2933",
    "--ink-soft": "#586571",
    "--rule": "#D9E0E5",
    "--surface-soft": "#F8FAFB",
    "--field-bg": "#FFFFFF",
    "--table-header": "#F3F6F8",
    "--card-accent": "linear-gradient(90deg, var(--candy), var(--sky))",
    "--radius": "8px",
    "--radius-sm": "6px",
    "--sidebar-bg": "#17232B",
    "--sidebar-active": "#2C414C",
    # The text on a filled brand surface — a primary button, a count chip. The
    # stylesheet declares a white default in `:root`, but a page that does *not*
    # load the stylesheet has only the tokens its theme hands it inline, and the
    # phone capture tool is one of those: without this it read `--button-text`
    # from nowhere and painted the label in the inherited ink. So every theme
    # supplies it, and `custom_tokens` still derives its own for Custom Brand.
    #
    # Its one stylesheet consumer is now the destructive button's label
    # (`.btn-danger`), and `--danger` is derived *against this value* — so the
    # pair is designed together and measured together in the keyboard guard.
    # It is the pole the danger fill walks to, not a universal button ink: the
    # brand and success fills each derive their own label in `filled_pair`.
    "--button-text": "#FFFFFF",
    "--topbar-start": "#17232B",
    "--topbar-end": "#334C59",
    # What stays legible on that bar is derived per palette in
    # `interaction_tokens` — a bar built from the theme's own hues cannot
    # assume white reads on it (Kids Boutique's candy end measured 3.19:1).
    "--success-bg": "#E8F5ED",
    "--warning-bg": "#FFF5D9",
    "--danger-bg": "#FDEBEC",
    # The dim behind a photo lightbox or a blocking overlay, and what stays
    # legible on it. A picture is viewed against something, and the theme's card
    # is the wrong shape for that — a light surface in the light palettes has to
    # dim, not brighten, or the photo is washed out.
    "--scrim": "rgba(12, 10, 34, 0.92)",
    # What stays legible on that dim. The scrim is near-black in every palette
    # by design (a picture is viewed against something that dims, not
    # brightens), so white reads on it wherever it is laid — over a white page
    # the composite still measures ~13:1. A designed pair, declared together;
    # if a palette ever lightens its scrim, this constant is the first thing
    # to move with it.
    "--scrim-text": "#FFFFFF",
    "--density-scale": "1",
}

THEMES: dict[str, dict[str, Any]] = {
    "operations-light": {
        "name": "Operations Light",
        "description": "Clear merchant workspace for everyday store operations.",
        "mode": "light",
        "tokens": dict(_BASE),
    },
    "pos-focus": {
        "name": "POS Focus",
        "description": "High-contrast checkout-oriented interface for fast sales.",
        "mode": "dark-shell",
        "tokens": {**_BASE, "--candy": "#0F766E", "--candy-dark": "#0B5E58", "--sky": "#0891B2", "--sky-dark": "#0E7490", "--mint": "#16A34A", "--mint-dark": "#15803D", "--sidebar-bg": "#111827", "--sidebar-active": "#1F4B51", "--topbar-start": "#111827", "--topbar-end": "#164E63", "--radius": "6px", "--radius-sm": "4px", "--field-bg": "#F8FAFC", "--table-header": "#E8F2F4", "--card-accent": "linear-gradient(90deg, #0F766E, #0891B2)"},
    },
    "premium-navy": {
        "name": "Premium Navy",
        "description": "Polished boutique styling with navy and terracotta accents.",
        "mode": "light",
        "tokens": {**_BASE, "--candy": "#B65345", "--candy-dark": "#8F3D33", "--sky": "#284B63", "--sky-dark": "#1D374A", "--sunshine": "#B88932", "--bg": "#F7F5F1", "--surface-soft": "#FCFAF7", "--rule": "#E3DED6", "--sidebar-bg": "#172A3A", "--sidebar-active": "#2C485E", "--topbar-start": "#172A3A", "--topbar-end": "#35566B", "--radius": "6px", "--field-bg": "#FFFDF9", "--table-header": "#F0ECE5", "--card-accent": "linear-gradient(90deg, #B65345, #284B63)"},
    },
    "atelier": {
        "name": "Atelier",
        "description": "Quiet paper surfaces, charcoal type, and burgundy actions.",
        "mode": "light",
        "tokens": {**_BASE, "--candy": "#8D354A", "--candy-dark": "#6E293A", "--sky": "#52606D", "--sky-dark": "#3D4A55", "--sunshine": "#A17C35", "--bg": "#F3F1ED", "--card": "#FFFDF9", "--surface-soft": "#F9F7F2", "--rule": "#DED9CF", "--sidebar-bg": "#292826", "--sidebar-active": "#4A3A3B", "--topbar-start": "#292826", "--topbar-end": "#514043", "--radius": "4px", "--radius-sm": "4px", "--field-bg": "#FFFDF9", "--table-header": "#EEEAE2", "--card-accent": "linear-gradient(90deg, #8D354A, #A17C35)"},
    },
    "kids-boutique": {
        "name": "Kids Boutique",
        "description": "The colorful, playful RaiKids identity with emoji accents.",
        "mode": "light",
        "tokens": {**_BASE, "--candy": "#E85D75", "--candy-dark": "#C84660", "--sky": "#2A9DB5", "--sky-dark": "#217D91", "--sunshine": "#E7A932", "--mint": "#35A853", "--mint-dark": "#278640", "--lavender": "#805AD5", "--bg": "#FFF7F2", "--card": "#FFFFFF", "--ink": "#2D2D2D", "--ink-soft": "#756F6A", "--rule": "#F0E4D8", "--surface-soft": "#FFFBF8", "--field-bg": "#FFFFFF", "--table-header": "#FFFAF5", "--card-accent": "linear-gradient(90deg, #E85D75, #E7A932, #35A853, #2A9DB5)", "--sidebar-bg": "#FFFFFF", "--sidebar-text": "#5B5551", "--sidebar-active": "#FFF0F4", "--topbar-start": "#E85D75", "--topbar-end": "#2A9DB5", "--success-bg": "#E8F8EC", "--warning-bg": "#FFF3D6", "--danger-bg": "#FFE8E8", "--radius": "14px", "--radius-sm": "8px"},
    },
    "ocean-commerce": {
        "name": "Ocean Commerce",
        "description": "Cool white surfaces with calm blue and cyan navigation.",
        "mode": "light",
        "tokens": {**_BASE, "--candy": "#0E7490", "--candy-dark": "#155E75", "--sky": "#2563EB", "--sky-dark": "#1D4ED8", "--mint": "#16805A", "--mint-dark": "#126548", "--bg": "#F2F7FA", "--surface-soft": "#F7FBFD", "--rule": "#D4E2EA", "--sidebar-bg": "#123047", "--sidebar-active": "#1D506B", "--topbar-start": "#123047", "--topbar-end": "#1B6078", "--field-bg": "#F8FCFE", "--table-header": "#EAF5F8", "--card-accent": "linear-gradient(90deg, #0E7490, #2563EB)"},
    },
    "forest-ledger": {
        "name": "Forest Ledger",
        "description": "Grounded operational palette suited to inventory and accounting.",
        "mode": "light",
        "tokens": {**_BASE, "--candy": "#2F6B4F", "--candy-dark": "#24523D", "--sky": "#386A73", "--sky-dark": "#2B5158", "--sunshine": "#B37A20", "--mint": "#26734D", "--mint-dark": "#1B5A3A", "--bg": "#F4F7F3", "--surface-soft": "#F8FBF7", "--rule": "#D8E2D8", "--sidebar-bg": "#193A31", "--sidebar-active": "#2D5A4A", "--topbar-start": "#193A31", "--topbar-end": "#2E5B50", "--field-bg": "#FAFCF9", "--table-header": "#EDF5EF", "--card-accent": "linear-gradient(90deg, #2F6B4F, #B37A20)"},
    },
    "midnight-operations": {
        "name": "Midnight Operations",
        "description": "Full dark workspace for low-light retail operations.",
        "mode": "dark",
        "tokens": {**_BASE, "--candy": "#F07A91", "--candy-dark": "#D95B75", "--sky": "#56C2D9", "--sky-dark": "#36A8C0", "--sunshine": "#E8B94C", "--persimmon": "#FF9770", "--mint": "#55C878", "--mint-dark": "#39A85B", "--bg": "#111820", "--card": "#1C2731", "--ink": "#F4F7F8", "--ink-soft": "#B5C0C6", "--rule": "#34434D", "--surface-soft": "#17232B", "--sidebar-bg": "#0B1116", "--sidebar-text": "#EAF0F2", "--sidebar-active": "#263C48", "--topbar-start": "#0B1116", "--topbar-end": "#18323D", "--success-bg": "#173522", "--warning-bg": "#3B3018", "--danger-bg": "#3A2026", "--field-bg": "#1E2B35", "--table-header": "#22323D", "--card-accent": "linear-gradient(90deg, #F07A91, #56C2D9)"},
    },
    "high-contrast": {
        "name": "High Contrast",
        "description": "Strong borders, explicit states, and maximum visual clarity.",
        "mode": "high-contrast",
        "tokens": {**_BASE, "--candy": "#8B0000", "--candy-dark": "#650000", "--sky": "#003D66", "--sky-dark": "#002B49", "--sunshine": "#7A4F00", "--persimmon": "#8F3A00", "--mint": "#005A2B", "--mint-dark": "#003D1D", "--bg": "#FFFFFF", "--card": "#FFFFFF", "--ink": "#000000", "--ink-soft": "#202020", "--rule": "#000000", "--surface-soft": "#F1F1F1", "--sidebar-bg": "#000000", "--sidebar-text": "#FFFFFF", "--sidebar-active": "#303030", "--topbar-start": "#000000", "--topbar-end": "#202020", "--success-bg": "#E6F4EA", "--warning-bg": "#FFF1CC", "--danger-bg": "#FFE6E6", "--field-bg": "#FFFFFF", "--table-header": "#F1F1F1", "--card-accent": "#000000", "--shadow": "0 0 0 1px #000000", "--shadow-hover": "0 0 0 2px #000000", "--shadow-overlay": "0 0 0 3px #000000", "--radius": "2px", "--radius-sm": "2px"},
    },
    "custom-brand": {
        "name": "Custom Brand",
        "description": "Your brand colors with accessible derived surfaces and text.",
        "mode": "light",
        "tokens": dict(_BASE),
    },
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _mix(first: str, second: str, amount: float) -> str:
    """`color-mix(in srgb, first (1-amount), second amount)` in sRGB — the same
    arithmetic the stylesheet asks the browser for, done here so a derived token
    can be measured rather than trusted."""
    channels = [round(x * (1 - amount) + y * amount) for x, y in zip(_hex(first), _hex(second))]
    return "#%02X%02X%02X" % tuple(channels)


def _luminance(value: str) -> float:
    channels = []
    for channel in _hex(value):
        scaled = channel / 255
        channels.append(scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    light = max(_luminance(first), _luminance(second))
    dark = min(_luminance(first), _luminance(second))
    return (light + 0.05) / (dark + 0.05)


def validate_hex(value: str) -> bool:
    return bool(_HEX.fullmatch((value or "").strip()))


def _legible(seed: str, surfaces: list[str], ratio: float) -> str:
    """The seed walked out of its own lightness until it reads on every surface."""
    for target in ("#000000", "#FFFFFF"):
        for step in range(1, 21):
            candidate = _mix(seed, target, step / 20)
            if all(contrast_ratio(candidate, surface) >= ratio for surface in surfaces):
                return candidate
    return seed


def _tinted(base: str, accent: str, ratio: float,
            others: tuple[str, ...] = (), start: float = 0.06) -> str:
    """`base` carrying a little of `accent`, mixed in until the tint is visible
    against the surface it sits on — and against the other surfaces that surface
    is drawn over, because a row's hover sits on a card inside a page."""
    for step in range(round(start * 100), 61):
        candidate = _mix(base, accent, step / 100)
        if all(contrast_ratio(candidate, surface) >= ratio for surface in (base, *others)):
            return candidate
    return _mix(base, accent, 0.6)


def tier_pair(name: str, light: str, dark: str) -> dict[str, str]:
    """The metals a customer's loyalty badge is painted in, same doctrine as the
    fills in `interaction_tokens`.

    These gradients were frozen in the stylesheet — a white label on the
    diamond's `#A66CFF` end read 3.3:1, the same latent bug the brand fill
    had on the phone. A metal is light by nature (silver is pale grey in
    every palette), so the label pole is fixed at black and the *fill* is
    the thing that moves: the hue a gradient end was frozen at is honoured
    where black already reads on it, and walked darker where it does not.
    """
    label = "#000000"

    def fill(seed: str) -> str:
        if contrast_ratio(label, seed) >= 4.5:
            return seed
        return _legible(seed, [label], 4.5)

    return {
        f"--tier-{name}-fill": fill(light),
        f"--tier-{name}-fill-dark": fill(dark),
        f"--tier-{name}-label": label,
    }


def interaction_tokens(tokens: dict[str, str]) -> dict[str, str]:
    """The colours whose only job is to be legible: focus, hover, disabled, label.

    Derived from the palette every time it is assembled rather than written into
    `_BASE` and inherited, because a theme that moves `--card`, `--sidebar-bg` or
    `--button-text` leaves a hand-written state colour behind — and a focus ring
    that is inherited still paints, which is the same failure `--persimmon` had
    for eleven declarations. `tests/test_keyboard_shell.py` measures every pair
    below in all ten palettes, so the floors stated here are the guard's floors.
    """
    card = tokens["--card"]
    bg = tokens["--bg"]
    field = tokens["--field-bg"]
    soft = tokens["--surface-soft"]
    label = tokens["--button-text"]
    # Where brand-coloured *text* is read: a card, a page, a field, the soft
    # surface, and the strongest tint of the brand that any rule paints under it
    # (the state badges mix 18% of it into the card).
    ring = _legible(tokens["--sky"], [card, field, bg], 3)
    hover = _tinted(card, tokens["--candy"], 1.25, others=(tokens["--bg"],))

    # The shell's top bar is a gradient of the palette's own two hues, and the
    # brand, the two topbar buttons and the mobile capture page's header all
    # sit on it. White was assumed for every palette and Kids Boutique's bar
    # proved the assumption wrong: its candy end read 3.36:1 and its sky end
    # 3.19:1. The label is the pole that reads on the *worse* end, walked if
    # neither pole manages the floor as it came — the same decision
    # `filled_pair` makes for a button.
    def bar_label(dark_end: str, light_end: str) -> str:
        white = min(contrast_ratio("#FFFFFF", dark_end), contrast_ratio("#FFFFFF", light_end))
        black = min(contrast_ratio("#000000", dark_end), contrast_ratio("#000000", light_end))
        pole = "#FFFFFF" if white >= black else "#000000"
        if min(contrast_ratio(pole, dark_end), contrast_ratio(pole, light_end)) >= 4.5:
            return pole
        return _legible(pole, [dark_end, light_end], 4.5)

    topbar_text = bar_label(tokens["--topbar-start"], tokens["--topbar-end"])
    # The sidebar's label is derived against the surfaces the sidebar itself
    # paints — its own background, its hover tint, its active item — the way
    # every other label is derived against the surface it is read on. A palette
    # that declares its own `--sidebar-text` (Kids Boutique's warm charcoal,
    # the high-contrast palette's white-on-black) is honoured, not overwritten:
    # a declared value is a designed value, and this only fills what a palette
    # left to inherit.
    sidebar_text = tokens.get("--sidebar-text")
    if not sidebar_text:
        sidebar_surfaces = [tokens["--sidebar-bg"],
                            _tinted(tokens["--sidebar-bg"], "#EAF0F2", 1.3),
                            tokens["--sidebar-active"]]
        seed = "#EAF0F2"
        sidebar_text = (seed if all(contrast_ratio(seed, s) >= 4.5 for s in sidebar_surfaces)
                        else _legible(seed, sidebar_surfaces, 4.5))

    def ink(seed: str, tint: str, *alerts: str) -> str:
        """A palette's hue, walked until it reads as text everywhere text is read:
        on the four surfaces a page is made of, on the strongest tint of the hue
        that any badge paints under it, and on the alert background that belongs to
        it (the warning banner is pale gold, and gold text on it read 1.84:1)."""
        surfaces = [card, bg, field, soft, _mix(card, tokens[tint], 0.18), hover, *alerts]
        return _legible(seed, surfaces, 4.5)

    def filled_pair(light_key: str, dark_key: str, prefix: str) -> dict[str, str]:
        """A filled surface and the label that sits on it, derived as a pair.

        One shared label colour for every filled surface cannot work: a white
        label read 2.66:1 on Midnight's candy and 2.12:1 on its mint, while the
        dark palettes wanted white on mint and black nowhere — the phone capture
        page was where white-on-candy at 2.7:1 was finally seen. So each family
        — the brand pair and the success pair — chooses its own label, the pole
        (white or black) that reads better on the *worse* end of its gradient,
        and the fill keeps the palette's hue unless no pole reads on it as it
        came, in which case it moves just enough for its label: Operations
        Light's candy was 0.02 below white's floor and moved a single step, and
        pos-focus's dark mint the same for black.
        """
        light, dark = tokens[light_key], tokens[dark_key]

        def worst(pole: str) -> float:
            return min(contrast_ratio(pole, light), contrast_ratio(pole, dark))

        white, black = worst("#FFFFFF"), worst("#000000")
        label = "#FFFFFF" if white >= black else "#000000"

        def fill(seed: str) -> str:
            if contrast_ratio(label, seed) >= 4.5:
                return seed
            return _legible(seed, [label], 4.5)

        return {
            f"{prefix}-fill": fill(light),
            f"{prefix}-fill-dark": fill(dark),
            f"{prefix}-label": label,
        }

    return {
        # The ring a keyboard reader follows. One for the page's own surfaces and
        # one for the shell's bars, which are dark in the light palettes and light
        # in the dark ones: no single colour reads on both a white card and a
        # near-black topbar, which is why the two exist.
        "--ring": ring,
        "--focus-ring": f"0 0 0 3px {ring}",
        "--ring-brand": _legible(tokens["--sky"],
                                 [tokens["--topbar-start"], tokens["--topbar-end"],
                                  tokens["--sidebar-bg"], tokens["--sidebar-active"]], 3),
        # A hovered row or choice, on a card; and a hovered item in the sidebar,
        # which is its own surface in every palette. The row tint is derived for
        # *visibility* (1.2:1); the text that may sit on a hovered row is derived
        # against it below — the tint cannot carry every ink at floor (no candy
        # wash does in the light palettes), so the inks walk, the same doctrine
        # as everywhere else: keep the hue, move the lightness.
        "--hover-surface": hover,
        "--sidebar-hover": _tinted(tokens["--sidebar-bg"], sidebar_text, 1.3),
        # What a button that cannot be pressed looks like: a quiet surface and a
        # label on it that still reads, rather than the whole control faded out.
        "--disabled-surface": tokens["--surface-soft"],
        # The two shell labels, derived above against the surfaces they are read
        # on. Declared values win; the derivation fills what a palette inherited.
        "--topbar-text": topbar_text,
        "--sidebar-text": sidebar_text,
        "--disabled-ink": _legible(tokens["--ink-soft"],
                                   [tokens["--surface-soft"], card], 4.5),
        # The quiet meta text — «۲ روز پیش», a share, a phone number — walked out
        # of its authored lightness until it reads on the hover surface too, the
        # way the sidebar's own label is already required to read on its hover.
        # The tint the row hover paints was derived for visibility (1.2:1) and
        # knew nothing of the text that sits on it; Kids Boutique's warm grey
        # measured 3.70:1 on its candy-washed hover, and four palettes' links
        # 4.19:1 — below the text floor, on a surface text is read on.
        "--ink-on-hover": _legible(tokens["--ink-soft"], [hover], 4.5),
        # The fill under a destructive button's label.
        "--danger": _legible("#FF4757", [label], 4.5),
        # The brand colour as text — a link, a KPI figure, a chip's label — and as
        # the fill a label sits on. `--candy` is a hue the palette picked for the
        # brand, not a colour that reads anywhere: as text it measured 3.36:1 on
        # the card in Kids Boutique and 4.12:1 as `--candy-dark` in Midnight, and a
        # white label on it measured 3.34:1 on the light palettes and 2.10:1 on the
        # dark one. The design keeps the hue and moves the lightness, which is the
        # only thing a contrast ratio can be bought with.
        "--link": ink(tokens["--candy"], "--candy", tokens["--danger-bg"]),
        "--link-hover": ink(tokens["--candy-dark"], "--candy", tokens["--danger-bg"]),
        # The same treatment for the other three voices the app speaks in: a
        # positive figure, a neutral note, a warning. Each was used as text as it
        # came — `--mint-dark` on a tint of itself read 3.64:1, `--sky-dark` 3.77:1,
        # and the warning alert printed `--sunshine` on the pale gold of its own
        # background at 1.84:1.
        "--success-ink": ink(tokens["--mint-dark"], "--mint", tokens["--success-bg"]),
        "--info-ink": ink(tokens["--sky-dark"], "--sky"),
        "--warning-ink": ink(tokens["--sunshine"], "--sunshine", tokens["--warning-bg"]),
        # The fourth voice: the special/special-offer colour the catalogue keeps
        # in `--lavender`, as text — it used to exist only as a fill.
        "--accent-ink": ink(tokens["--lavender"], "--lavender"),
        **filled_pair("--candy", "--candy-dark", "--brand"),
        **filled_pair("--mint", "--mint-dark", "--success"),
        # Paper: what a printed page is. The print rules used to hard-code a
        # white-and-black document; a token pair says it once, here, and lets a
        # palette own even its printer. The mid tone is what a rule or a chart
        # frame reads on paper: a quiet grey mixed from the pair, so a print
        # stylesheet speaks in tokens instead of another hard-coded grey.
        "--paper": "#FFFFFF",
        "--paper-ink": "#000000",
        "--paper-mid": _mix("#FFFFFF", "#000000", 0.18),
    }


def elevation_tokens(tokens: dict[str, str], mode: str) -> dict[str, str]:
    """The elevation ladder a palette lifts its surfaces with, from its own
    colours.

    The pair used to live in `_BASE` and be inherited: one light-tuned blur
    (`rgba(20, 35, 45, …)`) served every palette, and on Midnight it separated
    card from backdrop by 1.017:1 — a shadow the eye cannot find — while its
    hover state *lightened* the dark background instead of deepening it. A
    palette's elevation is part of its surfaces, so it is worked out here from
    the palette that owns them, the same doctrine as `interaction_tokens`:

    * a light palette tints its blur from its own `--ink`, so the shadow is
      that palette's darkness, not a neutral grey chosen for someone else;
    * a dark palette cannot darken by blur (its background is already the
      deepest colour it has), so it lifts with a 1px rim mixed from its card
      toward white — the edge is the elevation — plus a deepening blur drawn
      from its own `--bg`;
    * the high-contrast palette has designed its own idiom and keeps it: a
      hard ring instead of any blur. It is declared on the theme, and this
      derivation deliberately leaves declared pairs alone.

    The ladder has three rungs. `--shadow` is the rest state of a card,
    `--shadow-hover` the hover of a clickable one, and `--shadow-overlay` the
    depth of what floats above the page — modals, dropdown pickers, the
    lightbox. An overlay is a third, higher depth, not the hover value
    reused: it must clear whatever it floats over by more than hover does, in
    every palette, which is what the guard in tests/test_themes.py measures.
    """
    if mode == "high-contrast":
        return {}
    if mode == "dark":
        card = tokens["--card"]
        deep = _hex(tokens["--bg"])
        deep_rgba = f"rgba({deep[0]}, {deep[1]}, {deep[2]},"
        return {
            "--shadow": f"0 0 0 1px {_mix(card, '#FFFFFF', 0.06)}, "
                        f"0 6px 20px {deep_rgba} 0.55)",
            "--shadow-hover": f"0 0 0 1px {_mix(card, '#FFFFFF', 0.09)}, "
                              f"0 10px 28px {deep_rgba} 0.65)",
            "--shadow-overlay": f"0 0 0 1px {_mix(card, '#FFFFFF', 0.12)}, "
                                f"0 16px 44px {deep_rgba} 0.80)",
        }
    ink = _hex(tokens["--ink"])
    ink_rgba = f"rgba({ink[0]}, {ink[1]}, {ink[2]},"
    return {
        "--shadow": f"0 2px 8px {ink_rgba} 0.08)",
        "--shadow-hover": f"0 8px 20px {ink_rgba} 0.14)",
        "--shadow-overlay": f"0 14px 40px {ink_rgba} 0.22)",
    }


def custom_tokens(primary: str, secondary: str) -> dict[str, str]:
    primary = primary.strip().upper()
    secondary = secondary.strip().upper()
    if not validate_hex(primary) or not validate_hex(secondary):
        raise ValueError("Brand colors must be six-digit hexadecimal values")
    if contrast_ratio(primary, "#FFFFFF") < 4.5 and contrast_ratio(primary, "#000000") < 4.5:
        raise ValueError("Primary color does not provide readable button contrast")
    if contrast_ratio(secondary, "#FFFFFF") < 3 and contrast_ratio(secondary, "#000000") < 3:
        raise ValueError("Secondary color does not provide readable control contrast")
    primary_text = "#FFFFFF" if contrast_ratio(primary, "#FFFFFF") >= contrast_ratio(primary, "#000000") else "#000000"
    secondary_text = "#FFFFFF" if contrast_ratio(secondary, "#FFFFFF") >= contrast_ratio(secondary, "#000000") else "#000000"
    return {
        "--candy": primary,
        "--candy-dark": primary,
        "--sky": secondary,
        "--sky-dark": secondary,
        "--sidebar-bg": "#18232B",
        "--sidebar-active": secondary,
        "--topbar-start": "#18232B",
        "--topbar-end": secondary,
        "--primary-contrast": primary_text,
        "--secondary-contrast": secondary_text,
        "--button-text": primary_text,
    }


# Each palette is completed with the state colours it implies: a theme states the
# surfaces and its hovers, and the values that exist only to stay legible are
# worked out from them. One place, so a theme cannot keep a focus ring chosen for
# a background it no longer uses.
def _complete(theme: dict[str, Any]) -> None:
    """The state colours, then the tier metals that have no hue token to hang
    from: their gradients were frozen in the stylesheet, so the seeds are
    stated here, once, where the rest of the palette's choices live."""
    tokens = theme["tokens"]
    tokens.update(interaction_tokens(tokens))
    tokens.update(elevation_tokens(tokens, theme.get("mode", "light")))
    tokens.update(tier_pair("silver", "#E8E8E8", "#D0D0D0"))
    tokens.update(tier_pair("gold", "#FFE082", tokens["--sunshine"]))
    tokens.update(tier_pair("diamond", "#D4B0FF", tokens["--lavender"]))


for _theme in THEMES.values():
    _complete(_theme)


def theme_preview(theme_id: str, custom: dict[str, str] | None = None) -> dict[str, Any]:
    theme = THEMES.get(theme_id) or THEMES[DEFAULT_THEME_ID]
    tokens = dict(theme["tokens"])
    if theme_id == "custom-brand" and custom:
        tokens.update(custom_tokens(custom.get("primary", DEFAULT_CUSTOM_PRIMARY),
                                    custom.get("secondary", DEFAULT_CUSTOM_SECONDARY)))
        # A shop's own two colours move the brand surfaces, so the state colours
        # are worked out again from them: the ring follows the brand, not `_BASE`.
        # `_complete` re-derives everything the same way the catalogue did.
        _complete({"tokens": tokens})
    return {
        "id": theme_id if theme_id in THEMES else DEFAULT_THEME_ID,
        "name": theme["name"],
        "description": theme["description"],
        "mode": theme["mode"],
        "tokens": tokens,
    }


def theme_inline_style(theme: dict[str, Any]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in theme["tokens"].items())


# The hues the chart renderer leads with, in paint order — the appearance page
# prints them as the swatch strip under each theme's sample chart, so what the
# owner reads on the card is the order the charts actually use.
_CHART_ACCENT_TOKENS = ("--candy", "--sky", "--sunshine", "--mint")


def chart_accents(theme: dict[str, Any]) -> list[str]:
    tokens = theme["tokens"]
    return [tokens[name] for name in _CHART_ACCENT_TOKENS if tokens.get(name)]


def theme_json(theme: dict[str, Any]) -> str:
    return json.dumps(theme, ensure_ascii=False, separators=(",", ":"))


def get_theme(db=None) -> dict[str, Any]:
    # The shell asks for the theme on every page render, but it changes only
    # through the appearance form — the same shape the store profile keeps: a
    # short cache, invalidated at the writer. Only the shell path (no session
    # handed in) may read the cache; an explicit session gets a live read, so
    # a caller mid-transaction never sees a stale answer.
    global _SHELL_THEME_CACHE
    if db is None:
        now = time.time()
        if _SHELL_THEME_CACHE["data"] is not None and now - _SHELL_THEME_CACHE["at"] <= _SHELL_THEME_TTL:
            return _SHELL_THEME_CACHE["data"]
    close = db is None
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
    try:
        # One read of the settings table answers the theme, the custom primary
        # and the custom secondary — a page render asks once, not three times.
        chosen = {s.key: s.value for s in db.query(Settings).filter(
            Settings.key.in_([THEME_SETTING_KEY, CUSTOM_PRIMARY_KEY, CUSTOM_SECONDARY_KEY])).all()}
        theme_id = chosen.get(THEME_SETTING_KEY)
        if not theme_id or theme_id not in THEMES:
            theme_id = DEFAULT_THEME_ID
        custom = {
            "primary": chosen.get(CUSTOM_PRIMARY_KEY) or DEFAULT_CUSTOM_PRIMARY,
            "secondary": chosen.get(CUSTOM_SECONDARY_KEY) or DEFAULT_CUSTOM_SECONDARY,
        }
        try:
            theme = theme_preview(theme_id, custom)
        except ValueError:
            theme_id = DEFAULT_THEME_ID
            theme = theme_preview(theme_id)
        theme["inline_style"] = theme_inline_style(theme)
        if close:
            _SHELL_THEME_CACHE["data"] = dict(theme)
            _SHELL_THEME_CACHE["at"] = time.time()
        return theme
    finally:
        if close:
            db.close()


def invalidate_theme_cache() -> None:
    """Drop the shell's cached theme; called where the appearance form writes."""
    global _SHELL_THEME_CACHE
    _SHELL_THEME_CACHE = {"data": None, "at": 0.0}


def all_theme_previews(db=None) -> list[dict[str, Any]]:
    active = get_theme(db)
    custom = {
        "primary": active["tokens"].get("--candy", DEFAULT_CUSTOM_PRIMARY) if active["id"] == "custom-brand" else DEFAULT_CUSTOM_PRIMARY,
        "secondary": active["tokens"].get("--sky", DEFAULT_CUSTOM_SECONDARY) if active["id"] == "custom-brand" else DEFAULT_CUSTOM_SECONDARY,
    }
    previews = []
    for theme_id in THEMES:
        preview = theme_preview(theme_id, custom)
        preview["inline_style"] = theme_inline_style(preview)
        preview["chart_accent"] = chart_accents(preview)
        previews.append(preview)
    return previews
