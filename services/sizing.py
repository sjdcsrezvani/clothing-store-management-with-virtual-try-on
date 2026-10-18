"""Sizing engine — two-hop conversion through one axis.

Every clothing system is a ladder of labels; every rung carries a person-height
band. Body-based systems (age bands, height/EU codes, Chinese height codes, US
year numerals) share one core growth table; garment-based systems (Iranian
length numbers, letters) carry per-garment-type ladders. Age is derived from
height through the core table; display speaks Latin digits and shows months
only in the baby zone, adults as «بزرگسال».

All data lives in the app — the shop is offline. Tables are seeded from
standard charts and calibrated by the shop; the Iranian-length seeds are
explicitly provisional.
"""
from __future__ import annotations

from dataclasses import dataclass

from models import to_english_digits

SIZE_TYPES = {
    "upper": "بالاتنه",
    "lower": "پایین‌تنه",
    "set": "سرهمی/ست",
    "shoes": "کفش",
}

SYSTEMS = {
    "age": {"label": "سن (ماه/سال)"},
    "height": {"label": "قد / EU (سانت)"},
    "cn": {"label": "چین (سانت)"},
    "us": {"label": "سال عددی (US/CN)"},
    "iran": {"label": "ایران (قد لباس)"},
    "letters": {"label": "حروف (S تا 3XL)"},
}


@dataclass(frozen=True)
class Band:
    """A resolved size: person height (cm) and age (months) — one of them may be
    authoritative; adults carry no age."""
    height_min: int
    height_max: int
    months_min: int | None
    months_max: int | None


# The one core table (H&M/BOSS/TOG24 consensus): month band -> height band.
# The last row is the adult tail — months beyond 16y display as «بزرگسال».
CORE_ROWS = [
    (0, 1, 44, 50), (1, 2, 50, 56), (2, 4, 56, 62), (4, 6, 62, 68),
    (6, 9, 68, 74), (9, 12, 74, 80), (12, 18, 80, 86), (18, 24, 86, 92),
    (24, 36, 92, 98), (36, 48, 98, 104), (48, 60, 104, 110), (60, 72, 110, 116),
    (72, 84, 116, 122), (84, 96, 122, 128), (96, 108, 128, 134), (108, 120, 134, 140),
    (120, 132, 140, 146), (132, 144, 146, 152), (144, 156, 152, 158), (156, 168, 158, 164),
    (168, 180, 164, 170), (180, 192, 170, 176), (192, 1200, 176, 188),
]

_HEIGHT_RUNGS = [44, 50, 56, 62, 68, 74, 80, 86, 92,
                 98, 104, 110, 116, 122, 128, 134, 140, 146, 152, 158, 164, 170, 176, 182, 188]
_CN_RUNGS = [59, 66, 73, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170]
_US_YEARS = list(range(2, 17))

# Provisional seeds until the shop calibrates with real stock (per garment
# type): center height = base + slope * (N - 40).
_IRAN_SEEDS = {
    "upper": (92, 2.8, 7),
    "set": (92, 2.8, 7),
    "lower": (92, 2.15, 6),
}
_IRAN_RUNGS = [str(n) for n in range(35, 85, 5)]

# Adult letters: deliberately wide, overlapping bands — letters are body-fit
# codes, not heights; the shop calibrates with what it actually stocks.
_LETTER_BANDS = {
    "S": (150, 168), "M": (158, 175), "L": (165, 182),
    "XL": (172, 190), "2XL": (178, 195), "3XL": (182, 200),
}


def _months_for_span(height_min: int, height_max: int):
    """The core row that overlaps [height_min, height_max) the most; a tie
    goes to the younger row (charts read EU 122 as 6-7 years)."""
    best, best_overlap = None, 0
    for row in CORE_ROWS:
        overlap = min(height_max, row[3]) - max(height_min, row[2])
        if overlap > best_overlap:
            best, best_overlap = row, overlap
    if best is None:
        return None, None
    return best[0], best[1]


def _age_code(row) -> str | None:
    months_min, months_max = row[0], row[1]
    if months_max <= 24:
        return f"{months_min}-{months_max}m"
    if months_max > 192 or months_min % 12 or months_max % 12:
        return None
    return f"{months_min // 12}-{months_max // 12}"


def _age_band(code: str):
    for row in CORE_ROWS:
        if _age_code(row) == code:
            return Band(row[2], row[3], row[0], row[1])
    return None


def _numeric_code_band(value: str, rungs, tolerance: int):
    if not value.isdigit():
        return None
    number = int(value)
    if number not in rungs:
        return None
    months_min, months_max = _months_for_span(number - tolerance, number + tolerance)
    return Band(number - tolerance, number + tolerance, months_min, months_max)


def _us_band(value: str):
    if not value.isdigit():
        return None
    year = int(value)
    if year not in _US_YEARS:
        return None
    months_min, months_max = year * 12, year * 12 + 12
    heights = [row for row in CORE_ROWS if row[0] < months_max and row[1] > months_min]
    if not heights:
        return None
    return Band(min(r[2] for r in heights), max(r[3] for r in heights), months_min, months_max)


def _iran_band(value: str, size_type):
    if size_type not in _IRAN_SEEDS or value not in _IRAN_RUNGS:
        return None
    base, slope, half = _IRAN_SEEDS[size_type]
    center = int(base + slope * (int(value) - 40) + 0.5)
    months_min, months_max = _months_for_span(center - half, center + half)
    return Band(center - half, center + half, months_min, months_max)


def _letters_band(value: str, size_type):
    if size_type not in ("upper", "lower", "set"):
        return None
    key = value.strip().upper()
    band = _LETTER_BANDS.get(key)
    if band is None:
        return None
    return Band(band[0], band[1], None, None)


def resolve_band(system: str, size, size_type=None):
    """Resolve a tag size into a Band, or None when it cannot be understood —
    a None never blocks a save."""
    text = to_english_digits(str(size or "")).strip().replace("–", "-").replace("—", "-")
    if not text:
        return None
    if system == "age":
        return _age_band(text)
    if system == "height":
        return _numeric_code_band(text, _HEIGHT_RUNGS, 3)
    if system == "cn":
        return _numeric_code_band(text, _CN_RUNGS, 5)
    if system == "us":
        return _us_band(text)
    if system == "iran":
        return _iran_band(text, size_type)
    if system == "letters":
        return _letters_band(text, size_type)
    return None


def _age_text(band: Band) -> str:
    if band.months_min is None or band.months_min >= 192:
        return "بزرگسال"
    left, right = band.months_min, band.months_max
    if right <= 24:
        unit = "ماه"
    else:
        left, right = left // 12, -(-right // 12)
        unit = "سال"
    return f"{left}-{right} {unit}" if left != right else f"{left} {unit}"


def _height_text(band: Band) -> str:
    return f"قد {band.height_min} تا {band.height_max}"


def band_display(band: Band, standard: str = "age") -> str:
    """The band in one chosen display system."""
    if standard == "height":
        return _height_text(band)
    return _age_text(band)


def band_summary(band: Band, standard: str = "age") -> str:
    """The conversion line the forms show: the chosen standard first, the other
    beside it. Same values, two readings."""
    if standard == "height":
        return f"{_height_text(band)} · {_age_text(band)}"
    return f"{_age_text(band)} · {_height_text(band)}"


def get_sizing_standard(db) -> str:
    """The shop's chosen display unit for sizes — a system id, «age» when unset
    or when the stored value is one the engine cannot name."""
    from models import Settings

    row = db.query(Settings).filter(Settings.key == "sizing_standard").first()
    value = (row.value or "").strip() if row else ""
    return value if value in SYSTEMS else "age"


def size_options(system: str, size_type=None):
    """The ladder for a picker: [{'value', 'label'}] — or None for a system the
    form should leave as free text."""
    if system == "age":
        options = []
        for row in CORE_ROWS:
            code = _age_code(row)
            if code is None:
                continue
            if row[1] <= 24:
                label = f"{row[0]}-{row[1]} ماه"
            else:
                label = f"{row[0] // 12}-{row[1] // 12} سال"
            options.append({"value": code, "label": label})
        return options
    if system == "height":
        return [{"value": str(n), "label": str(n)} for n in _HEIGHT_RUNGS]
    if system == "cn":
        return [{"value": str(n), "label": str(n)} for n in _CN_RUNGS]
    if system == "us":
        return [{"value": str(y), "label": f"{y} سال"} for y in _US_YEARS]
    if system == "iran":
        if size_type not in _IRAN_SEEDS:
            return None
        return [{"value": n, "label": n} for n in _IRAN_RUNGS]
    if system == "letters":
        if size_type not in ("upper", "lower", "set"):
            return None
        return [{"value": key, "label": key} for key in _LETTER_BANDS]
    return None
