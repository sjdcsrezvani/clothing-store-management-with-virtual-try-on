"""Sizing engine — every tag language through one band.

A tag is entered as one number/letter, never a range. The band is the honest
range behind it: a child's body height and age, or an adult numeric size. The
shop's display setting picks exactly one unit — age, letters, body height or
garment number — and the same band answers all four where it can. Adult
numeric sizes are body-measurement codes, not heights, so body/garment display
is «—» for them.

All data lives in the app — the shop is offline. Tables are seeded from
published charts and calibrated by the shop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from models import to_english_digits

SIZE_TYPES = {
    "upper": "بالاتنه",
    "lower": "پایین‌تنه",
    "set": "سرهمی/ست",
    "shoes": "کفش",
}

SYSTEMS = {
    "age": {"label": "سن — مثل 18m، 3T یا 8"},
    "height": {"label": "قد — مثل 120 یا 122"},
    "kids_letters": {"label": "حروف بچگانه — مثل M(8/10)"},
    "adult": {"label": "بزرگسال — مثل S یا 38"},
    "iran": {"label": "شماره لباس — مثل 45"},
    "sock": {"label": "سایز جوراب — مثل 23-26"},
    "shoe_eu": {"label": "سایز کفش — مثل 30"},
    "shoe_uk": {"label": "سایز کفش — مثل 7"},
    "shoe_us": {"label": "سایز کفش — مثل 8C"},
    "shoe_cm": {"label": "طول پا — مثل 14 سانت"},
}

DISPLAY_STANDARDS = {
    "age": "سن (سال/ماه)",
    "letters": "حروف (XS تا 3XL)",
    "height": "قد شخص (سانت)",
    "garment": "قد لباس (شماره)",
}


@dataclass(frozen=True)
class Band:
    """A resolved size. Child bands carry a height and an age; adult bands
    carry a numeric size code. One of the two sides is authoritative."""
    height_min: int | None
    height_max: int | None
    months_min: int | None
    months_max: int | None
    adult_min: int | None = None
    adult_max: int | None = None


# The one core table: month band -> height band. The last row is the adult
# tail — months from 16y display as «بزرگسال».
CORE_ROWS = [
    (0, 1, 44, 50), (1, 2, 50, 56), (2, 4, 56, 62), (4, 6, 62, 68),
    (6, 9, 68, 74), (9, 12, 74, 80), (12, 18, 80, 86), (18, 24, 86, 92),
    (24, 36, 92, 98), (36, 48, 98, 104), (48, 60, 104, 110), (60, 72, 110, 116),
    (72, 84, 116, 122), (84, 96, 122, 128), (96, 108, 128, 134), (108, 120, 134, 140),
    (120, 132, 140, 146), (132, 144, 146, 152), (144, 156, 152, 158), (156, 168, 158, 164),
    (168, 180, 164, 170), (180, 192, 170, 176), (192, 1200, 176, 188),
]

# Height pickers offer single numbers only. Double tags are entered by their
# lower number; the table below keeps the full pair and its band.
_HEIGHT_RUNGS_EU = [44, 50, 56, 62, 68, 74, 80, 86, 92,
                    98, 104, 110, 116, 122, 128, 134, 140, 146, 152, 158, 164, 170, 176, 182, 188]
_HEIGHT_RUNGS_CM = [59, 66, 73, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170]
_HEIGHT_SINGLES = sorted(set(_HEIGHT_RUNGS_EU) | set(_HEIGHT_RUNGS_CM) | {112})
_CN_ONLY = {59, 66, 73, 90, 100, 120, 130, 150, 160}

# lower number -> (height_min, height_max, months_min, months_max)
_HEIGHT_DOUBLES = {
    86: (80, 92, 12, 24),
    98: (92, 104, 24, 48),
    110: (104, 116, 48, 72),
    112: (112, 118, 60, 72),
    122: (116, 128, 72, 96),
    134: (128, 140, 96, 120),
    146: (140, 152, 120, 144),
    158: (152, 164, 144, 168),
    170: (164, 176, 168, 192),
}

_KIDS_LETTERS = {
    "XS": (48, 60, 104, 110),
    "S": (72, 84, 116, 122),
    "M": (96, 120, 128, 140),
    "L": (120, 144, 140, 152),
    "XL": (144, 168, 152, 164),
}

_WOMEN_NUMBERS = list(range(32, 53, 2))
_MEN_NUMBERS = list(range(44, 63, 2))
_WOMEN_LETTERS = {
    "XS": (34, 34), "S": (36, 38), "M": (38, 40), "L": (40, 42),
    "XL": (42, 44), "XXL": (44, 46), "3XL": (46, 48),
}
_MEN_LETTERS = {
    "XS": (44, 46), "S": (46, 48), "M": (48, 50), "L": (50, 52),
    "XL": (52, 54), "XXL": (54, 56), "3XL": (56, 58),
}

# Published Iranian garment table. The size number is the tag number; the age
# bands are the chart's own (they deliberately skip alternate years).
_IRAN_ROWS = [
    ("25", 0, 6, 44, 68),
    ("30", 6, 12, 68, 80),
    ("35", 12, 24, 80, 92),
    ("40", 36, 48, 98, 104),
    ("45", 48, 60, 104, 110),
    ("50", 72, 84, 116, 122),
    ("55", 96, 108, 128, 134),
    ("60", 120, 132, 140, 146),
    ("65", 144, 156, 152, 158),
]
_IRAN_BY_VALUE = {row[0]: row for row in _IRAN_ROWS}

_SOCK_ROWS = [
    ("15-18", 0, 12, 44, 80),
    ("19-22", 12, 24, 80, 92),
    ("23-26", 24, 48, 92, 104),
    ("27-30", 48, 72, 104, 116),
    ("31-34", 72, 96, 116, 128),
    ("35-38", 96, 120, 128, 140),
]
_SOCK_BY_VALUE = {row[0]: row for row in _SOCK_ROWS}

# Kids' shoe sizes — one chart (EU, UK, US, heel-toe cm; Adidas kids as the
# seed) so four tag languages land on the same coarse sock band. A shoe size
# is not a body height, so the range is the honest answer. UK 1-5 and US 1-5.5
# exist on two scales; the picker writes the k/C/Y marker, and a bare number
# from that overlap resolves to nothing rather than to the wrong child.
_SHOE_ROWS = [
    (15, "0k", "1C", 8.1),
    (16, "0k", "1C", 8.1),
    (17, "1k", "2C", 9.0),
    (18, "2k", "3C", 9.8),
    (19, "3k", "4C", 10.6),
    (20, "4k", "5C", 11.5),
    (21, "5k", "5.5C", 12.3),
    (22, "5.5k", "6C", 12.8),
    (23, "6k", "6.5C", 13.2),
    (24, "7k", "7.5C", 14.0),
    (25, "7.5k", "8C", 14.5),
    (26, "8.5k", "9C", 15.3),
    (27, "9.5k", "10C", 16.1),
    (28, "10k", "10.5C", 16.6),
    (29, "11k", "11.5C", 17.4),
    (30, "11.5k", "12C", 17.8),
    (31, "12.5k", "13C", 18.7),
    (32, "13.5k", "1Y", 19.5),
    (33, "1y", "1.5Y", 20.0),
    (34, "2y", "2.5Y", 20.8),
    (35, "2.5y", "3Y", 21.2),
    (36, "3.5y", "4Y", 22.1),
    (37, "4.5y", "5Y", 22.9),
    (38, "5y", "5.5Y", 23.3),
]
_SHOE_UK = {row[1]: row[0] for row in _SHOE_ROWS}
_SHOE_US = {row[2]: row[0] for row in _SHOE_ROWS}
# A bare UK/US number is usable only outside the two-scale overlap.
_AMBIGUOUS_UK = {"1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5"}
_AMBIGUOUS_US = {"1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5"}
_SHOE_UK_BARE = {key[:-1]: eu for key, eu in _SHOE_UK.items()
                 if key.endswith("k") and key[:-1] not in _AMBIGUOUS_UK}
_SHOE_US_BARE = {key[:-1]: eu for key, eu in _SHOE_US.items()
                 if key.endswith("C") and key[:-1] not in _AMBIGUOUS_US}

_TODDLER = re.compile(r"^([2-5])\s*T$", re.I)
_MONTH_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)\s*(?:m|ماه|ماهه)$", re.I)
_MONTH_SINGLE = re.compile(r"^(\d+)\s*(?:m|ماه|ماهه)$", re.I)
_YEAR_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)\s*(?:سال|anos?)?$", re.I)
_YEAR_SINGLE = re.compile(r"^(\d+)\s*(?:سال|anos?)?$", re.I)
_HEIGHT_RANGE = re.compile(r"^(\d+)\s*[-/]\s*(\d+)$")
_LETTER_PREFIX = re.compile(r"^([A-Z]+)")


def _clean(value) -> str:
    return (to_english_digits(str(value or ""))
            .strip().replace("–", "-").replace("—", "-").replace("−", "-"))


def _is_man(gender) -> bool:
    return str(gender or "").strip().lower() == "man"


def _span_label(months_min: int, months_max: int) -> str:
    if months_max <= 24:
        return f"{months_min}-{months_max} ماه"
    return f"{months_min // 12}-{-(-months_max // 12)} سال"


def _band_from_months(months_min: int, months_max: int):
    rows = [row for row in CORE_ROWS if row[0] < months_max and row[1] > months_min]
    if not rows:
        return None
    return Band(min(row[2] for row in rows), max(row[3] for row in rows),
                months_min, months_max)


def _row_for_month(months: int):
    for row in CORE_ROWS:
        if row[0] <= months < row[1]:
            return row
    return None


def _months_for_span(height_min: int, height_max: int):
    """The core row that overlaps [height_min, height_max) the most; a tie
    goes to the younger row."""
    best, best_overlap = None, 0
    for row in CORE_ROWS:
        overlap = min(height_max, row[3]) - max(height_min, row[2])
        if overlap > best_overlap:
            best, best_overlap = row, overlap
    if best is None:
        return None, None
    return best[0], best[1]


def _year_band(year: int):
    if year < 1 or year > 16:
        return None
    return _band_from_months(year * 12, year * 12 + 12)


def _age_band(text: str):
    match = _TODDLER.match(text)
    if match:
        return _year_band(int(match.group(1)))

    match = _MONTH_RANGE.match(text)
    if match:
        left, right = sorted((int(match.group(1)), int(match.group(2))))
        return _band_from_months(left, right)

    match = _MONTH_SINGLE.match(text)
    if match:
        row = _row_for_month(int(match.group(1)))
        if row is None:
            return None
        return Band(row[2], row[3], row[0], row[1])

    match = _YEAR_RANGE.match(text)
    if match:
        left, right = sorted((int(match.group(1)), int(match.group(2))))
        if left < 1 or right > 16 or left >= right:
            return None
        return _band_from_months(left * 12, right * 12)

    match = _YEAR_SINGLE.match(text)
    if match:
        return _year_band(int(match.group(1)))
    return None


def _height_band(text: str):
    match = _HEIGHT_RANGE.match(text)
    if match:
        left, right = sorted((int(match.group(1)), int(match.group(2))))
        pair = _HEIGHT_DOUBLES.get(left)
        if pair and pair[1] == right:
            return Band(pair[0], pair[1], pair[2], pair[3])
        months = _months_for_span(left, right)
        return Band(left, right, months[0], months[1])

    if not text.isdigit():
        return None
    number = int(text)
    pair = _HEIGHT_DOUBLES.get(number)
    if pair:
        return Band(pair[0], pair[1], pair[2], pair[3])
    if number < 44 or number > 188:
        return None
    tolerance = 5 if number in _CN_ONLY else 3
    months = _months_for_span(number - tolerance, number + tolerance)
    return Band(number - tolerance, number + tolerance, months[0], months[1])


def _kids_letters_band(text: str):
    match = _LETTER_PREFIX.match(text.upper())
    if not match:
        return None
    row = _KIDS_LETTERS.get(match.group(1))
    if not row:
        return None
    return Band(row[2], row[3], row[0], row[1])


def _adult_band(text: str, gender=None):
    key = text.upper().replace("2XL", "XXL").replace("XXXL", "3XL")
    table = _MEN_LETTERS if _is_man(gender) else _WOMEN_LETTERS
    row = table.get(key)
    if row:
        return Band(None, None, None, None, row[0], row[1])
    numbers = _MEN_NUMBERS if _is_man(gender) else _WOMEN_NUMBERS
    match = re.match(r"^(\d+)\s*[-/]\s*(\d+)$", key)
    if match:
        left, right = sorted((int(match.group(1)), int(match.group(2))))
        if left in numbers and right in numbers:
            return Band(None, None, None, None, left, right)
        return None
    if not key.isdigit():
        return None
    number = int(key)
    if number in numbers:
        return Band(None, None, None, None, number, number)
    return None


def _iran_band(text: str):
    row = _IRAN_BY_VALUE.get(text)
    if not row:
        return None
    return Band(row[3], row[4], row[1], row[2])


def _sock_band(text: str):
    key = text.replace(" ", "").replace("/", "-")
    row = _SOCK_BY_VALUE.get(key)
    if not row:
        return None
    return Band(row[3], row[4], row[1], row[2])


def _sock_band_for_eu(eu: int):
    for value, months_min, months_max, height_min, height_max in _SOCK_ROWS:
        low, high = value.split("-")
        if int(low) <= eu <= int(high):
            return Band(height_min, height_max, months_min, months_max)
    return None


def _shoe_eu_band(text: str):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    # Adult shoes (EU 39+) have no clothing band; the raw tag is the honest
    # display, so they save without conversion.
    if number < 15 or number > 38:
        return None
    return _sock_band_for_eu(round(number))


def _shoe_uk_band(text: str):
    key = text.lower().replace(" ", "")
    eu = _SHOE_UK.get(key) or _SHOE_UK_BARE.get(key)
    return _sock_band_for_eu(eu) if eu else None


def _shoe_us_band(text: str):
    key = text.upper().replace(" ", "")
    eu = _SHOE_US.get(key) or _SHOE_US_BARE.get(key)
    return _sock_band_for_eu(eu) if eu else None


def _shoe_cm_band(text: str):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:cm|سانت(?:ی‌متر)?|سانتی‌متر)?", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    if value < 7.5 or value > 23.5:
        return None
    row = min(_SHOE_ROWS, key=lambda item: abs(item[3] - value))
    return _sock_band_for_eu(row[0])


def resolve_band(system: str, size, size_type=None, gender=None):
    """Resolve a tag size into a Band, or None when it cannot be understood —
    a None never blocks a save. Entry is one number/letter; a double tag is
    entered by its lower number and the tables keep the pair."""
    text = _clean(size)
    if not text:
        return None
    if system == "age":
        return _age_band(text)
    if system == "height":
        return _height_band(text)
    if system == "kids_letters":
        return _kids_letters_band(text)
    if system == "adult":
        return _adult_band(text, gender)
    if system == "iran":
        return _iran_band(text)
    if system == "sock":
        return _sock_band(text)
    if system == "shoe_eu":
        return _shoe_eu_band(text)
    if system == "shoe_uk":
        return _shoe_uk_band(text)
    if system == "shoe_us":
        return _shoe_us_band(text)
    if system == "shoe_cm":
        return _shoe_cm_band(text)
    return None


def _age_text(band: Band) -> str:
    if band.adult_min is not None:
        return "بزرگسال"
    if band.months_min is None:
        return "—"
    if band.months_min >= 192:
        return "بزرگسال"
    left, right = band.months_min, band.months_max
    if right <= 24:
        unit = "ماه"
    else:
        left, right = left // 12, -(-right // 12)
        unit = "سال"
    return f"{left}-{right} {unit}" if left != right else f"{left} {unit}"


def _height_text(band: Band) -> str:
    if band.height_min is None or band.height_max is None:
        return "—"
    return f"قد {band.height_min} تا {band.height_max}"


def _kids_letters_text(band: Band, gender=None) -> str:
    if band.adult_min is not None:
        table = _MEN_LETTERS if _is_man(gender) else _WOMEN_LETTERS
        low, high = band.adult_min, band.adult_max
        if low == high:
            for key, (left, right) in table.items():
                if left <= low <= right:
                    return key
            return "—"
        hits = [key for key, (left, right) in table.items()
                if high > left and low < right]
        if not hits:
            return "—"
        return hits[0] if len(hits) == 1 else f"{hits[0]} تا {hits[-1]}"

    if band.months_min is None:
        return "—"
    hits = [key for key, (start, end, _, _) in _KIDS_LETTERS.items()
            if band.months_max > start and band.months_min < end]
    if not hits:
        return "—"
    return hits[0] if len(hits) == 1 else f"{hits[0]} تا {hits[-1]}"


def _garment_text(band: Band) -> str:
    if band.adult_min is not None:
        return "—"
    if band.months_min is not None:
        middle = (band.months_min + band.months_max) / 2
        for value, start, end, _, _ in _IRAN_ROWS:
            if start <= middle <= end:
                return value
        best, best_distance = None, None
        for value, start, end, _, _ in _IRAN_ROWS:
            distance = min(abs(middle - start), abs(middle - end))
            if best_distance is None or distance < best_distance:
                best, best_distance = value, distance
        return best if best_distance is not None and best_distance <= 12 else "—"

    if band.height_min is None:
        return "—"
    middle = (band.height_min + band.height_max) / 2
    for value, _, _, start, end in _IRAN_ROWS:
        if start <= middle <= end:
            return value
    return "—"


def band_display(band: Band, standard: str = "age", gender=None) -> str:
    """The band in exactly one chosen display unit."""
    if band is None:
        return "—"
    if standard == "height":
        return _height_text(band)
    if standard == "letters":
        return _kids_letters_text(band, gender)
    if standard == "garment":
        return _garment_text(band)
    return _age_text(band)


def get_sizing_standard(db) -> str:
    """The shop's chosen display unit — «age» when unset or when the stored
    value is not a unit the engine can speak."""
    from models import Settings

    row = db.query(Settings).filter(Settings.key == "sizing_standard").first()
    value = (row.value or "").strip() if row else ""
    return value if value in DISPLAY_STANDARDS else "age"


def band_from_variant(variant) -> Band | None:
    """The stored band of a saved variant, or None when it never had one."""
    values = (
        variant.band_min_cm,
        variant.band_max_cm,
        variant.band_min_months,
        variant.band_max_months,
        getattr(variant, "band_min_adult", None),
        getattr(variant, "band_max_adult", None),
    )
    if all(value is None for value in values):
        return None
    return Band(values[0], values[1], values[2], values[3], values[4], values[5])


def variant_size_display(variant, standard: str = "age", fallback: bool = True) -> str:
    """A saved variant in the shop's chosen unit. When that unit has no honest
    value (adult numeric under body/garment length), the raw tag size is a
    more useful fallback on lists and printed tags than a dash."""
    band = band_from_variant(variant)
    if band is None:
        return variant.size or ""
    gender = variant.product.gender if getattr(variant, "product", None) else None
    text = band_display(band, standard, gender)
    if text == "—" and fallback:
        return variant.size or ""
    return text


def _adult_options(gender) -> list[dict]:
    table = _MEN_LETTERS if _is_man(gender) else _WOMEN_LETTERS
    numbers = _MEN_NUMBERS if _is_man(gender) else _WOMEN_NUMBERS
    options = []
    for key, (left, right) in table.items():
        label = f"{key} ({left})" if left == right else f"{key} ({left}-{right})"
        options.append({"value": key, "label": label})
    options.extend({"value": str(number), "label": str(number)} for number in numbers)
    return options


def _shoe_options(index: int) -> list[dict]:
    """The UK/US ladders: a marker keeps the two scales apart, and every
    option names its EU equivalent and age band so the right one is obvious."""
    options, seen = [], set()
    for row in _SHOE_ROWS:
        value = row[index]
        if value in seen:
            continue
        seen.add(value)
        band = _sock_band_for_eu(row[0])
        options.append({
            "value": value,
            "label": f"{value} (معادل {row[0]} · {_span_label(band.months_min, band.months_max)})",
        })
    return options


def size_options(system: str, gender=None):
    """The single-value ladder for a picker, or None for free text."""
    if system == "age":
        options = [{"value": f"{months}m", "label": f"{months} ماه"}
                   for months in (0, 3, 6, 9, 12, 18, 24)]
        options.extend({"value": str(year), "label": f"{year} سال"}
                       for year in range(2, 17))
        options.extend({"value": f"{year}T", "label": f"{year}T ({_span_label(year * 12, year * 12 + 12)})"}
                       for year in (2, 3, 4, 5))
        return options
    if system == "height":
        return [{"value": str(number), "label": str(number)} for number in _HEIGHT_SINGLES]
    if system == "kids_letters":
        return [{"value": key, "label": f"{key} ({_span_label(start, end)})"}
                for key, (start, end, _, _) in _KIDS_LETTERS.items()]
    if system == "adult":
        return _adult_options(gender)
    if system == "iran":
        return [{"value": value, "label": f"{value} ({_span_label(start, end)})"}
                for value, start, end, _, _ in _IRAN_ROWS]
    if system == "sock":
        return [{"value": value, "label": f"{value} ({_span_label(start, end)})"}
                for value, start, end, _, _ in _SOCK_ROWS]
    if system == "shoe_eu":
        options = []
        for eu, _, _, _ in _SHOE_ROWS:
            band = _sock_band_for_eu(eu)
            options.append({
                "value": str(eu),
                "label": f"{eu} ({_span_label(band.months_min, band.months_max)})",
            })
        return options
    if system == "shoe_uk":
        return _shoe_options(1)
    if system == "shoe_us":
        return _shoe_options(2)
    if system == "shoe_cm":
        options = []
        for cm in range(8, 24):
            row = min(_SHOE_ROWS, key=lambda item: abs(item[3] - cm))
            band = _sock_band_for_eu(row[0])
            options.append({
                "value": str(cm),
                "label": f"{cm} (معادل {row[0]} · {_span_label(band.months_min, band.months_max)})",
            })
        return options
    return None
