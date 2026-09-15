from __future__ import annotations

import json
import re
from typing import Any

from models import Settings

DEFAULT_THEME_ID = "operations-light"
THEME_SETTING_KEY = "ui_theme"
CUSTOM_PRIMARY_KEY = "theme_custom_primary"
CUSTOM_SECONDARY_KEY = "theme_custom_secondary"
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
    "--focus-ring": "0 0 0 4px rgba(25, 122, 140, 0.24)",
    "--shadow": "0 2px 8px rgba(20, 35, 45, 0.08)",
    "--shadow-hover": "0 8px 20px rgba(20, 35, 45, 0.14)",
    "--radius": "8px",
    "--radius-sm": "6px",
    "--sidebar-bg": "#17232B",
    "--sidebar-text": "#EAF0F2",
    "--sidebar-active": "#2C414C",
    "--topbar-start": "#17232B",
    "--topbar-end": "#334C59",
    "--success-bg": "#E8F5ED",
    "--warning-bg": "#FFF5D9",
    "--danger-bg": "#FDEBEC",
    # The dim behind a photo lightbox or a blocking overlay, and what stays
    # legible on it. A picture is viewed against something, and the theme's card
    # is the wrong shape for that — a light surface in the light palettes has to
    # dim, not brighten, or the photo is washed out.
    "--scrim": "rgba(12, 10, 34, 0.92)",
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
        "tokens": {**_BASE, "--candy": "#E85D75", "--candy-dark": "#C84660", "--sky": "#2A9DB5", "--sky-dark": "#217D91", "--sunshine": "#E7A932", "--mint": "#35A853", "--mint-dark": "#278640", "--lavender": "#805AD5", "--bg": "#FFF7F2", "--card": "#FFFFFF", "--ink": "#2D2D2D", "--ink-soft": "#756F6A", "--rule": "#F0E4D8", "--surface-soft": "#FFFBF8", "--field-bg": "#FFFFFF", "--table-header": "#FFFAF5", "--card-accent": "linear-gradient(90deg, #E85D75, #E7A932, #35A853, #2A9DB5)", "--sidebar-bg": "#FFFFFF", "--sidebar-text": "#5B5551", "--sidebar-active": "#FFF0F4", "--topbar-start": "#E85D75", "--topbar-end": "#2A9DB5", "--success-bg": "#E8F8EC", "--warning-bg": "#FFF3D6", "--danger-bg": "#FFE8E8", "--shadow": "0 4px 16px rgba(0,0,0,0.06)", "--shadow-hover": "0 8px 24px rgba(0,0,0,0.10)", "--radius": "14px", "--radius-sm": "8px"},
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
        "tokens": {**_BASE, "--candy": "#F07A91", "--candy-dark": "#D95B75", "--sky": "#56C2D9", "--sky-dark": "#36A8C0", "--sunshine": "#E8B94C", "--persimmon": "#FF9770", "--mint": "#55C878", "--mint-dark": "#39A85B", "--bg": "#111820", "--card": "#1C2731", "--ink": "#F4F7F8", "--ink-soft": "#B5C0C6", "--rule": "#34434D", "--surface-soft": "#17232B", "--sidebar-bg": "#0B1116", "--sidebar-text": "#EAF0F2", "--sidebar-active": "#263C48", "--topbar-start": "#0B1116", "--topbar-end": "#18323D", "--success-bg": "#173522", "--warning-bg": "#3B3018", "--danger-bg": "#3A2026", "--field-bg": "#1E2B35", "--table-header": "#22323D", "--card-accent": "linear-gradient(90deg, #F07A91, #56C2D9)", "--focus-ring": "0 0 0 4px rgba(86, 194, 217, 0.38)"},
    },
    "high-contrast": {
        "name": "High Contrast",
        "description": "Strong borders, explicit states, and maximum visual clarity.",
        "mode": "high-contrast",
        "tokens": {**_BASE, "--candy": "#8B0000", "--candy-dark": "#650000", "--sky": "#003D66", "--sky-dark": "#002B49", "--sunshine": "#7A4F00", "--persimmon": "#8F3A00", "--mint": "#005A2B", "--mint-dark": "#003D1D", "--bg": "#FFFFFF", "--card": "#FFFFFF", "--ink": "#000000", "--ink-soft": "#202020", "--rule": "#000000", "--surface-soft": "#F1F1F1", "--sidebar-bg": "#000000", "--sidebar-text": "#FFFFFF", "--sidebar-active": "#303030", "--topbar-start": "#000000", "--topbar-end": "#202020", "--success-bg": "#E6F4EA", "--warning-bg": "#FFF1CC", "--danger-bg": "#FFE6E6", "--field-bg": "#FFFFFF", "--table-header": "#F1F1F1", "--card-accent": "#000000", "--shadow": "0 0 0 1px #000000", "--shadow-hover": "0 0 0 2px #000000", "--radius": "2px", "--radius-sm": "2px"},
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


def theme_preview(theme_id: str, custom: dict[str, str] | None = None) -> dict[str, Any]:
    theme = THEMES.get(theme_id) or THEMES[DEFAULT_THEME_ID]
    tokens = dict(theme["tokens"])
    if theme_id == "custom-brand" and custom:
        tokens.update(custom_tokens(custom.get("primary", DEFAULT_CUSTOM_PRIMARY),
                                    custom.get("secondary", DEFAULT_CUSTOM_SECONDARY)))
    return {
        "id": theme_id if theme_id in THEMES else DEFAULT_THEME_ID,
        "name": theme["name"],
        "description": theme["description"],
        "mode": theme["mode"],
        "tokens": tokens,
    }


def theme_inline_style(theme: dict[str, Any]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in theme["tokens"].items())


def theme_json(theme: dict[str, Any]) -> str:
    return json.dumps(theme, ensure_ascii=False, separators=(",", ":"))


def get_theme(db=None) -> dict[str, Any]:
    close = db is None
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
        theme_id = setting.value if setting and setting.value in THEMES else DEFAULT_THEME_ID
        custom = {
            "primary": (db.query(Settings).filter(Settings.key == CUSTOM_PRIMARY_KEY).first() or Settings(value=DEFAULT_CUSTOM_PRIMARY)).value or DEFAULT_CUSTOM_PRIMARY,
            "secondary": (db.query(Settings).filter(Settings.key == CUSTOM_SECONDARY_KEY).first() or Settings(value=DEFAULT_CUSTOM_SECONDARY)).value or DEFAULT_CUSTOM_SECONDARY,
        }
        try:
            theme = theme_preview(theme_id, custom)
        except ValueError:
            theme_id = DEFAULT_THEME_ID
            theme = theme_preview(theme_id)
        theme["inline_style"] = theme_inline_style(theme)
        return theme
    finally:
        if close:
            db.close()


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
        previews.append(preview)
    return previews
