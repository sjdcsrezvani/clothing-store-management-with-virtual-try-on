"""Parsian/Newland POS integration.

The terminal exposes a raw TCP socket in PC mode. This module intentionally
keeps the transport and response parsing isolated from checkout so the tested
protocol from the standalone ``server.js`` project is used verbatim.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
from pathlib import Path

DEFAULT_HOST = "192.168.1.155"
DEFAULT_PORT = 8500
POS_TIMEOUT_SECONDS = 120
LOG_FILE = Path(__file__).resolve().parent.parent / "transactions.log"

logger = logging.getLogger(__name__)

RESPONSE_CODE_LABELS = {
    "00": "Approved",
    "05": "Declined - do not honor",
    "14": "Declined - invalid card number",
    "51": "Declined - insufficient funds",
    "54": "Declined - expired card",
    "55": "Declined - incorrect PIN",
    "57": "Declined - transaction not permitted",
    "58": "Declined - terminal not permitted",
    "91": "Declined - issuer unavailable, try again",
}


def _read_response_code(response: str) -> str | None:
    """Read the five-digit response value from a packed PEC response."""
    match = re.search(r"RS\d{3}RS(\d{5})", response)
    return match.group(1) if match else None


def classify_response(raw: str) -> dict:
    """Classify a terminal response using the rules in the working server.js.

    PEC sale responses use ``00200`` for approval and ``00250`` for a payment
    cancelled on the terminal. Named ``ResponseCode=`` replies and packed
    ``RS...RS.....`` replies are both accepted.
    """
    response = (raw or "").rstrip("\x00").strip()
    named_match = re.search(
        r"(?:^|\r?\n)\s*ResponseCode\s*=\s*([^\r\n]+)",
        response,
        flags=re.IGNORECASE,
    )
    response_code = named_match.group(1).strip() if named_match else _read_response_code(response)

    if response_code in ("00", "00200"):
        return {"status": "approved", "response_code": response_code, "label": "Approved"}
    if response_code == "00250":
        return {"status": "cancelled", "response_code": response_code, "label": "Cancelled on POS"}
    if response_code:
        return {
            "status": "declined",
            "response_code": response_code,
            "label": RESPONSE_CODE_LABELS.get(response_code, "Declined"),
        }
    return {"status": "unknown", "response_code": None, "label": "Unknown response"}


def _safe_details(details: dict | None) -> dict:
    details = details or {}
    safe = {}
    for key, value in details.items():
        if key in {"payload", "text", "response", "error"}:
            safe[key] = "[redacted]"
        elif key in {"amount", "ip"}:
            safe[key] = "[redacted]"
        else:
            safe[key] = value
    return safe


def log_event(event: str, details: dict | None = None) -> None:
    """Write redacted JSON-line transaction diagnostics."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "event": event,
        **_safe_details(details),
    }
    line = json.dumps(entry, ensure_ascii=False)
    logger.info(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{line}\n")
    except OSError as error:
        logger.warning("Could not write transaction log: %s", error)


def build_parsian_payload(amount_str: str) -> str:
    """Build the tested Parsian sale payload from server.js exactly."""
    process_code = "PR006000000"
    currency = "CU003364"
    print_mode = "PD0011"

    amount_block = f"AM{len(amount_str):03d}{amount_str}"
    request_body = f"{process_code}{amount_block}{currency}{print_mode}"
    request_block = f"RQ{len(request_body):03d}{request_body}"
    return f"{len(request_block):04d}{request_block}"


# --- Merchant receipt printing (R4 + R8) -----------------------------------
# Photo-proven on a Newland SP830 across ~40 terminal probes (2026-10-06/07):
# one R4 (120B cap, terminal trims past it) plus one R8 (28B hard cap —
# anything longer drops the WHOLE tag, no graceful trim), both placed
# before PD, windows-1256 bytes with byte-count lengths. Firmware quirks
# the code below defends against: uppercase LT anywhere aborts the sale,
# *, = and leading @ jump to the line end, ×/…/emoji have no glyphs, and
# multiple number-runs per line render in reverse byte order (so item lines
# carry {name} {total} {qty} and read {name} {qty} {total}).
RECEIPT_MAX_BYTES = 120
RECEIPT_R8_MAX_BYTES = 28
RECEIPT_NAME_MAX_BYTES = 30

_THANKS_LINE = "دوستدار شما رای کیدز"
_FOOTER_THANKS = ("از خرید شما ممنونیم", "رای کیدز دوستدار شما")


class _UnsafeReceipt(Exception):
    """Receipt text that must never reach the wire (dropped digit)."""


def _receipt_bytes(text: str) -> bytes:
    """Encode receipt text the way the terminal printer reads it.

    Farsi Yeh has no windows-1256 slot, so it normalizes to Arabic Yeh
    (prints identically). Glyph-less characters (emoji, ×, …) are dropped —
    except digits: a dropped digit destroys an amount, so that raises and
    the caller falls back to the footer instead of sending a lie.
    """
    out = bytearray()
    for char in text.replace("ی", "ي"):
        if ord(char) < 128:
            out.append(ord(char))
            continue
        try:
            encoded = char.encode("windows-1256")
        except UnicodeEncodeError:
            if char.isdigit():
                raise _UnsafeReceipt(f"dropped digit {char!r}")
            continue
        if len(encoded) == 1:
            out.append(encoded[0])
        elif char.isdigit():
            raise _UnsafeReceipt(f"dropped digit {char!r}")
    return bytes(out)


def _clean_name(name: str) -> str:
    """Truncate a product name to the byte budget, never mid-character."""
    out = ""
    for char in str(name or "—").replace("\n", " "):
        candidate = out + char
        try:
            if len(_receipt_bytes(candidate)) > RECEIPT_NAME_MAX_BYTES:
                break
        except _UnsafeReceipt:
            continue
        out = candidate
    return out or "—"


def build_receipt_text(items, discount_amount: int, final_amount: int,
                       handle: str, ref: str) -> str:
    """Shop receipt in the photo-frozen shape. Latin digits — the terminal
    draws them Persian-style on its own, and Persian digits have no wire
    slot at all."""
    lines = []
    for item in items or []:
        qty = int(item.get("quantity") or 0)
        total = int(item.get("total_price") or 0)
        if qty <= 0 or total < 0:
            continue
        lines.append(f"{_clean_name(item.get('name'))} {total} {qty}")
    lines.append(f"تخفیف {int(discount_amount or 0)}")
    lines.append(f"جمع {int(final_amount or 0)}")
    lines.append(f"{handle} {ref}".strip())
    return "\n".join(lines)


def build_receipt_payload(items, discount_amount: int, final_amount: int,
                          shop_name: str, ref: str) -> bytes | None:
    """Receipt wire bytes, or None when the footer must go instead.

    None means: over 120B, an uppercase-LT tripwire anywhere, or a dropped
    digit. The caller falls back to the footer — a sale is never blocked
    or sent with corrupt figures.
    """
    _ = shop_name  # the shop brands the app invoice; the slip is too small
    handle = "raykids_official"
    text = build_receipt_text(items, discount_amount, final_amount, handle, ref)
    if "LT" in text:
        return None
    try:
        data = _receipt_bytes(text)
    except _UnsafeReceipt:
        return None
    if len(data) > RECEIPT_MAX_BYTES:
        return None
    return data


def build_receipt_thanks() -> bytes:
    """R8 thanks line. Fixed Persian, always encodable, always fits."""
    return _receipt_bytes(_THANKS_LINE)


def build_footer_payload(shop_name: str, ref: str) -> bytes:
    """Footer-only R4: handle + ref + the bigger thanks note. ~95B, always
    fits; hard-truncated only against an absurd shop configuration."""
    _ = shop_name
    text = "\n".join(["raykids_official " + ref, *_FOOTER_THANKS])
    try:
        data = _receipt_bytes(text)
    except _UnsafeReceipt:  # fixed text cannot drop digits; belt and braces
        data = b"raykids_official"
    return data[:RECEIPT_MAX_BYTES]


def wants_receipt(receipt_on: bool, product_count: int, r4: bytes | None) -> bool:
    """The send gate: setting ON, at most 2 distinct products, and bytes
    that fit. Bytes win over the count on any conflict."""
    return bool(receipt_on and product_count <= 2 and r4)


def build_parsian_payload_bytes(amount_str: str, r4: bytes | None = None,
                                r8: bytes | None = None) -> bytes:
    """Sale payload as wire bytes, receipt tags riding before PD.

    Tagless this is byte-identical to build_parsian_payload; with tags, a
    single R4 (byte-count length) then R8 precede the print flag — the slip
    order the terminal confirmed (R4 block first, R8 tail after).
    Latin-1 carries the receipt bytes through the ASCII framing untouched.
    """
    message = build_parsian_payload(amount_str)
    if not r4 and not r8:
        return message.encode("ascii")
    tags = ""
    if r4:
        tags += "R4" + f"{len(r4):03d}" + r4.decode("latin-1")
    if r8:
        tags += "R8" + f"{len(r8):03d}" + r8.decode("latin-1")
    content = message[9:]  # past the LLLL RQ LLL framing, which is recomputed
    before, _, _ = content.partition("PD0011")
    body = before + tags + "PD0011"
    framed = f"RQ{len(body):03d}{body}"
    return f"{len(framed):04d}{framed}".encode("latin-1")


def get_terminal_config(db) -> dict:
    """Resolve terminal address from Settings, with the tested app defaults."""
    from models import Settings

    values = {
        row.key: row.value
        for row in db.query(Settings).filter(
            Settings.key.in_(["pos_terminal_host", "pos_terminal_port"])
        ).all()
    }
    host = DEFAULT_HOST
    if "pos_terminal_host" in values:
        host = (values["pos_terminal_host"] or "").strip()

    port = DEFAULT_PORT
    if "pos_terminal_port" in values:
        try:
            port = int((values["pos_terminal_port"] or "").strip())
        except (TypeError, ValueError):
            port = 0

    # Host/port become an explicit POS configuration when the owner saves
    # either setting. This lets older/manual-card installs keep working until
    # the tested POS connection is deliberately enabled from Settings.
    configured = "pos_terminal_host" in values and "pos_terminal_port" in values
    return {
        "host": host,
        "port": port,
        "enabled": bool(host and 1 <= port <= 65535),
        "configured": configured,
        "receipt_2item": values.get("pos_receipt_2item", "0").strip() == "1",
    }


def check_connection(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 2.0,
) -> dict:
    """Perform a read-only TCP reachability check; never sends a sale."""
    started_at = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {
                "online": True,
                "latency_ms": int((time.time() - started_at) * 1000),
            }
    except (OSError, ValueError):
        return {"online": False, "latency_ms": None}


def send_sale(
    host: str,
    port: int,
    amount: int | str,
    timeout: float = POS_TIMEOUT_SECONDS,
    r4: bytes | None = None,
    r8: bytes | None = None,
) -> dict:
    """Send one Parsian sale and wait for the terminal's final response.

    This mirrors server.js: the socket sends the ASCII ``RQ`` payload, gathers
    all response chunks, treats a response followed by a quiet socket as a
    complete response, and only then classifies the result. Receipt tags ride
    before PD and print at the bottom of the merchant slip.
    """
    amount_text = str(amount)
    if not amount_text.isdigit() or int(amount_text) < 0:
        raise ValueError("Amount must be a numeric string")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("POS host is required")
    if not 1 <= int(port) <= 65535:
        raise ValueError("POS port is invalid")

    host = host.strip()
    port = int(port)
    address = f"{host}:{port}"
    started_at = time.time()
    response_chunks: list[bytes] = []
    log_event("socket_connecting", {"ip": host, "port": port, "amount": amount_text})

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            payload = build_parsian_payload_bytes(amount_text, r4, r8)
            log_event(
                "socket_connected",
                {
                    "ip": host,
                    "port": port,
                    "payload_length": len(payload),
                    "payload": payload,
                },
            )
            sock.sendall(payload)
            log_event(
                "payload_sent",
                {"ip": host, "port": port, "bytes": len(payload)},
            )

            sock.settimeout(timeout)
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    if response_chunks:
                        break
                    raise TimeoutError(f"POS transaction timed out after {int(timeout)} seconds.")
                if not chunk:
                    break
                text = chunk.decode("ascii", errors="replace")
                log_event(
                    "response_chunk",
                    {"ip": host, "port": port, "bytes": len(chunk), "text": text},
                )
                response_chunks.append(chunk)

        response = b"".join(response_chunks).decode("ascii", errors="replace")
        result = classify_response(response)
        log_event(
            "transaction_response",
            {
                "ip": host,
                "port": port,
                "amount": amount_text,
                "duration_ms": int((time.time() - started_at) * 1000),
                "response_length": len(response),
                **result,
                "response": response,
            },
        )
        return {
            "ok": True,
            "url": address,
            "response": response,
            **result,
        }
    except Exception as error:
        log_event(
            "transaction_error",
            {
                "ip": host,
                "port": port,
                "amount": amount_text,
                "duration_ms": int((time.time() - started_at) * 1000),
                "error": str(error),
            },
        )
        raise


# Backwards-compatible name for callers that used the experimental adapter.
def send_amount(
    amount: int,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = POS_TIMEOUT_SECONDS,
    response_window: float | None = None,
    r4: bytes | None = None,
    r8: bytes | None = None,
) -> dict:
    del response_window  # The tested protocol waits for the final POS result.
    return send_sale(host, port, amount, timeout=timeout, r4=r4, r8=r8)
