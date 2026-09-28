"""The on-premise SMS gateway: the phone's half of the conversation.

The old gateway ran on a VPS the shop rented; the store called it over HTTP and
it relayed to the phone. That middleman is gone. The endpoints the APK already
speaks live here now — same paths, same headers, same JSON shapes — but the
data they touch is the store's own ``SmsDevice``/``SmsMessage`` rows, so there
is one database and one truth about what was sent.

Three deliberate differences from the VPS version:

* **Offline is not an error.** The VPS API refused a send when the device was
  offline, so a birthday wish queued while the phone was asleep was *lost* after
  retries. Here a queued message simply waits for the next claim — «the phone
  isn't here yet» is the normal case on a shop Wi-Fi.
* **The key is a plain token.** The phone polls every 15 seconds; hashing a
  bcrypt round over every row on every poll is the wrong shape. The key is a
  43-char urlsafe token stored with SHA-256 (hashed-at-rest, cheap to verify,
  shown exactly once at pairing).
* **Latency is honest.** The phone's foreground service polls every 15s, so a
  message reaches the handset within that window — no 5-minute scheduler batch
  in front of it.
"""
from __future__ import annotations

import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from sqlalchemy.orm import Session

from models import SmsDevice, SmsMessage

GATEWAY_PORT = 8101
CLAIM_LIMIT = 20                    # messages per poll, like the VPS version
STALE_CLAIM_MINUTES = 5             # a claim older than this is handed out again
HEARTBEAT_OFFLINE_MINUTES = 3       # no heartbeat for this long ⇒ «آفلاین»

DEVICE_AUTH_HEADER = "X-Device-API-Key"


def _is_usable_lan_ip(value: str) -> bool:
    """An IPv4 address a phone on the same WiFi could dial."""
    try:
        parts = [int(p) for p in value.split(".")]
    except (AttributeError, ValueError):
        return False
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        return False
    if parts[0] == 127:
        return False
    if parts[0] == 169 and parts[1] == 254:
        return False  # link-local: no router, no phone route
    return True


def _is_private_ip(value: str) -> bool:
    parts = [int(p) for p in value.split(".")]
    return (
        parts[0] == 10
        or (parts[0] == 172 and 16 <= parts[1] <= 31)
        or (parts[0] == 192 and parts[1] == 168)
    )


def _wifi_ip_darwin() -> str | None:
    """The Mac's WiFi address straight from the interface.

    The old default-route probe answers with the VPN tunnel the moment one
    is up — an address the phone can never dial. en0 *is* the WiFi on every
    Mac that matters here, so it is asked first, by name.
    """
    import subprocess
    import sys

    if not sys.platform.startswith("darwin"):
        return None
    try:
        out = subprocess.run(
            ["ipconfig", "getifaddr", "en0"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, ValueError):
        return None
    candidate = (out.stdout or "").strip()
    return candidate if _is_usable_lan_ip(candidate) else None


def _tunnel_ips() -> set[str]:
    """Addresses no phone can dial: VPN tunnels and point-to-point links.

    The UDP default-route probe answers with the tunnel the moment a VPN is
    up — that 10.x address looks private and plausible, but the phone's route
    to it does not exist. Anything bound to a tunnel interface is excluded.
    """
    import re
    import subprocess
    import sys

    if not sys.platform.startswith("darwin"):
        return set()
    try:
        out = subprocess.run(
            ["ifconfig", "-a"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, ValueError):
        return set()
    found: set[str] = set()
    point_to_point = False
    for line in (out.stdout or "").splitlines():
        head = re.match(r"^(\w+): flags=\d+<([^>]*)>", line)
        if head:
            flags = head.group(2).split(",")
            point_to_point = "POINTOPOINT" in flags
            continue
        if point_to_point:
            match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", line)
            if match:
                found.add(match.group(1))
    return found


def _hostname_ip() -> str | None:
    """Whatever this machine calls itself, if it is dialable."""
    import socket

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    tunnels = _tunnel_ips()
    dialable = [info[4][0] for info in infos
                if _is_usable_lan_ip(info[4][0]) and info[4][0] not in tunnels]
    privates = [ip for ip in dialable if _is_private_ip(ip)]
    if privates:
        return privates[0]
    return dialable[0] if dialable else None


def _default_route_ip() -> str | None:
    """Last resort: the source address of the default route.

    Right when a VPN is up — the case that kept printing the tunnel address
    on the pairing page — so a VPN address is refused here even if usable:
    a tunnel IP is never the phone's route.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        candidate = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    if not _is_usable_lan_ip(candidate) or candidate in _tunnel_ips():
        return None
    return candidate


def lan_ip() -> str:
    """This machine's LAN address — the one the phone dials.

    One implementation for the launcher print, the pairing QR and the page:
    WiFi interface first, hostname second, default route last, loopback never.
    A new PC answers with its own address with no configuration.
    """
    for probe in (_wifi_ip_darwin, _hostname_ip, _default_route_ip):
        try:
            found = probe()
        except OSError:
            continue
        if found:
            return found
    return "127.0.0.1"


def gateway_port() -> int:
    """The port the phone pairs to — one truth for the listener and the QR.

    The launcher binds this port and the pairing page encodes it; reading the
    same variable in both places is what keeps a custom ``RAYKID_GATEWAY_PORT``
    from pairing the phone to a port nothing listens on.
    """
    import os

    try:
        return int(os.environ.get("RAYKID_GATEWAY_PORT", "") or GATEWAY_PORT)
    except (TypeError, ValueError):
        return GATEWAY_PORT


# ── the device and its key ────────────────────────────────────────────────────

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands naive datetimes back; treat them as UTC for comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def device_health(db: Session) -> SmsDevice | None:
    """The single device, with its status brought up to date on read."""
    device = db.query(SmsDevice).order_by(SmsDevice.id.asc()).first()
    if device is None or not device.last_seen_at:
        return device
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=HEARTBEAT_OFFLINE_MINUTES)
    if device.status == "online" and _as_utc(device.last_seen_at) < cutoff:
        device.status = "offline"
        db.commit()
    return device


_OFFLINE_ALERT_KEY = "sms_gateway_offline_alerted"


def gateway_status(db: Session) -> dict:
    """What the dashboard strip needs: paired, online, and what is waiting.

    No money figures — safe for every role that sees the dashboard. A phone
    never paired is not an outage, so it reads as quiet, not alarming.
    """
    device = device_health(db)
    if device is None or device.status == "never_connected":
        return {"paired": False, "online": False, "offline": False,
                "queued": 0, "device_name": "", "last_seen_at": None}
    queued = db.query(SmsMessage).filter(SmsMessage.status == "queued").count()
    online = device.status == "online"
    return {"paired": True, "online": online, "offline": not online,
            "queued": int(queued), "device_name": device.name or "",
            "last_seen_at": device.last_seen_at}


def check_gateway_offline(db: Session) -> str | None:
    """One alert per outage, one note per recovery — for the scheduler.

    Returns "alerted" the first pass the paired phone reads offline,
    "recovered" the first pass it is back, else None. The latch lives in
    Settings so a restart mid-outage does not re-announce it.
    """
    from models import Settings

    status = gateway_status(db)
    row = db.query(Settings).filter(Settings.key == _OFFLINE_ALERT_KEY).first()
    latched = bool(row and row.value)
    if not status["paired"]:
        if latched:
            row.value = ""
            db.commit()
        return None
    if status["offline"] and not latched:
        if row is None:
            db.add(Settings(key=_OFFLINE_ALERT_KEY, value="1"))
        else:
            row.value = "1"
        db.commit()
        return "alerted"
    if not status["offline"] and latched:
        row.value = ""
        db.commit()
        return "recovered"
    return None


def pair_device(db: Session, *, name: str = "", phone: str = "") -> tuple[SmsDevice, str]:
    """Create (or re-key) the shop's device and return it with the raw key.

    The raw key is returned **once** — it goes into the pairing QR on the
    پیامک page and nowhere else; only its SHA-256 is stored.
    """
    raw_key = secrets.token_urlsafe(32)
    device = device_health(db)
    if device is None:
        device = SmsDevice(name=name or "گوشی فروشگاه", phone=phone or None)
        db.add(device)
    device.name = name or device.name
    if phone:
        device.phone = phone
    device.api_key_hash = _hash_key(raw_key)
    device.status = "never_connected"
    device.last_seen_at = None
    db.flush()
    return device, raw_key


def unpair_device(db: Session) -> bool:
    device = db.query(SmsDevice).order_by(SmsDevice.id.asc()).first()
    if device is None:
        return False
    db.delete(device)
    return True


def authenticate_device(db: Session, header_value: str | None) -> SmsDevice | None:
    """Constant-shape key check against the one device row."""
    if not header_value:
        return None
    device = device_health(db)
    if device is None or not device.api_key_hash:
        return None
    if secrets.compare_digest(_hash_key(header_value), device.api_key_hash):
        return device
    return None


def pairing_qr_data_uri(pairing: dict) -> str:
    """The QR as an inline image: base URL + key, the APK's two fields.

    Rendered from the freshly issued key in the request cycle — the stored hash
    can never be turned back into a QR, so an old page cannot re-pair a phone.
    """
    payload = f"{pairing['base_url']}|{pairing['api_key']}"
    import base64

    encoded = base64.b64encode(qr_png(payload)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def qr_png(data: str) -> bytes:
    """A PNG of the pairing QR, drawn by the same library the tags use."""
    image = qrcode.make(data)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ── what the phone does ───────────────────────────────────────────────────────

def poll(db: Session, device: SmsDevice) -> list[dict]:
    """Claim waiting messages for the phone.

    Both the *unclaimed* queue and any claim older than the stale window are
    handed out in one transaction, mirroring the VPS claim: a phone that died
    mid-send cannot wedge a message forever, and a second loop cannot steal a
    fresh claim. ``attempts`` records the handout so the history can say so.
    """
    now = datetime.now(timezone.utc)
    device.last_seen_at = now
    device.status = "online"

    stale_cutoff = now - timedelta(minutes=STALE_CLAIM_MINUTES)
    fresh = db.query(SmsMessage).filter(
        SmsMessage.status == "queued",
        SmsMessage.delivery_state == "",
    ).order_by(SmsMessage.id.asc()).limit(CLAIM_LIMIT).all()
    stale = [row for row in db.query(SmsMessage).filter(
        SmsMessage.status == "queued",
        SmsMessage.delivery_state == "claimed",
        SmsMessage.claimed_at.is_not(None),
    ).order_by(SmsMessage.id.asc()).limit(CLAIM_LIMIT).all()
        if _as_utc(row.claimed_at) < stale_cutoff][:CLAIM_LIMIT - len(fresh)]

    claimed: list[dict] = []
    for row in (*fresh, *stale):
        row.delivery_state = "claimed"
        row.claimed_at = now
        row.attempts = (row.attempts or 0) + 1
        claimed.append({"id": row.id, "to_number": row.phone, "message": row.body})
    # Flushed (not just dirty) so a second poll — even in this same session,
    # before the caller commits — cannot hand the same rows out twice.
    db.flush()
    return claimed


def heartbeat(db: Session, device: SmsDevice, *, status: str = "online",
              battery_level: int | None = None, signal_strength: int | None = None,
              app_version: str | None = None) -> None:
    device.last_seen_at = datetime.now(timezone.utc)
    if status in ("online", "offline"):
        device.status = status
    if battery_level is not None:
        device.battery_level = int(battery_level)
    if signal_strength is not None:
        device.signal_strength = int(signal_strength)
    if app_version:
        device.app_version = str(app_version)[:40]


def report_result(db: Session, device: SmsDevice, message_id: int, *,
                  success: bool, error: str | None = None) -> bool:
    """The phone's verdict on one send: «sent» or «failed»."""
    row = db.query(SmsMessage).filter(
        SmsMessage.id == message_id,
        SmsMessage.status == "queued",
        SmsMessage.delivery_state == "claimed",
    ).first()
    if row is None:
        return False
    if success:
        row.status = "sent"
        row.delivery_state = "claimed"      # awaiting the delivery note
        row.sent_at = datetime.now(timezone.utc)
        row.sent_by_device_id = device.id
        row.error = None
    else:
        row.status = "failed"
        row.error = (error or "گوشی ارسال را ناموفق گزارش کرد")[:500]
    # Flushed so the delivery report the APK sends next — in this same session,
    # before any commit — sees the row as «sent», not its pre-report state.
    db.flush()
    return True


def report_delivery(db: Session, device: SmsDevice, message_id: int, *,
                    success: bool) -> bool:
    """The carrier's verdict, after the send. Only a «sent» row can move."""
    row = db.query(SmsMessage).filter(
        SmsMessage.id == message_id,
        SmsMessage.sent_by_device_id == device.id,
    ).first()
    if row is None or row.status != "sent":
        return False
    row.delivery_state = "delivered" if success else "undelivered"
    if not success:
        row.error = "پیام به مخاطب نرسید (گزارش اپراتور)"
    db.flush()
    return True


def release_stale_claims(db: Session) -> int:
    """Return timed-out claims to the queue so the next poll re-hands them.

    Called from the scheduler tick; on a quiet shop this is the thing that
    makes a crashed phone self-heal without human attention.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES)
    rows = [row for row in db.query(SmsMessage).filter(
        SmsMessage.status == "queued",
        SmsMessage.delivery_state == "claimed",
        SmsMessage.claimed_at.is_not(None),
    ).all() if _as_utc(row.claimed_at) < cutoff]
    for row in rows:
        row.delivery_state = ""
        row.claimed_at = None
    return len(rows)


# ── what the store page shows ─────────────────────────────────────────────────

def queue_snapshot(db: Session) -> dict:
    """The درگاه tab's numbers: nothing here reads a second database."""
    queued = db.query(SmsMessage).filter(SmsMessage.status == "queued").count()
    claimed = db.query(SmsMessage).filter(
        SmsMessage.status == "queued", SmsMessage.delivery_state == "claimed",
    ).count()
    delivered = db.query(SmsMessage).filter(
        SmsMessage.delivery_state == "delivered",
    ).count()
    return {"queued": queued, "claimed": claimed, "delivered": delivered}
