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


PAYMENT_LABELS = {"card": "کارت", "cash": "نقد", "credit": "نسیه", "split": "ترکیبی"}


def payment_label(method) -> str:
    """Paper name of a payment method — one table, HTML and PDF agree."""
    return PAYMENT_LABELS.get(method or "", "نقد")


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


def generate_invoice_pdf(sale, customer, items, store_name=None, store=None,
                         cashier_name=None, credit_remaining=None,
                         footer_note="", payment_parts=()) -> str:
    """
    Generate a PDF invoice and save it. Returns the URL path, or None if the
    PDF library is unavailable. Persian text is reshaped + bidi-ordered with
    the bundled Farisi.ttf so invoices print correctly for Persian shops.

    One file per sale: a finished invoice never changes, so a revisit reuses
    the same path instead of stacking timestamped copies. A refunded sale
    gets its own `_void` file carrying the void stamp.
    """
    store_name = store_name or _store_name()
    try:
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        invoice_dir = Path("static/uploads/invoices")
        invoice_dir.mkdir(parents=True, exist_ok=True)

        void = bool(getattr(sale, "is_refunded", False))
        filename = f"invoice_{sale.id}{'_void' if void else ''}.pdf"
        filepath = invoice_dir / filename
        if filepath.exists():
            return f"/static/uploads/invoices/{filename}"

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

        store = store or {}
        for extra in (store.get("address") or "", store.get("phone") or ""):
            if extra:
                draw_line(extra, 20 * mm, y, size=8)
                y -= 5 * mm

        now = datetime.now(timezone.utc)
        draw_line(f"تاریخ: {gregorian_to_jalali(now)}", 20 * mm, y, size=9)
        c.setFont(font, 9)
        c.drawRightString(width - 20 * mm, y, fa(f"شماره فاکتور: {sale.id}"))
        y -= 7 * mm
        if cashier_name:
            draw_line(f"صندوقدار: {cashier_name}", 20 * mm, y, size=9)
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
        c.drawRightString(70 * mm, y, fa("قیمت واحد"))
        c.drawRightString(92 * mm, y, fa("تعداد"))
        c.drawRightString(width - 20 * mm, y, fa("جمع"))
        y -= 5 * mm
        c.line(20 * mm, y, width - 20 * mm, y)
        y -= 6 * mm

        def _item_name(item):
            variant = getattr(item, "variant", None)
            if variant and getattr(variant, "display_name", None):
                return variant.display_name
            product = getattr(item, "product", None)
            return product.name if product and getattr(product, "name", None) else "نامشخص"

        c.setFont(font, 9)
        for item in items:
            name = _item_name(item)
            name = (name[:25] + "...") if len(name) > 25 else name
            rendered_name = fa(name) if persian else name
            c.drawString(20 * mm, y, rendered_name)
            c.drawRightString(70 * mm, y, fa(format_toman(item.unit_price)))
            c.drawRightString(92 * mm, y, fa(str(item.quantity)))
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
            try:
                import json as _json
                details = sale.discount_details
                details = _json.loads(details) if isinstance(details, str) else (details or [])
            except Exception:
                details = []
            for detail in details:
                draw_line(f"· {detail}", 20 * mm, y, size=8)
                y -= 5 * mm
        draw_line(f"مبلغ قابل پرداخت: {format_toman(sale.final_amount)}", 20 * mm, y, size=11, bold=True)
        y -= 10 * mm

        draw_line(f"روش پرداخت: {payment_label(sale.payment_method)}", 20 * mm, y, size=9)
        y -= 6 * mm
        if sale.payment_method == "split":
            for part in payment_parts:
                method = getattr(part, "method", "")
                draw_line(f"{'نقدی' if method == 'cash' else 'کارتی'}: "
                          f"{format_toman(getattr(part, 'amount', 0))}", 20 * mm, y, size=9)
                y -= 6 * mm
        if sale.payment_method == "credit":
            if (sale.credit_surcharge or 0) > 0:
                draw_line(f"کارمزد نسیه: {format_toman(sale.credit_surcharge)}", 20 * mm, y, size=9)
                y -= 6 * mm
            if credit_remaining is not None:
                draw_line(f"بدهی نسیه این فاکتور: {format_toman(credit_remaining)}", 20 * mm, y, size=9)
                y -= 6 * mm
            if getattr(sale, "credit_due_date", None):
                draw_line(f"سررسید: {gregorian_to_jalali(sale.credit_due_date)}", 20 * mm, y, size=9)
                y -= 6 * mm
        y -= 6 * mm

        if void:
            draw_line("ابطال شد", 20 * mm, y, size=14, bold=True)
            y -= 8 * mm
            if getattr(sale, "refund_reason", None):
                draw_line(f"دلیل: {sale.refund_reason}", 20 * mm, y, size=9)
                y -= 6 * mm

        # Scannable strip: the sale id as Code39, so a return is one scan.
        try:
            from reportlab.graphics.barcode.code39 import Standard39
            strip = Standard39(str(sale.id), barHeight=12 * mm, barWidth=0.5)
            strip.drawOn(c, (width - strip.width) / 2, y - 14 * mm)
            y -= 18 * mm
        except Exception:
            logger.warning("Could not draw invoice barcode", exc_info=True)

        if footer_note:
            draw_line(footer_note, 20 * mm, y, size=8)
            y -= 6 * mm
        draw_line("تعویض کالا با ارائه این فاکتور انجام می‌شود.", 20 * mm, y, size=8)
        y -= 6 * mm

        c.setFont(font, 9)
        c.drawCentredString(width / 2, y, fa("از خرید شما متشکریم"))

        c.save()
        return f"/static/uploads/invoices/{filename}"

    except ImportError:
        return None
    except Exception as e:
        logger.error("PDF generation error: %s", e)
        return None
