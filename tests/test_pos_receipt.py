"""Terminal receipt (R4 + R8) — photo-proven on a Newland SP830.

Contract from paper: one R4 (120B cap, terminal trims past it) plus one R8
(28B hard cap — anything longer drops the WHOLE tag), both before PD,
windows-1256 bytes with byte-count lengths, PD=1, face Toman digits.
Firmware quirks defended in code: uppercase LT aborts the sale, *, = and
leading @ jump to the line end, ×/…/emoji have no glyphs, multiple
number-runs per line render reversed (so item lines carry
{name} {total} = {qty} and read {name} {qty} = {total}).
"""
from services.pos_terminal import (
    RECEIPT_MAX_BYTES,
    RECEIPT_R8_MAX_BYTES,
    _receipt_bytes,
    build_footer_payload,
    build_parsian_payload,
    build_parsian_payload_bytes,
    build_receipt_payload,
    build_receipt_text,
    build_receipt_thanks,
    wants_receipt,
)


def test_proven_persian_vector_matches_terminal_accepted_bytes():
    """The exact R4019 payload the terminal approved and printed."""
    receipt = _receipt_bytes("فروشگاه رایکید سپاس")
    assert len(receipt) == 19
    wire = build_parsian_payload_bytes("5000", receipt, None)
    expect = bytes.fromhex(
        "303036335251303538"
        "5052303036303030303030414d30303435303030"
        "43553030333336345234303139"
        "ddd1e6d490c7e520d1c7ed98edcf20d381c7d3"
        "504430303131"
    )
    assert wire == expect


def test_no_receipt_path_is_byte_identical_to_legacy():
    assert build_parsian_payload_bytes("180000") == \
        build_parsian_payload("180000").encode("ascii")


def test_receipt_shape_item_total_qty_discount_final_handle_ref():
    items = [{"name": "شلوار", "quantity": 2, "total_price": 200000},
             {"name": "تیشرت", "quantity": 1, "total_price": 80000}]
    text = build_receipt_text(items, 20000, 260000, "raykids_official", "inv48271")
    assert text.splitlines() == [
        "شلوار 200000 2",
        "تیشرت 80000 1",
        "تخفیف 20000",
        "جمع 260000",
        "raykids_official inv48271",
    ]
    wire = build_receipt_payload(items, 20000, 260000, "", "inv48271")
    assert wire is not None and len(wire) <= RECEIPT_MAX_BYTES


def test_inv_ref_costs_digits_plus_three():
    assert len("inv48271".encode("ascii")) == 8  # 1-3 digit invoices: 4-6B
    assert len("inv1234567".encode("ascii")) == 10  # 7-digit max: 10B


def test_unencodable_chars_are_dropped_not_fatal():
    """Emoji in product names and Farsi Yeh must never fault a sale."""
    items = [{"name": "🧸 خرس", "quantity": 1, "total_price": 50000}]
    wire = build_receipt_payload(items, 0, 50000, "", "inv7")
    assert wire is not None
    assert "🧸".encode("utf-8") not in wire  # emoji dropped
    assert _receipt_bytes("ی") == _receipt_bytes("ي")  # Yeh normalizes


def test_dropped_digit_falls_back_to_footer_never_a_lie():
    """Persian digits have no windows-1256 slot — amounts using them must
    refuse the receipt rather than ship figures with holes."""
    from services.pos_terminal import _UnsafeReceipt
    try:
        _receipt_bytes("۲")
        raised = False
    except _UnsafeReceipt:
        raised = True
    assert raised


def test_lt_tripwire_falls_back_to_footer():
    items = [{"name": "ULTRA", "quantity": 1, "total_price": 1000}]
    assert build_receipt_payload(items, 0, 1000, "", "inv1") is None


def test_overflow_falls_back_to_footer():
    items = [{"name": "شلوار خیلی بلند و توضیح‌دار", "quantity": 9,
              "total_price": 9000000}] * 6
    assert build_receipt_payload(items, 0, 54000000, "", "inv99") is None


def test_footer_shape_and_fit():
    data = build_footer_payload("", "inv48271")
    assert data.decode("windows-1256").splitlines() == [
        "raykids_official inv48271",
        "از خريد شما ممنونيم",
        "راي کيدز دوستدار شما",
    ]
    assert len(data) <= RECEIPT_MAX_BYTES


def test_thanks_line_shape_and_fit():
    data = build_receipt_thanks()
    assert len(data) <= RECEIPT_R8_MAX_BYTES


def test_r4_rides_before_pd_then_r8_with_byte_count_lengths():
    wire = build_parsian_payload_bytes("5000", b"ABC", b"XY")
    text = wire.decode("latin-1")
    assert "R4003ABC" in text and "R8002XY" in text
    assert text.index("R4003ABC") < text.index("R8002XY") < text.index("PD0011")


def test_send_gate_setting_count_bytes():
    assert wants_receipt(True, 2, b"x") is True
    assert wants_receipt(False, 2, b"x") is False  # setting OFF: footer
    assert wants_receipt(True, 3, b"x") is False  # >2 products: footer
    assert wants_receipt(True, 2, None) is False  # bytes win: overflow/LT
    assert wants_receipt(True, 1, b"x") is True
