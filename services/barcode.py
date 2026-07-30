import os
import random
import string
from pathlib import Path


def generate_barcode_number() -> str:
    """Generate a unique 12-digit barcode number."""
    return ''.join(random.choices(string.digits, k=12))


def generate_barcode_image(barcode_number: str, product_name: str = "") -> str:
    """
    Generate a barcode image and save it to disk.
    Returns the relative path to the saved image.
    """
    try:
        import barcode
        from barcode.writer import ImageWriter
        
        # Create barcode directory if it doesn't exist
        barcode_dir = Path("static/uploads/barcodes")
        barcode_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate Code128 barcode
        code128 = barcode.get('code128', barcode_number, writer=ImageWriter())
        
        # Save barcode image
        filepath = barcode_dir / f"barcode_{barcode_number}"
        saved_path = code128.save(str(filepath), options={
            'module_width': 0.3,
            'module_height': 15,
            'font_size': 10,
            'text_distance': 5,
            'quiet_zone': 6.5,
        })
        
        # Return relative path for database storage
        return saved_path.replace("static/", "/static/")
        
    except ImportError:
        # Fallback if barcode library not installed
        return None
    except Exception as e:
        print(f"Barcode generation error: {e}")
        return None


def get_or_create_barcode(existing_barcode: str = None) -> tuple[str, str]:
    """
    Get existing barcode or generate a new one.
    Returns (barcode_number, image_path)
    """
    if existing_barcode:
        return existing_barcode, None
    
    barcode_number = generate_barcode_number()
    image_path = generate_barcode_image(barcode_number)
    
    return barcode_number, image_path
