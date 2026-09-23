"""Overlay elevation and the scrim, seen at the pixels.

`test_themes.py` asserts the tokens exist in every palette and that overlay ≥
hover (algebra over the token strings); `test_visual_hover.py` and
`test_visual_buttons.py` already see the hover row and the till's filled
buttons in pixels. What no guard has *seen* is what the browser paints when
the two floating surfaces actually open:

* the **lightbox** — the scrim (`#lightbox`, `background: var(--scrim)`) plus
  its elevated stage (`.lightbox-stage`, `--shadow-overlay`);
* the **date-picker popup** — `.pdp-popup`, the floating dropdown surface that
  carries `--shadow-overlay` and paints `--card` over whatever is behind it;
* the **mobile sidebar** — the app's third scrim surface, which exists only at
  a pocket width, so the walk switches itself to one: `#menu-toggle` clicked
  the way a thumb would, the drawer given its slide, and the scrim's
  `color-mix` coat (composited over flat windows of the before-shot, since a
  backdrop blur smears edges) and the drawer's elevation both measured.

So this module renders the real pages on the real server, opens each surface
with the same click a user gives it (`--overlay` driver mode), photographs the
full viewport — the scrim only exists at that scale — and measures what a user
would see:

* the **scrim dims the page**: each corner of the with-overlay capture equals
  the palette's declared scrim composited over the same corner of the
  before-shot (±8/channel) — the scrim paints the coat the palette declared.
  The composite rule is deliberately not a luminance floor: in a dark palette
  a 0.92 near-black scrim over a near-black page cannot separate by luminance
  (that is the design — the stage's rim carries it there), and in a light
  palette the same rule *proves* a visible dim;
* the **stage is a legible layer**: its modal paint separates from the dimmed
  surroundings by ≥ 1.2:1 — a stage that melts into the scrim is not a layer;
* the **popup paints a card, not the page**: the modal of its own padding edge
  equals the palette's resolved `--card` (±8/channel) — the original sin this
  family of guards exists for is a floating surface painting the page
  background, and in dark palettes the card-vs-page luminance gap is
  legitimately tiny, so identity is the check, not separation;
* the **popup carries the overlay rung**: its computed shadow, normalized into
  (offsets, blur, spread, colour) layers, equals the palette's own
  `--shadow-overlay` — the DOM normalizes strings, so both sides are parsed.

Evidence lands in `.snapshots/` (gitignored): `{theme}--lightbox.png` and
`{theme}--popup.png` per palette.
`RAYKIDS_VISUAL_THEMES="a,b"` trims the run; `RAYKIDS_SKIP_VISUAL=1` skips it.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Base, SessionLocal, engine  # noqa: E402
from models import GeneratedImage  # noqa: E402
from tests.test_visual_hover import (  # noqa: E402
    CHROME,
    SNAPSHOTS,
    THEME_IDS,
    _contrast,
    _decode_png,
    _owner_cookie,
    _switch_theme,
    probe_server,  # noqa: F401 — pytest re-exports the module fixture
)

PROBE = ROOT / "tools" / "visual_probe.mjs"

# A real generated photo, opaque and bright, so the lightbox stage carries the
# pixels a saved try-on carries — not a mostly-transparent logo over scrim.
SEED_IMAGE = "/static/uploads/generated/probe-seed.png"


def _seed_saved_tryon() -> None:
    """One saved try-on row pointing at a real static file, so the gallery has
    an opener to click and the stage holds actual content."""
    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        if not db.query(GeneratedImage).filter(GeneratedImage.image_path == SEED_IMAGE).first():
            db.add(GeneratedImage(image_path=SEED_IMAGE))
        db.commit()


def _overlay_walk(server, cookie: str, theme_id: str) -> dict:
    result = subprocess.run(
        [shutil.which("node"), str(PROBE), str(server.port), cookie, "/admin/checks",
         "body", str(SNAPSHOTS), theme_id, "--overlay"],
        capture_output=True, text=True, timeout=180, cwd=ROOT,
    )
    if result.returncode != 0:
        pytest.fail(f"overlay probe failed for {theme_id}:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _modal_in(pixels, y0: int, y1: int, x0: int, x1: int) -> tuple[int, int, int]:
    counted: dict[tuple[int, int, int], int] = {}
    for y in range(max(0, y0), min(len(pixels), y1)):
        for pixel in pixels[y][max(0, x0):max(0, x1)]:
            counted[pixel] = counted.get(pixel, 0) + 1
    assert counted, "the sampled region was empty — the surface did not render"
    return max(counted, key=counted.get)


def _parse_rgba(value: str) -> tuple[float, float, float, float]:
    """`rgba(r, g, b, a)`, `rgb(r, g, b)`, `#rrggbb`, or the resolved
    `color(srgb r g b / a)` form Chrome reports for a computed `color-mix` —
    the sidebar scrim's declared coat."""
    value = value.strip()
    if value.startswith("#"):
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0)
    inner = value[value.index("(") + 1:value.rindex(")")]
    if "/" in inner:
        # `color(srgb r g b / a)` — the keyword first, then the channels.
        channels, _, alpha = inner.partition("/")
        numbers = [p for p in channels.split()[1:]]
        chans = []
        for p in numbers:
            p = p.strip()
            if p.endswith("%"):
                chans.append(float(p[:-1]) * 2.55)
            else:
                chans.append(float(p) * 255)
        return (chans[0], chans[1], chans[2], float(alpha.strip()))
    parts = [p.strip() for p in inner.split(",")]
    r, g, b = (float(p) for p in parts[:3])
    return (r, g, b, float(parts[3]) if len(parts) > 3 else 1.0)


def _uniform(pixels, y0: int, y1: int, x0: int, x1: int) -> bool:
    """Whether a window is one flat colour — the only ground a scrim-with-blur
    can be composite-checked against strictly, since blur smears neighbouring
    pixels and an edge window's "before" is not one colour."""
    window = [p for row in pixels[y0:y1] for p in row[x0:x1]]
    for channel in range(3):
        values = [p[channel] for p in window]
        if max(values) - min(values) > 6:
            return False
    return True


def _composite(over: tuple, under: tuple) -> tuple[int, int, int]:
    """`over` (rgba) composited over `under` (rgb) — what the browser paints
    when the scrim covers the page."""
    r, g, b, a = over
    return (round(r * a + under[0] * (1 - a)),
            round(g * a + under[1] * (1 - a)),
            round(b * a + under[2] * (1 - a)))


def _close_enough(first: tuple, second: tuple, tolerance: int = 8) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first[:3], second[:3]))


def _parse_shadow(value: str) -> list[tuple]:
    """Normalize a box-shadow (declared token or computed style) into layers of
    (x, y, blur, spread, rgba) so the DOM's reformatting — colours first,
    `0px` for `0`, `0.8` for `0.80` — cannot fake a mismatch."""
    import re

    layers = []
    for part in re.split(r", (?=(?:[^()]*\([^()]*\))*[^()]*$)", value):
        colour = (0.0, 0.0, 0.0, 1.0)
        m = re.search(r"(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8})", part)
        if m:
            colour = _parse_rgba(m.group(1))
            part = part[:m.start()] + part[m.end():]
        # Zeros may be declared unitless (`0 0 0 1px`) — the DOM always writes
        # px. Numbers only appear in the lengths once the colour is stripped.
        lengths = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", part)]
        while len(lengths) < 4:
            lengths.append(0.0)
        layers.append(tuple(lengths[:4]) + (colour,))
    return layers


def _palette_overlay(theme_id: str) -> str:
    from services.themes import theme_preview

    return theme_preview(theme_id)["tokens"]["--shadow-overlay"]


# ── the probes ────────────────────────────────────────────────────────────────


@pytest.mark.skipif(CHROME is None, reason="no chromium-family browser on this machine")
@pytest.mark.skipif(shutil.which("node") is None, reason="no node to drive the browser")
@pytest.mark.skipif(os.environ.get("RAYKIDS_SKIP_VISUAL") == "1", reason="RAYKIDS_SKIP_VISUAL=1")
def test_overlay_surfaces_are_seen_in_every_palette(probe_server):
    _seed_saved_tryon()
    cookie = _owner_cookie(probe_server)

    wanted = [
        theme.strip() for theme in os.environ.get("RAYKIDS_VISUAL_THEMES", "").split(",")
        if theme.strip()
    ] or THEME_IDS

    offenders: list[str] = []
    evidence: list[str] = []
    for theme_id in wanted:
        _switch_theme(probe_server, cookie, theme_id)
        capture = _overlay_walk(probe_server, cookie, theme_id)
        overlay = capture["overlay"]

        # ── the lightbox: the scrim dims, the stage reads as a layer ──
        lb = overlay["lightbox"]
        assert lb["open"], f"{theme_id}: the lightbox never opened"
        lb_pixels = _decode_png(Path(lb["png"]).read_bytes())
        height, width = len(lb_pixels), len(lb_pixels[0])
        stage = lb["stage"]
        sx0, sy0 = int(stage["vx"]), int(stage["vy"])
        sx1, sy1 = sx0 + int(stage["width"]), sy0 + int(stage["height"])

        stage_paint = _modal_in(lb_pixels, sy0 + 8, sy1 - 8, sx0 + 8, sx1 - 8)
        # The four corners: whatever the page painted there before the overlay
        # (the before-shot, same viewport, same scroll — pixel-aligned), the
        # scrim must repaint as the declared coat over it. Not a luminance
        # floor: in a dark palette a 0.92 near-black scrim over a near-black
        # page cannot separate by luminance — that is the design, and the
        # stage's rim carries it there. The composite rule proves the coat is
        # the palette's own, and in light palettes it *is* a visible dim.
        before_pixels = _decode_png(Path(lb["pngBefore"]).read_bytes())
        scrim_rgba = _parse_rgba(lb["scrimPaint"])
        corners = [(4, 4), (4, width - 20), (height - 20, 4), (height - 20, width - 20)]
        for cy, cx in corners:
            before = _modal_in(before_pixels, cy, cy + 16, cx, cx + 16)
            after = _modal_in(lb_pixels, cy, cy + 16, cx, cx + 16)
            expected = _composite(scrim_rgba, before)
            if not _close_enough(after, expected):
                offenders.append(
                    f"{theme_id}: the lightbox scrim does not wear its declared coat — "
                    f"corner at ({cx},{cy}) paints {after}, the palette's scrim over the "
                    f"page is {expected} (page alone: {before})")

        separation = _contrast(stage_paint, _modal_in(lb_pixels, 4, 20, 4, 20))
        evidence.append(f"{theme_id} lightbox: stage {stage_paint} vs scrimmed page, {separation:.2f}:1")
        if separation < 1.2:
            offenders.append(
                f"{theme_id}: the lightbox stage's paint ({stage_paint}) does not separate "
                f"from the dimmed page ({_modal_in(lb_pixels, 4, 20, 4, 20)}) — "
                f"{separation:.2f}:1; the overlay does not read as a layer")

        # ── the popup: card over page, overlay rung in the DOM ──
        pop = overlay["popup"]
        assert pop["open"], f"{theme_id}: the date-picker popup never opened"
        pop_pixels = _decode_png(Path(pop["png"]).read_bytes())
        pheight, pwidth = len(pop_pixels), len(pop_pixels[0])
        pr = pop["popup"]
        px0, py0 = int(pr["vx"]), int(pr["vy"])
        px1, py1 = px0 + int(pr["width"]), py0 + int(pr["height"])

        # The popup's padding edge (not its header row, which selects paint
        # themselves) — what the card must paint is the palette's own card, not
        # the page background: in dark palettes the card-vs-page luminance gap
        # is legitimately tiny, so identity is the check, not separation.
        pop_paint = _modal_in(pop_pixels, py1 - 30, py1 - 8, px0 + 10, px1 - 10)
        from services.themes import theme_preview

        expected_card = _parse_rgba(theme_preview(theme_id)["tokens"]["--card"])
        if not _close_enough(pop_paint, expected_card):
            offenders.append(
                f"{theme_id}: the popup's paint ({pop_paint}) is not the palette's card "
                f"({expected_card}) — the floating surface does not paint its own surface")

        # The shadow: the DOM normalizes the declared token (colours first,
        # `0px` for `0`, `0.80` for `0.8`), so both sides are parsed into layers.
        declared = _parse_shadow(_palette_overlay(theme_id))
        computed = _parse_shadow(pop["shadow"])
        if declared != computed:
            offenders.append(
                f"{theme_id}: the popup's shadow {pop['shadow']!r} does not normalize to "
                f"the palette's --shadow-overlay {_palette_overlay(theme_id)!r}")
        evidence.append(
            f"{theme_id} popup: card {pop_paint} == palette card, "
            f"shadow {len(computed)} layer(s) match overlay rung")

        # ── the mobile sidebar: the third scrim, at a pocket width ──
        sb = overlay["sidebar"]
        assert sb["open"], f"{theme_id}: the mobile sidebar never opened"
        sb_pixels = _decode_png(Path(sb["png"]).read_bytes())
        sb_before = _decode_png(Path(sb["pngBefore"]).read_bytes())
        sh, sw = len(sb_pixels), len(sb_pixels[0])
        drawer = sb["drawer"]
        dx0, dy0 = int(drawer["vx"]), int(drawer["vy"])
        dx1, dy1 = dx0 + int(drawer["width"]), dy0 + int(drawer["height"])

        # The scrim's coat, composite-checked like the lightbox's — but this
        # one carries a backdrop blur, which smears any edge under it, so a
        # window qualifies only when the before-shot is locally flat there.
        # At least three of the four sample windows must be flat and matching;
        # fewer means the probe cannot witness this surface and says so.
        scrim_rgba = _parse_rgba(sb["scrimPaint"])
        # Candidate windows across the scrimmed area left of the drawer — a
        # grid, not four pins: which parts of a page are locally flat depends
        # on the page, and a window on an edge witnesses nothing.
        candidates = [(y, x) for y in range(76, sh - 36, 60)
                      for x in (8, 60, max(8, dx0 - 80))]
        flat = matched = 0
        for cy, cx in candidates:
            if not _uniform(sb_before, cy, cy + 16, cx, cx + 16):
                continue
            flat += 1
            before = _modal_in(sb_before, cy, cy + 16, cx, cx + 16)
            after = _modal_in(sb_pixels, cy, cy + 16, cx, cx + 16)
            expected = _composite(scrim_rgba, before)
            if _close_enough(after, expected, tolerance=12):
                matched += 1
            else:
                offenders.append(
                    f"{theme_id}: the sidebar scrim does not wear its declared coat — "
                    f"window at ({cx},{cy}) paints {after}, the palette's coat over the "
                    f"page is {expected} (page alone: {before})")
        if flat < 3 or matched < 3:
            offenders.append(
                f"{theme_id}: only {flat} flat windows witnessed the sidebar scrim and "
                f"{matched} matched — the probe cannot see this surface")

        # The drawer is an overlay surface (it covers the page above a scrim),
        # so its elevation must be the overlay rung — the same ladder the
        # lightbox and the popup climb — and its paint must separate from the
        # dimmed page next to it.
        declared_rung = _parse_shadow(_palette_overlay(theme_id))
        computed_rung = _parse_shadow(sb["drawerShadow"])
        if declared_rung != computed_rung:
            offenders.append(
                f"{theme_id}: the mobile drawer's shadow {sb['drawerShadow']!r} is not the "
                f"palette's --shadow-overlay {_palette_overlay(theme_id)!r} — an overlay "
                f"surface on the rest rung")
        drawer_paint = _modal_in(sb_pixels, dy0 + 20, dy1 - 20, dx0 + 10, dx1 - 10)
        beside = _modal_in(sb_pixels, sh - 60, sh - 20, max(4, dx0 - 80), dx0 - 8)
        lift = _contrast(drawer_paint, beside)
        evidence.append(
            f"{theme_id} sidebar: scrim {sb['scrimPaint']} blur {sb['scrimBlur']}, "
            f"drawer {drawer_paint} vs dimmed page {beside}, {lift:.2f}:1, "
            f"overlay rung in the DOM")
        if lift < 1.2:
            offenders.append(
                f"{theme_id}: the mobile drawer's paint ({drawer_paint}) does not separate "
                f"from the dimmed page ({beside}) — {lift:.2f}:1; it does not read as a layer")

    print("\noverlay evidence:")
    for line in evidence:
        print("  " + line)
    if offenders:  # pragma: no cover
        raise AssertionError("an overlay surface does not read as one:\n" + "\n".join(offenders))


def test_the_probe_reports_its_own_blindness():
    """The guard must not pass because nothing rendered: a sampled region that
    does not exist fails loudly here, and the driver the overlay probe depends
    on exists."""
    with pytest.raises(AssertionError):
        _modal_in([[(0, 0, 0)]], 5, 9, 0, 1)  # starts past the only row
    assert PROBE.exists(), "the CDP driver went missing"
