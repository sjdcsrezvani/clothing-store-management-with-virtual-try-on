import os
from datetime import datetime, timezone
from pathlib import Path


def format_toman(amount: int) -> str:
    """Format amount with comma separator and تومان suffix."""
    return f"{amount:,} تومان"


def generate_invoice_text(sale, customer, items, store_name="رای کیدز") -> str:
    """Generate a simple text invoice."""
    now = datetime.now(timezone.utc)
    
    lines = [
        "=" * 40,
        f"    {store_name}",
        "=" * 40,
        f"تاریخ: {now.strftime('%Y/%m/%d %H:%M')}",
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


def generate_invoice_pdf(sale, customer, items, store_name="رای کیدز") -> str:
    """
    Generate a PDF invoice and save it.
    Returns the file path.
    """
    try:
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        
        # Create invoices directory
        invoice_dir = Path("static/uploads/invoices")
        invoice_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"invoice_{sale.id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.pdf"
        filepath = invoice_dir / filename
        
        # Create PDF
        c = canvas.Canvas(str(filepath), pagesize=A5)
        width, height = A5
        
        # Simple layout without Persian font support (would need arabic-reshaper + bidi)
        # For now, use basic text
        y = height - 20 * mm
        
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(width / 2, y, store_name)
        y -= 10 * mm
        
        c.setFont("Helvetica", 10)
        now = datetime.now(timezone.utc)
        c.drawString(20 * mm, y, f"Date: {now.strftime('%Y/%m/%d %H:%M')}")
        c.drawRightString(width - 20 * mm, y, f"Invoice #{sale.id}")
        y -= 8 * mm
        
        if customer:
            full_name = f"{customer.first_name or ''} {customer.last_name or ''}".strip() or "N/A"
            c.drawString(20 * mm, y, f"Customer: {full_name}")
            y -= 5 * mm
            c.drawString(20 * mm, y, f"Phone: {customer.phone}")
            y -= 8 * mm
        
        # Items table header
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, "Product")
        c.drawString(80 * mm, y, "Qty")
        c.drawString(100 * mm, y, "Price")
        c.drawRightString(width - 20 * mm, y, "Total")
        y -= 5 * mm
        
        # Line
        c.line(20 * mm, y, width - 20 * mm, y)
        y -= 5 * mm
        
        # Items
        c.setFont("Helvetica", 9)
        for item in items:
            name = (item.product.name[:25] + "...") if len(item.product.name) > 25 else item.product.name
            c.drawString(20 * mm, y, name)
            c.drawString(80 * mm, y, str(item.quantity))
            c.drawString(100 * mm, y, format_toman(item.unit_price))
            c.drawRightString(width - 20 * mm, y, format_toman(item.total_price))
            y -= 5 * mm
        
        # Line
        c.line(20 * mm, y, width - 20 * mm, y)
        y -= 8 * mm
        
        # Totals
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, y, f"Subtotal: {format_toman(sale.total_amount)}")
        y -= 6 * mm
        
        if sale.discount_amount > 0:
            c.drawString(20 * mm, y, f"Discount: {format_toman(sale.discount_amount)}")
            y -= 6 * mm
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20 * mm, y, f"Total: {format_toman(sale.final_amount)}")
        y -= 10 * mm
        
        c.setFont("Helvetica", 10)
        c.drawCentredString(width / 2, y, f"Payment: {sale.payment_method}")
        y -= 15 * mm
        
        c.setFont("Helvetica", 9)
        c.drawCentredString(width / 2, y, "Thank you for your purchase!")
        y -= 5 * mm
        c.drawCentredString(width / 2, y, "Come back soon!")
        
        c.save()
        
        # Return relative path
        return f"/static/uploads/invoices/{filename}"
        
    except ImportError:
        # ReportLab not installed, return None
        return None
    except Exception as e:
        print(f"PDF generation error: {e}")
        return None
