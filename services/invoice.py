import logging
from datetime import datetime, timezone
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display

from services._common import gregorian_to_jalali
from services.store import get_store

logger = logging.getLogger(__name__)


def format_toman(amount: int) -> str:
    """Format amount with comma separator and تومان suffix."""
    return f"{amount:,} تومان"


def _store_name() -> str:
    try:
        return get_store().get("name") or "فروشگاه"
    except Exception:
        return "فروشگاه"


def fa(text) -> str:
    """Shape + reorder a Persian string for correct RTL rendering."""
    try:
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)


def generate_invoice_text(sale, customer, items, store_name=None) -> str:
    """Generate a simple text invoice."""
    store_name = store_name or _store_name()
    now = datetime.now(timezone.utc)

    lines = [
        "=" * 40,
        f"    {store_name}",
        "=" * 40,
        f"تاریخ: {gregorian_to_jalali(now)}",
        f"شماره فاکتور: {sale.id}",
        "",
    ]

    if customer:
        full_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip() or "—"
        lines.extend([
            f"مشتری: {full_name}",
            f"تلفن: {customer.phone}",
            "",
        ])

    lines.extend([
        "-" * 40,
        f"{'کالا':<20} {'تعداد':>5} {'قیمت':>10} {'جمع':>10}",
        "-" * 40,
    ])

    for item in items:
        name = item.product.name[:18] if item.product else "نامشخص"
        lines.append(
            f"{name:<20} {item.quantity:>5} {format_toman(item.unit_price):>10} {format_toman(item.total_price):>10}"
        )

    lines.extend([
        "-" * 40,
        f"جمع کل: {format_toman(sale.total_amount):>30}",
    ])

    if sale.discount_amount > 0:
        lines.append(f"تخفیف: {format_toman(sale.discount_amount):>30}")

    lines.extend([
        f"مبلغ قابل پرداخت: {format_toman(sale.final_amount):>30}",
        "",
        f"روش پرداخت: {sale.payment_method}",
        "",
        "=" * 40,
        "    از خرید شما متشکریم",
        "    با ما دوباره خرید کنید",
        "=" * 40,
    ])

    return "\n".join(lines)


_FONT_REGISTERED = False


def _register_font(c):
    """Register the bundled Persian TTF once; returns the font name to use."""
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        font_path = Path("static/fonts/Farisi.ttf")
        if font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont("Farisi", str(font_path)))
                # Same face under a Bold alias so setFont("Farisi-Bold") works.
                pdfmetrics.registerFont(TTFont("Farisi-Bold", str(font_path)))
                _FONT_REGISTERED = True
            except Exception as e:
                logger.warning("Could not register Persian font: %s", e)
        if not _FONT_REGISTERED:
            _FONT_REGISTERED = True  # fall back to Helvetica below
    return "Farisi" if _FONT_REGISTERED and Path("static/fonts/Farisi.ttf").exists() else "Helvetica"


def generate_invoice_pdf(sale, customer, items, store_name=None) -> str:
    """
    Generate a PDF invoice and save it. Returns the URL path, or None if the
    PDF library is unavailable. Persian text is reshaped + bidi-ordered with
    the bundled Farisi.ttf so invoices print correctly for Persian shops.
    """
    store_name = store_name or _store_name()
    try:
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        invoice_dir = Path("static/uploads/invoices")
        invoice_dir.mkdir(parents=True, exist_ok=True)

        filename = f"invoice_{sale.id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.pdf"
        filepath = invoice_dir / filename

        c = canvas.Canvas(str(filepath), pagesize=A5)
        width, height = A5
        font = _register_font(c)
        persian = font != "Helvetica"  # shaping is only meaningful with the TTF

        def draw_line(text, x, y, size=10, bold=False, right=False):
            c.setFont(font if not bold else f"{font}-Bold", size)
            rendered = fa(text) if persian else str(text)
            if right:
                c.drawRightString(x, y, rendered)
            else:
                c.drawString(x, y, rendered)

        y = height - 18 * mm
        c.setFont(font, 16)
        c.drawCentredString(width / 2, y, fa(store_name) if persian else store_name)
        y -= 9 * mm

        now = datetime.now(timezone.utc)
        draw_line(f"تاریخ: {gregorian_to_jalali(now)}", 20 * mm, y, size=9)
        c.setFont(font, 9)
        c.drawRightString(width - 20 * mm, y, fa(f"شماره فاکتور: {sale.id}"))
        y -= 7 * mm

        if customer:
            full_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip() or "—"
            draw_line(f"مشتری: {full_name}", 20 * mm, y, size=9)
            y -= 5 * mm
            draw_line(f"تلفن: {customer.phone}", 20 * mm, y, size=9)
            y -= 8 * mm

        # Items table header
        draw_line("کالا", 20 * mm, y, size=9, bold=True)
        c.setFont(font, 9)
        c.drawRightString(80 * mm, y, fa("تعداد"))
        c.drawRightString(105 * mm, y, fa("قیمت"))
        c.drawRightString(width - 20 * mm, y, fa("جمع"))
        y -= 5 * mm
        c.line(20 * mm, y, width - 20 * mm, y)
        y -= 6 * mm

        c.setFont(font, 9)
        for item in items:
            name = (item.product.name[:25] + "...") if item.product and len(item.product.name) > 25 else (item.product.name if item.product else "نامشخص")
            rendered_name = fa(name) if persian else name
            c.drawString(20 * mm, y, rendered_name)
            c.drawRightString(80 * mm, y, fa(str(item.quantity)))
            c.drawRightString(105 * mm, y, fa(format_toman(item.unit_price)))
            c.drawRightString(width - 20 * mm, y, fa(format_toman(item.total_price)))
            y -= 5 * mm
            if y < 25 * mm:  # avoid overflow — start a new page
                c.showPage()
                c.setFont(font, 9)
                y = height - 15 * mm

        c.line(20 * mm, y, width - 20 * mm, y)
        y -= 8 * mm

        draw_line(f"جمع کل: {format_toman(sale.total_amount)}", 20 * mm, y, size=10)
        y -= 6 * mm
        if sale.discount_amount > 0:
            draw_line(f"تخفیف: {format_toman(sale.discount_amount)}", 20 * mm, y, size=10)
            y -= 6 * mm
        draw_line(f"مبلغ قابل پرداخت: {format_toman(sale.final_amount)}", 20 * mm, y, size=11, bold=True)
        y -= 10 * mm

        draw_line(f"روش پرداخت: {'کارت' if sale.payment_method == 'card' else 'نقد'}", 20 * mm, y, size=9)
        y -= 12 * mm

        c.setFont(font, 9)
        c.drawCentredString(width / 2, y, fa("از خرید شما متشکریم"))
        y -= 5 * mm
        c.drawCentredString(width / 2, y, fa("با ما دوباره خرید کنید"))

        c.save()
        return f"/static/uploads/invoices/{filename}"

    except ImportError:
        return None
    except Exception as e:
        logger.error("PDF generation error: %s", e)
        return None
