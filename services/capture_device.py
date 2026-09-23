"""The capture phone's own pairing, on the model the SMS gateway already uses.

The photo capture page used to be set up by hand: whoever held the phone typed
the computer's LAN address into one field and the app's static API token into
another. The token was shared by everyone, never rotated, copied off the
server's environment, and the address was a typo waiting to happen — on the one
screen that is used standing up, holding a child's photo.

The gateway phone already solved both halves: a per-device key issued on pair,
stored only as its SHA-256, shown exactly once in a QR that carries the address
and the key together. This module is that pattern for the capture phone. There
is no second device row to hang it on — re-keying the SMS phone's row would cut
the message sender off — so the hash lives in the settings store: one device,
one key, and pairing again kills the old key.
"""
from __future__ import annotations

import hashlib
import io
import secrets

import qrcode
from sqlalchemy.orm import Session

from models import Settings

# The one setting row that holds the capture phone's key hash, and the header
# the phone sends it under. Distinct from the gateway's `X-Device-API-Key` on
# purpose: two phones, two credentials, two stores — a header that named both
# would let one phone's key be mistaken for the other's.
KEY_HASH_SETTING = "capture_device_key_hash"
AUTH_HEADER = "X-Capture-Key"


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stored_hash(db: Session) -> str:
    row = db.query(Settings).filter(Settings.key == KEY_HASH_SETTING).first()
    return (row.value if row else "") or ""


def _set_hash(db: Session, key_hash: str) -> None:
    row = db.query(Settings).filter(Settings.key == KEY_HASH_SETTING).first()
    if row is None:
        row = Settings(key=KEY_HASH_SETTING, value=key_hash)
        db.add(row)
    else:
        row.value = key_hash
    db.flush()


def is_paired(db: Session) -> bool:
    """Whether a capture phone holds a key right now."""
    return bool(_stored_hash(db))


def pair_device(db: Session) -> str:
    """Issue (or rotate) the capture phone's key and return the raw value.

    The raw key is returned **once** — it goes into the pairing QR and nowhere
    else; only its SHA-256 is stored. Pairing again invalidates the key the
    previous phone holds, which is also how a lost phone is shut out.
    """
    raw_key = secrets.token_urlsafe(32)
    _set_hash(db, _hash_key(raw_key))
    return raw_key


def unpair_device(db: Session) -> bool:
    """Forget the capture phone: its key stops working immediately."""
    row = db.query(Settings).filter(Settings.key == KEY_HASH_SETTING).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def authenticate_device(db: Session, header_value: str | None) -> bool:
    """Constant-shape key check against the one stored hash."""
    if not header_value:
        return False
    stored = _stored_hash(db)
    if not stored:
        return False
    return secrets.compare_digest(_hash_key(header_value), stored)


def pairing_url(base_url: str, raw_key: str) -> str:
    """What the QR encodes: the phone's camera opens it as a link.

    The capture page is session-guarded — the phone may not be logged in when it
    scans — so the URL cannot depend on a server redirect to carry the key: the
    key rides in the **fragment** (``#…``), which browsers never transmit to the
    server. The landing page reads it locally, stores it, and proves it with a
    ping; no request on this server ever carries the credential in its request
    line, so nothing of it can land in an access log.
    """
    return f"{base_url}/admin/mobile/pair#{raw_key}"


def pairing_qr_data_uri(base_url: str, raw_key: str) -> str:
    """The pairing URL as an inline PNG, drawn by the same library the tags use."""
    import base64

    image = qrcode.make(pairing_url(base_url, raw_key))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
