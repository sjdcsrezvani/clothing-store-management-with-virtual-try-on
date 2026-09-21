"""The checkout till's filled buttons, seen at the pixels.

`test_keyboard_shell.py` measures `--brand-label` against `--brand-fill` and
`--success-label` against `--success-fill` in all ten palettes; those numbers
say what the palette intends for the *pair*. What they cannot say is what a
browser actually paints on the checkout page — a gradient is a ramp of
composited colours, the page may composite the button over the card, and the
label is antialiased over all of it. This module renders the real checkout
(`templates/sales/checkout.html`) on the real server, photographs its filled
controls through the same headless-Chrome driver the hover evidence uses, and
measures what is painted:

* the **confirm button** — `.btn-success`, the money button — photographed on
  the till's money screen, reached through the real flow driven *inside the
  browser* (`--till` mode: customer step → skip-customer → add-to-basket, the
  same forms the cashier's clicks submit), because the scan step exists only
  behind POSTs and the button only renders once the basket is not empty;
* the **customer-search button** — `.btn-primary` on the till's first screen;
* the **front-door ghost** — «فروش بدون ثبت مشتری», the anonymous-sale path
  beside the search button;
* the **payment-area ghost** — «ارسال مبلغ به کارت‌خوان», the payment options'
  own control on the money screen (the options themselves are radio labels,
  so this button is what the payment step fills);
* the **نسیه walks** — a second and third till walk through real customers
  (`TILL_PHONE` drives the lookup, the credit radio is clicked in the
  browser): one with room on their account — the selected option's candy wash
  and the confirm button with the terminal ghost gone — and one already over
  their سقف اعتبار, whose screen must show the refusal warning. The credit
  confirmation states, seen rather than assumed;
* the **invoice page's controls** — the print button (`.btn-primary`, the one
  this whole paper doctrine hangs off), the refund danger (`.btn-danger`),
  the PDF ghost and the new-sale success — every filled control the sale path
  ends with, photographed on a real confirmed sale's invoice.

For each control, per palette, the pixels must show a label that reads: the
modal fill of the button's own paint against the darkest quarter of its
antialiased label glyphs, floored at 4.5:1 — the same floor the numeric pair
guard states. A gradient's worst end is what a label sits on, and pixels know
that in a way tokens do not. A ghost button's fill is the surface it sits on,
which is exactly the pairing its ink must read against — the same measurement
says so honestly.

Evidence lands in `.snapshots/` (gitignored) as `till-{theme}--*.png`.
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
from main import app  # noqa: E402
from models import Product, ProductVariant  # noqa: E402
from tests.test_visual_hover import (  # noqa: E402
    CHROME,
    SNAPSHOTS,
    THEME_IDS,
    _contrast,
    _decode_png,
    _owner_cookie,
    _read_png_size,
    _switch_theme,
    probe_server,  # noqa: F401 — pytest re-exports the module fixture
)

PROBE = ROOT / "tools" / "visual_probe.mjs"

# The two screens the till shows: step 1 is the phone search (a `.btn-primary`
# filled button), the scan step carries the `.btn-success` confirm. The scan
# step's page is reached by the driver itself walking the real flow (`--till`),
# so the photographs are of what a cashier actually sees.
SEARCH_SELECTOR = ".hero-card .btn-primary"
TILL_GHOST_SELECTOR = ".hero-card .btn-ghost"
CONFIRM_SELECTOR = "#confirm-submit"
TERMINAL_SELECTOR = "#terminal-submit"
INVOICE_PRINT_SELECTOR = ".invoice-actions .btn-primary"
INVOICE_DANGER_SELECTOR = ".invoice-actions .btn-danger"
INVOICE_GHOST_SELECTOR = ".invoice-actions .btn-ghost"
INVOICE_SUCCESS_SELECTOR = ".invoice-actions .btn-success"
# The نسیه option as a control: the label paints the selected state (:has),
# and the *text* is what AA governs — the native radio widget is theme-owned
# chrome (accent-color), not the pairing a label reads by.
CREDIT_RADIO_SELECTOR = '.payment-option:has(input[value="credit"]) > span'
CARD_OPTION_SELECTOR = '.payment-option:has(input[value="card"]) > span'
CASH_OPTION_SELECTOR = '.payment-option:has(input[value="cash"]) > span'


def _seed_shop() -> None:
    """One product to scan — the walk is an anonymous sale, so no customer row
    is needed; the till's front door and its money screen are both reachable
    without one, exactly as a walk-in cashier sees them."""
    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        if not db.query(Product).filter(Product.name == "کفش صندوق").first():
            product = Product(name="کفش صندوق", category="کفش")
            db.add(product)
            db.flush()
            db.add(ProductVariant(product_id=product.id, size="M", color="مشکی",
                                  price=850_000, stock_quantity=5, barcode="TILL-0001"))
        db.commit()


def _seed_invoice_sale() -> int:
    """A confirmed card sale with an item, so the invoice page exists to
    photograph — the sale path's last screen, where the print button lives."""
    from datetime import datetime, timezone

    from models import Customer, Sale, SaleItem

    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        product = db.query(Product).filter(Product.name == "کفش صندوق").first()
        if product is None:
            product = Product(name="کفش صندوق", category="کفش")
            db.add(product)
            db.flush()
        variant = db.query(ProductVariant).filter(ProductVariant.product_id == product.id).first()
        if variant is None:
            variant = ProductVariant(product_id=product.id, size="M", color="مشکی",
                                     price=850_000, stock_quantity=5, barcode="TILL-0001")
            db.add(variant)
            db.flush()
        customer = Customer(phone="09120009601", first_name="آرمان", last_name="رضایی",
                            referral_code="BTB-1",
                            created_at=datetime.now(timezone.utc))
        db.add(customer)
        db.flush()
        sale = Sale(customer_id=customer.id, total_amount=850_000, final_amount=850_000,
                    payment_method="card", payment_confirmed=True,
                    created_at=datetime.now(timezone.utc), points_earned=85)
        db.add(sale)
        db.flush()
        db.add(SaleItem(sale_id=sale.id, product_id=product.id, variant_id=variant.id,
                        quantity=1, unit_price=850_000, unit_cost=300_000,
                        total_price=850_000))
        db.commit()
        return sale.id


def _seed_credit_customers() -> None:
    """The two نسیه confirmation states, as real customers: one with room on
    their account (the calm panel) and one already over their سقف اعتبار (the
    warning) — the alert state a calm-only seed would never render."""
    from models import Customer

    with SessionLocal() as db:
        Base.metadata.create_all(bind=engine)
        if not db.query(Customer).filter(Customer.phone == "09120009701").first():
            db.add(Customer(phone="09120009701", first_name="سیمین", last_name="اعتباری",
                            referral_code="CRC-1", credit_limit=5_000_000, total_debt=200_000))
        if not db.query(Customer).filter(Customer.phone == "09120009702").first():
            db.add(Customer(phone="09120009702", first_name="بهرام", last_name="سرریز",
                            referral_code="CRC-2", credit_limit=100_000, total_debt=500_000))
        db.commit()


def _shoot_till_credit(server, cookie: str, label: str, phone: str,
                       selectors: str) -> dict:
    """The credit walk: the driver's till walk with TILL_PHONE set — lookup,
    scan, نسیه radio clicked — photographing the money screen's credit state."""
    env = {**os.environ, "TILL_PHONE": phone}
    args = [shutil.which("node"), str(PROBE), str(server.port), cookie, "/sales/new",
            "body", str(SNAPSHOTS), label, "--till", selectors]
    result = subprocess.run(args, capture_output=True, text=True, timeout=180,
                            cwd=ROOT, env=env)
    if result.returncode != 0:
        pytest.fail(f"credit till probe failed for {label}:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _shoot_till_scan(server, cookie: str, label: str,
                     selectors: str = CONFIRM_SELECTOR) -> dict:
    """Walk the till to its money screen *inside the browser* and photograph
    the money screen's controls there — the scan step exists only behind POSTs."""
    capture = _shoot_buttons(server, cookie, label, selectors, "--till")
    state = capture.get("till") or {}
    if not state.get("confirmPresent"):
        pytest.fail(
            f"{label}: the till walk never reached the scan step — the money "
            f"button did not render (basket: {state.get('basketItems')!r})")
    return capture


def _shoot_buttons(server, cookie: str, label: str, selectors: str,
                   mode: str | None = None, path: str = "/sales/new") -> dict:
    # The driver's argv has one optional slot between label and the selectors:
    # it holds --print, --till, or the "--buttons" placeholder that means
    # "plain screenshot mode, buttons follow".
    args = [shutil.which("node"), str(PROBE), str(server.port), cookie, path,
            "body", str(SNAPSHOTS), label, mode or "--buttons", selectors]
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, cwd=ROOT)
    if result.returncode != 0:
        pytest.fail(f"button probe failed for {label}:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _crop_viewport(pixels: list[list[tuple[int, int, int]]], rect: dict) -> list[list[tuple[int, int, int]]]:
    """Cut the element out of the full-viewport capture with its viewport rect.
    The driver pins deviceScaleFactor to 1, so capture pixels and CSS pixels
    are the same grid — no coordinate translation to get wrong."""
    height, width = len(pixels), len(pixels[0])
    x0, y0 = int(rect["vx"]), int(rect["vy"])
    x1, y1 = int(rect["vx"] + rect["width"]), int(rect["vy"] + rect["height"])
    assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, \
        f"button rect {rect} sits outside the {width}x{height} capture"
    return [row[x0:x1] for row in pixels[y0:y1]]


def _worst_label_on_fill(png: Path, rect: dict) -> tuple[float, str, str, int]:
    """The honest reading of one button's photograph.

    The fill is the modal colour of the band the text sits in — on a real
    button the fill always wins that vote, gradient included. The label is the
    modal colour of the pixels in that band that are clearly *not* the fill
    (contrast >= 1.6 against it): the glyph cores, whichever luminance pole
    the palette chose — dark ink on a light fill or light ink on a dark one.
    Antialiasing puts every in-between shade on the glyph edges, but at the
    driver's 2x capture the cores are the ink itself, and the modal colour of
    the distinct pixels lands there. A button whose label never painted (or
    painted in the fill's own colour) has no distinct pixels and fails here
    instead of silently measuring the gradient against itself."""
    from collections import Counter

    data = png.read_bytes()
    width, height = _read_png_size(data)
    pixels = _crop_viewport(_decode_png(data), rect)
    height, width = len(pixels), len(pixels[0])
    assert width > 40 and height > 16, f"{png.name}: not a rendered button ({width}x{height})"

    band = Counter()
    for y in range(height // 4, 3 * height // 4):
        for pixel in pixels[y][width // 10: 9 * width // 10]:
            band[pixel] += 1
    assert band, f"{png.name}: the button's central band is empty"
    fill = band.most_common(1)[0][0]

    glyphs = Counter({pixel: n for pixel, n in band.items() if _contrast(pixel, fill) >= 1.6})
    distinct = sum(glyphs.values())
    if distinct < 40:
        raise AssertionError(
            f"{png.name}: only {distinct} pixels differ from the fill — no label "
            f"was painted where a label should be")
    label = glyphs.most_common(1)[0][0]
    return _contrast(label, fill), "#%02X%02X%02X" % label, "#%02X%02X%02X" % fill, distinct


@pytest.mark.skipif(CHROME is None, reason="no chromium-family browser on this machine")
@pytest.mark.skipif(shutil.which("node") is None, reason="no node to drive the browser")
@pytest.mark.skipif(os.environ.get("RAYKIDS_SKIP_VISUAL") == "1", reason="RAYKIDS_SKIP_VISUAL=1")
def test_the_till_filled_buttons_read_at_the_pixels(probe_server):
    _seed_shop()
    _seed_credit_customers()
    invoice_sale_id = _seed_invoice_sale()
    cookie = _owner_cookie(probe_server)

    wanted = [
        theme.strip() for theme in os.environ.get("RAYKIDS_VISUAL_THEMES", "").split(",")
        if theme.strip()
    ] or THEME_IDS

    offenders: list[str] = []
    evidence: list[str] = []
    for theme_id in wanted:
        _switch_theme(probe_server, cookie, theme_id)

        # Screen 1: the till's front door — search (filled) and the
        # anonymous-sale ghost beside it.
        first = _shoot_buttons(probe_server, cookie, f"till-{theme_id}",
                               f"{SEARCH_SELECTOR},{TILL_GHOST_SELECTOR}")
        search_capture = first["buttons"][0]
        till_ghost_capture = first["buttons"][1]

        # Screen 2: the money screen — the driver walks the real flow
        # (skip-customer, scan one item) before photographing. The confirm
        # (success), the payment options' own control (ghost, terminal), and
        # the option spans in their default states: کارت selected, نقد
        # unselected — the payment radios, seen rather than assumed.
        second = _shoot_till_scan(probe_server, cookie, f"till-scan-{theme_id}",
                                  f"{CONFIRM_SELECTOR},{TERMINAL_SELECTOR},"
                                  f"{CARD_OPTION_SELECTOR},{CASH_OPTION_SELECTOR}")
        confirm_capture = second["buttons"][0]
        terminal_capture = second["buttons"][1]
        card_default_capture = second["buttons"][2]
        cash_default_capture = second["buttons"][3]
        if (second.get("till") or {}).get("confirmDisabled"):
            evidence.append(f"{theme_id} confirm payment: button rendered disabled (POS approval) — photographed as painted")

        # Screen 2b: the نسیه walks. The credit radio is clicked inside the
        # browser, so what is photographed is the credit confirmation state:
        # the selected option's candy wash, the confirm button with the
        # terminal ghost gone, and — for the customer over their سقف اعتبار —
        # the refusal warning on the same screen.
        credit_selectors = f"{CONFIRM_SELECTOR},{CREDIT_RADIO_SELECTOR},{CARD_OPTION_SELECTOR}"
        calm = _shoot_till_credit(probe_server, cookie, f"till-credit-{theme_id}",
                                  "09120009701", credit_selectors)
        calm_state = calm.get("till") or {}
        assert calm_state.get("creditRadio") and calm_state.get("creditPanel"), \
            f"{theme_id}: the credit walk never reached the نسیه state ({calm_state})"
        assert calm_state.get("terminalHidden"), \
            f"{theme_id}: the terminal ghost should hide on a نسیه sale"
        assert not calm_state.get("creditWarning"), \
            f"{theme_id}: a customer with room must not see the refusal warning"
        over = _shoot_till_credit(probe_server, cookie, f"till-credit-over-{theme_id}",
                                  "09120009702", credit_selectors)
        over_state = over.get("till") or {}
        assert over_state.get("creditWarning"), \
            f"{theme_id}: the over-limit customer's warning never rendered ({over_state})"

        # Screen 3: the sale path's last screen — the invoice. Print (the
        # button the paper doctrine hangs off), the refund danger, the PDF
        # ghost and the new-sale success.
        third = _shoot_buttons(
            probe_server, cookie, f"invoice-{theme_id}",
            f"{INVOICE_PRINT_SELECTOR},{INVOICE_DANGER_SELECTOR},"
            f"{INVOICE_GHOST_SELECTOR},{INVOICE_SUCCESS_SELECTOR}",
            path=f"/sales/invoice/{invoice_sale_id}",
        )
        invoice_captures = third["buttons"]
        assert len(invoice_captures) == 4, \
            f"{theme_id}: the invoice should offer print, refund, PDF and new-sale — got {len(invoice_captures)}"

        controls = [
            ("customer search", search_capture),
            ("anonymous sale ghost", till_ghost_capture),
            ("confirm payment", confirm_capture),
            ("send to terminal ghost", terminal_capture),
            ("card option selected (default)", card_default_capture),
            ("cash option unselected", cash_default_capture),
            ("نسیه confirm", calm["buttons"][0]),
            ("نسیه radio selected", calm["buttons"][1]),
            ("card option unselected (after نسیه)", calm["buttons"][2]),
            ("نسیه over-limit confirm", over["buttons"][0]),
            ("نسیه over-limit radio", over["buttons"][1]),
            ("invoice print", invoice_captures[0]),
            ("invoice refund danger", invoice_captures[1]),
            ("invoice PDF ghost", invoice_captures[2]),
            ("invoice new sale", invoice_captures[3]),
        ]
        for what, capture in controls:
            ratio, label_px, fill_px, distinct = _worst_label_on_fill(
                Path(capture["png"]), capture["rect"])
            evidence.append(f"{theme_id} {what}: {ratio:.2f}:1 ({label_px} on {fill_px}, {distinct} label px)")
            if ratio < 4.5:
                offenders.append(
                    f"{theme_id}: the {what} button's label reads {ratio:.2f}:1 at the "
                    f"pixels ({label_px} on {fill_px}) — the pair guard's floor is 4.5:1")

    print("\npixel evidence:")
    for line in evidence:
        print("  " + line)
    if offenders:  # pragma: no cover
        raise AssertionError("a till button's label does not read at the pixels:\n"
                             + "\n".join(offenders))
