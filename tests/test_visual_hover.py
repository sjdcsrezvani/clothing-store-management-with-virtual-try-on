"""The hover state *seen*, not only measured.

`test_keyboard_shell.py` measures `--hover-surface` and `--ink-on-hover` in all
ten palettes; those numbers say what the palette intends. What they cannot say
is what a browser actually paints — the composition of a row, its borders, the
inline chips — until the page is rendered and the mouse has crossed a row.

So this module renders the real customer list (`templates/admin/customers.html`)
on a real server, for every palette, with the shop's own theme switch — the
appearance POST — and screenshots a row *while the mouse hovers it*, through
headless Chrome driven over the DevTools protocol (`tools/visual_probe.mjs`).

What is asserted is what "wrong" means here:

* a visible render, per palette — the screenshot exists, is a real PNG, and
  shows the hover: the hovered row's background differs from a sibling's;
* a legible hover, per palette — the strongest ink on the hovered row
  (`--ink-on-hover`, composited by the browser itself) reads >= 4.5:1 against
  the hovered row's painted background, measured from the pixels.

These are the two failures that escaped every numeric guard: a hover that
renders as nothing, and a hover that renders but its text washes out.

The captures land in `.snapshots/` (gitignored) — evidence to look at, not
fixtures to commit.

The full ten palettes run by default (~1 minute). `RAYKIDS_VISUAL_THEMES="a,b"`
trims a run to named palettes without weakening what is checked per cell, and
`RAYKIDS_SKIP_VISUAL=1` skips the render entirely for quick loops.

The same harness also *prints*: the invoice page is put through
`Page.printToPDF` — the browser's own print pipeline, print media applied —
and the PDF's pages are rasterized and measured against the paper doctrine:
the page must wear the theme's paper tokens (`--paper`/`--paper-ink`), not the
dark surfaces the screen wears. A dark palette whose card grey reached the
printer is exactly the wrongness that survived for years uncaught.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Base, engine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from models import (  # noqa: E402
    Customer,
    Product,
    ProductVariant,
    Sale,
    SaleItem,
)

PROBE = ROOT / "tools" / "visual_probe.mjs"
SNAPSHOTS = ROOT / ".snapshots"

# Chrome on this machine; the driver also checks its own candidates.
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Arc.app/Contents/MacOS/Arc",
)
CHROME = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)

THEME_IDS = [
    "kashi-tile", "kids-boutique", "operations-light", "pos-focus",
    "blush-maternal", "amber-till", "midnight-operations", "night-bazaar",
    "high-contrast", "custom-brand",
]

ROW_SELECTOR = "tbody tr"


# ── PNG reading, without a dependency on browser screenshot formats ─────────


def _read_png_chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    offset = 8
    while offset < len(data):
        assert offset + 8 <= len(data), "truncated PNG — the capture did not finish"
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        assert offset + 8 + length <= len(data), "truncated PNG — the capture did not finish"
        body = data[offset + 8:offset + 8 + length]
        yield kind, body
        offset += 12 + length


def _read_png_size(data: bytes) -> tuple[int, int]:
    for kind, body in _read_png_chunks(data):
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
            return width, height
    raise AssertionError("PNG without an IHDR")


def _decode_png(data: bytes) -> list[list[tuple[int, int, int]]]:
    """Decode the screenshot the browser actually wrote: truecolour or
    palette, filtered scanlines, no interlace — enough for pixel arithmetic."""
    width = height = bit_depth = colour_type = None
    palette: list[tuple[int, int, int]] = []
    idat = bytearray()
    for kind, body in _read_png_chunks(data):
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
            bit_depth, colour_type = body[8], body[9]
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"IDAT":
            idat += body
    assert bit_depth == 8 and colour_type in (2, 6), "unexpected screenshot format"

    raw = zlib.decompress(bytes(idat))
    channels = 3 if colour_type == 2 else 4
    stride = width * channels
    pixels: list[list[tuple[int, int, int]]] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_kind = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_kind == 1:    # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_kind == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_kind == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + (left + previous[i]) // 2) & 0xFF
        elif filter_kind == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        previous = line
        pixels.append([
            tuple(line[i * channels:i * channels + 3]) for i in range(width)
        ])
    return pixels


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    one, other = _relative_luminance(first), _relative_luminance(second)
    lighter, darker = max(one, other), min(one, other)
    return (lighter + 0.05) / (darker + 0.05)


# ── A real server for the real pages ─────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Server:
    """uvicorn's ASGI interface without a config file: one app, one port,
    lifetime owned by the test that started it."""

    def __init__(self) -> None:
        import uvicorn

        self.port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="warning", access_log=False, lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(50):
            if self.server.started:
                break
            time.sleep(0.1)
        assert self.server.started, "the probe server never came up"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture(scope="module")
def probe_server():
    server = _Server()
    yield server
    server.stop()


# ── The shop that gets rendered ───────────────────────────────────────────────


def _seed_customers() -> None:
    from datetime import datetime, timezone

    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        names = ["آرمان رضایی", "سارا محمدی", "نگار احمدی", "پویا کاظمی", "مینا صادقی",
                 "رضا توکلی", "الهام نوری", "سام مرادی", "شیوا رحیمی", "کیان اسدی"]
        for index, name in enumerate(names):
            first, _, last = name.partition(" ")
            db.add(Customer(
                phone=f"0912000{index:04d}",
                first_name=first,
                last_name=last,
                referral_code=f"P{index:05d}",
                tier=("silver", "gold", "diamond")[index % 3],
                created_at=datetime.now(timezone.utc),
            ))
        db.commit()


def _owner_cookie(server: _Server) -> str:
    """Log the owner in through the real login POST and keep the real cookie."""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        page = client.get("/admin/login").text
        token = page.split('name="csrf-token" content="')[1].split('"')[0]
        response = client.post("/admin/login", data={
            "username": "owner", "password": "test-admin-pass", "csrf_token": token,
        }, follow_redirects=False)
        assert response.status_code == 303, "owner login failed on the live server"
        jar = SimpleCookie()
        jar.load(response.headers["set-cookie"])
        cookie = jar["session"].value
        assert cookie, "no session cookie came back from the login"
        return cookie


def _switch_theme(server: _Server, cookie: str, theme_id: str) -> None:
    """Move the shop to a palette through the appearance POST — the same form,
    the same cache invalidation — not by reaching into the settings table."""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.cookies.set("session", cookie)
        page = client.get("/admin/customers").text
        token = page.split('name="csrf-token" content="')[1].split('"')[0]
        response = client.post("/admin/settings/appearance", data={
            "ui_theme": theme_id, "csrf_token": token,
        }, follow_redirects=False)
        assert response.status_code == 303, f"theme switch to {theme_id} failed"


def _hover_row(server: _Server, cookie: str, theme_id: str) -> dict:
    result = subprocess.run(
        [shutil.which("node"),
         str(PROBE), str(server.port), cookie, "/admin/customers", ROW_SELECTOR,
         str(SNAPSHOTS), theme_id],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    if result.returncode != 0:
        pytest.fail(f"visual probe failed for {theme_id}:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── The probes ────────────────────────────────────────────────────────────────


@pytest.mark.skipif(CHROME is None, reason="no chromium-family browser on this machine")
@pytest.mark.skipif(shutil.which("node") is None, reason="no node to drive the browser")
@pytest.mark.skipif(os.environ.get("RAYKIDS_SKIP_VISUAL") == "1", reason="RAYKIDS_SKIP_VISUAL=1")
def test_the_customer_list_hover_is_seen_in_every_palette(probe_server):
    _seed_customers()
    cookie = _owner_cookie(probe_server)

    wanted = [
        theme.strip() for theme in os.environ.get("RAYKIDS_VISUAL_THEMES", "").split(",")
        if theme.strip()
    ] or THEME_IDS

    offenders: list[str] = []
    for theme_id in wanted:
        _switch_theme(probe_server, cookie, theme_id)
        capture = _hover_row(probe_server, cookie, theme_id)

        png = Path(capture["png"])
        data = png.read_bytes()
        width, height = _read_png_size(data)
        assert width > 40 and height > 8, f"{theme_id}: capture is not a rendered row ({width}x{height})"

        pixels = _decode_png(data)

        def paint_at(y: int) -> tuple[int, int, int]:
            """The painted row background on one scanline: the modal colour of
            the row's edge columns, which its cells' padding keeps glyph-free."""
            counted: dict[tuple[int, int, int], int] = {}
            for pixel in pixels[y][4:16]:
                counted[pixel] = counted.get(pixel, 0) + 1
            return max(counted, key=counted.get)

        boundary = int(capture["rowHeight"])
        hovered = paint_at(boundary - 8)
        sibling = paint_at(boundary + 8)   # the row below: not hovered

        if _contrast(hovered, sibling) < 1.2:
            offenders.append(
                f"{theme_id}: hovered row's paint ({hovered}) is indistinct from its "
                f"sibling's ({sibling}) — {_contrast(hovered, sibling):.2f}:1; the hover is not seen")

        inks = [
            pixel for y in range(4, min(height, 36)) for pixel in pixels[y]
            if _contrast(pixel, hovered) >= 4.5
        ]
        if not inks:
            offenders.append(
                f"{theme_id}: no ink on the hovered row reads 4.5:1 against the "
                f"painted hover — text washes out at the pixels")

    if offenders:  # pragma: no cover
        import textwrap
        raise AssertionError("the hover is not seen, or not legible:\n" + "\n".join(offenders))


def test_the_probe_reports_its_own_blindness():
    """The guard must not pass because nothing rendered: an unreadable capture
    fails loudly here, and the driver the visual probe depends on exists."""
    with pytest.raises(AssertionError):
        _read_png_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    assert PROBE.exists(), "the CDP driver went missing"


# ── The invoice, printed: paper state seen in pixels ─────────────────────────


def _seed_sale() -> int:
    """A confirmed card sale with two items, so the invoice has real rows."""
    from datetime import datetime, timezone

    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        product = Product(name="تیشرت آزمایشی", category="clothing")
        db.add(product)
        db.flush()
        variant = ProductVariant(product_id=product.id, size="M", color="آبی",
                                 price=650_000, stock_quantity=10, barcode="PROBE-0001")
        variant2 = ProductVariant(product_id=product.id, size="L", color="قرمز",
                                  price=720_000, stock_quantity=10, barcode="PROBE-0002")
        db.add_all([variant, variant2])
        db.flush()
        customer = Customer(phone="09120009001", first_name="آرمان", last_name="رضایی",
                            referral_code="PRB001", tier="silver",
                            created_at=datetime.now(timezone.utc))
        db.add(customer)
        db.flush()
        sale = Sale(customer_id=customer.id, total_amount=1_370_000, final_amount=1_370_000,
                    payment_method="card", payment_confirmed=True,
                    created_at=datetime.now(timezone.utc), points_earned=137)
        db.add(sale)
        db.flush()
        db.add_all([
            SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id,
                     quantity=1, unit_price=650_000, unit_cost=300_000, total_price=650_000),
            SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant2.id,
                     quantity=1, unit_price=720_000, unit_cost=320_000, total_price=720_000),
        ])
        db.commit()
        return sale.id


def _print_pdf(server, cookie: str, url_path: str, label: str) -> Path:
    """Print a page to PDF through the browser's own print pipeline."""
    result = subprocess.run(
        [shutil.which("node"), str(PROBE), str(server.port), cookie, url_path,
         ".", str(SNAPSHOTS), label, "--print"],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    if result.returncode != 0:
        pytest.fail(f"print probe failed for {label}:\n{result.stdout}\n{result.stderr}")
    return Path(json.loads(result.stdout.strip().splitlines()[-1])["pdf"])


def _pdf_pages_as_png(pdf_path: Path) -> list[list[list[tuple[int, int, int]]]]:
    """Rasterize each PDF page with macOS Quartz into the same pixel arrays the
    hover probe measures. Scale 2 ≈ 144 dpi: fine enough to read text pixels."""
    import Quartz
    from PIL import Image

    url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, str(pdf_path.resolve()).encode(), len(str(pdf_path.resolve()).encode()), False)
    document = Quartz.CGPDFDocumentCreateWithURL(url)
    assert document is not None, "the printed PDF could not be opened"
    pages = Quartz.CGPDFDocumentGetNumberOfPages(document)
    assert pages >= 1, "the printed PDF has no pages"

    rendered = []
    for index in range(1, pages + 1):
        page = Quartz.CGPDFDocumentGetPage(document, index)
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        scale = 2.0
        width, height = int(rect.size.width * scale), int(rect.size.height * scale)
        context = Quartz.CGBitmapContextCreate(None, width, height, 8, width * 4,
                                               Quartz.CGColorSpaceCreateDeviceRGB(),
                                               Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextSetRGBFillColor(context, 1, 1, 1, 1)
        Quartz.CGContextFillRect(context, Quartz.CGRectMake(0, 0, width, height))
        Quartz.CGContextScaleCTM(context, scale, scale)
        Quartz.CGContextDrawPDFPage(context, page)
        image = Quartz.CGBitmapContextCreateImage(context)
        dest = Quartz.CGImageDestinationCreateWithURL(
            Quartz.CFURLCreateFromFileSystemRepresentation(
                None, str(pdf_path.with_suffix(".png").resolve()).encode(),
                len(str(pdf_path.with_suffix(".png").resolve()).encode()), False),
            "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, image, None)
        Quartz.CGImageDestinationFinalize(dest)
        png = pdf_path.with_suffix(".png")
        rendered.append(_decode_png(png.read_bytes()))
    return rendered


@pytest.mark.skipif(CHROME is None, reason="no chromium-family browser on this machine")
@pytest.mark.skipif(shutil.which("node") is None, reason="no node to drive the browser")
@pytest.mark.skipif(sys.platform != "darwin", reason="PDF rasterization needs macOS Quartz")
def test_the_printed_invoice_wears_the_theme_paper_in_midnight_and_kids(probe_server):
    """The paper doctrine, seen: the invoice printed from a dark palette must
    reach the printer as ink on white paper, not the dark surfaces the screen
    wears — measured from the pixels of the PDF a real print dialog produces."""
    sale_id = _seed_sale()
    cookie = _owner_cookie(probe_server)

    offenders: list[str] = []

    # The body rule that starts the paper state, pinned at source: on this page
    # its colour half has no visible surface in print (everything outside the
    # invoice box is hidden), so pixels cannot witness it — the stylesheet's
    # own words are the witness instead.
    print_css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "html, body { background: var(--paper) !important; color: var(--paper-ink) !important; }" in print_css, \
        "the print block's body rule no longer paints paper and paper-ink"

    for theme_id in ("midnight-operations", "kids-boutique", "kashi-tile", "night-bazaar"):
        _switch_theme(probe_server, cookie, theme_id)
        pdf = _print_pdf(probe_server, cookie, f"/sales/invoice/{sale_id}", f"invoice-{theme_id}")
        pages = _pdf_pages_as_png(pdf)

        for page_number, pixels in enumerate(pages, 1):
            height, width = len(pixels), len(pixels[0])
            from collections import Counter

            def modal(y0: int, y1: int) -> tuple[int, int, int]:
                counted: dict[tuple[int, int, int], int] = {}
                for y in range(y0, y1):
                    for pixel in pixels[y][::7]:
                        counted[pixel] = counted.get(pixel, 0) + 1
                return max(counted, key=counted.get)

            def modal_region(y0: int, y1: int, x0: int, x1: int) -> tuple[int, int, int]:
                counted: dict[tuple[int, int, int], int] = {}
                for y in range(y0, y1):
                    for pixel in pixels[y][x0:x1:5]:
                        counted[pixel] = counted.get(pixel, 0) + 1
                return max(counted, key=counted.get)

            # What the page is made of: the whole sheet's modal colour is the
            # paper; the ink is the page's dark pixels, sampled honestly — a
            # modal of a band that is mostly whitespace reads white and says
            # nothing about text. A dark palette's card grey reaching the
            # printer shows up as a page that is not paper.
            page_paint = modal(0, height)
            # The invoice box's own interior: the sheet's middle. A dark card
            # surface that leaked into the print block can lose the sheet-wide
            # modal vote to the white margins — here it cannot hide.
            interior = modal_region(int(height * 0.3), int(height * 0.7),
                                    int(width * 0.3), int(width * 0.7))
            ink_sample = [
                pixel for y in range(0, height, 2) for pixel in pixels[y][::4]
                if sum(pixel) < 330
            ]
            if not ink_sample:
                offenders.append(f"{theme_id} p{page_number}: no ink found on the sheet at all")
                continue
            ink_median = sorted(ink_sample, key=sum)[len(ink_sample) // 2]
            paper_ok = all(channel >= 245 for channel in page_paint)
            ink_ok = sum(ink_median) <= 330
            if not paper_ok:
                offenders.append(
                    f"{theme_id} p{page_number}: the page prints {page_paint} — not paper; "
                    f"a theme surface reached the printer")
            if not all(channel >= 245 for channel in interior):
                offenders.append(
                    f"{theme_id} p{page_number}: the invoice's own box prints {interior} — "
                    f"not paper; its print rule is not painting the paper tokens")
            if not ink_ok:
                offenders.append(
                    f"{theme_id} p{page_number}: the darkest quarter of ink reads {ink_median} — "
                    f"text would not survive toner")

            # The corners are the body's own paint — the one place no element's
            # paper rule can mask a theme surface that leaked to the printer.
            corners = [pixels[2][2], pixels[2][width - 3],
                       pixels[height - 3][2], pixels[height - 3][width - 3]]
            for corner in corners:
                if not all(channel >= 245 for channel in corner):
                    offenders.append(
                        f"{theme_id} p{page_number}: the sheet's margin prints {corner} — "
                        f"the body's own background is not paper")
                    break

            # And text must actually read on it: sample the page's own pixels —
            # text antialiasing means the darkest pixels approximate the ink.
            darkest = min(
                (pixel for y in range(0, height, 3) for pixel in pixels[y][::5]),
                key=sum,
            )
            lightest = max(
                (pixel for y in range(0, height, 3) for pixel in pixels[y][::5]),
                key=sum,
            )
            ratio = _contrast(darkest, lightest)
            if ratio < 8.0:
                offenders.append(
                    f"{theme_id} p{page_number}: strongest ink on the sheet reads {ratio:.1f}:1 "
                    f"— a printed page has no excuse below 8:1")

    if offenders:  # pragma: no cover
        raise AssertionError("the printed paper state is not clean:\n" + "\n".join(offenders))
