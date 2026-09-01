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


def log_event(event: str, details: dict | None = None) -> None:
    """Write the same JSON-line transaction diagnostics as server.js."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "event": event,
        **(details or {}),
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
) -> dict:
    """Send one Parsian sale and wait for the terminal's final response.

    This mirrors server.js: the socket sends the ASCII ``RQ`` payload, gathers
    all response chunks, treats a response followed by a quiet socket as a
    complete response, and only then classifies the result.
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
            payload = build_parsian_payload(amount_text)
            log_event(
                "socket_connected",
                {
                    "ip": host,
                    "port": port,
                    "payload_length": len(payload),
                    "payload": payload,
                },
            )
            sock.sendall(payload.encode("ascii"))
            log_event(
                "payload_sent",
                {"ip": host, "port": port, "bytes": len(payload.encode("ascii"))},
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
) -> dict:
    del response_window  # The tested protocol waits for the final POS result.
    return send_sale(host, port, amount, timeout=timeout)
