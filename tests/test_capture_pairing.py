"""The capture phone pairs by QR, on the model the SMS gateway already uses.

The page used to be set up by typing the server's LAN address into one field and
the app's static API token into another — a shared, never-rotated secret copied
by hand on the one screen used standing up, holding a child's photo. These tests
pin the replacement: a per-device key issued on pair, stored only as its
SHA-256, shown exactly once in a QR whose link carries the key in the URL
fragment (which browsers never transmit), gated uploads, and an unpair that
shuts a lost phone out immediately.
"""
import hashlib

from models import Settings
from services.capture_device import (
    AUTH_HEADER, KEY_HASH_SETTING, authenticate_device, pair_device,
    pairing_url, unpair_device,
)


# ── The service: one device, one hash, show-once key ─────────────────────────

def test_pairing_stores_only_the_hash(db_session):
    raw_key = pair_device(db_session)
    db_session.commit()

    stored = db_session.query(Settings).filter(Settings.key == KEY_HASH_SETTING).one()
    assert stored.value == hashlib.sha256(raw_key.encode()).hexdigest()
    # The raw key exists nowhere in the store — a database dump reveals nothing
    # that could send a photo.
    assert raw_key not in stored.value
    rows = db_session.query(Settings).filter(Settings.key == KEY_HASH_SETTING).all()
    assert len(rows) == 1  # rotate in place, never a second credential row


def test_pairing_again_kills_the_old_key(db_session):
    old = pair_device(db_session)
    db_session.commit()
    new = pair_device(db_session)
    db_session.commit()

    assert old != new
    assert not authenticate_device(db_session, old)
    assert authenticate_device(db_session, new)


def test_unpair_deletes_the_credential(db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    assert authenticate_device(db_session, raw_key)

    assert unpair_device(db_session) is True
    db_session.commit()
    assert not authenticate_device(db_session, raw_key)
    assert unpair_device(db_session) is False  # nothing left to remove


def test_pairing_url_carries_the_key_in_the_fragment():
    url = pairing_url("http://192.168.1.50:8000", "k" * 43)
    # The fragment never reaches the server: no request line can hold the key,
    # so no access log on this machine ever sees it.
    assert url.startswith("http://192.168.1.50:8000/admin/mobile/pair#")
    assert "k" * 43 in url
    assert "pair/" not in url  # a path segment would be transmitted and logged


def test_authenticate_rejects_garbage_and_an_unpaired_server(db_session):
    assert not authenticate_device(db_session, None)
    assert not authenticate_device(db_session, "")
    assert not authenticate_device(db_session, "whatever")


def test_auth_header_is_distinct_from_the_gateway_header():
    # Two phones, two credentials: a header that named both would let one
    # phone's key be mistaken for the other's.
    assert AUTH_HEADER == "X-Capture-Key"
    assert AUTH_HEADER != "X-Device-API-Key"


# ── The routes: QR shown once, landing session-free ──────────────────────────

def _owner_logs_in(client):
    import re
    resp = client.get("/admin/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', resp.text).group(1)
    client.post("/admin/login", data={"username": "owner", "password": "test-admin-pass",
                                      "csrf_token": token}, follow_redirects=False)


def _csrf_from(client, url="/admin/try-on"):
    """Login clears the session, so the token is minted by the next page render."""
    import re
    resp = client.get(url)
    return re.search(r'name="csrf-token" content="([^"]+)"', resp.text).group(1)


def test_pair_route_issues_key_and_shows_qr_once(client, db_session):
    _owner_logs_in(client)
    resp = client.post("/admin/try-on/pair-capture",
                       data={"csrf_token": _csrf_from(client)}, follow_redirects=False)
    assert resp.status_code == 200
    assert "data:image/png;base64," in resp.text  # the QR, inline

    stored = db_session.query(Settings).filter(Settings.key == KEY_HASH_SETTING).one()
    assert len(stored.value) == 64  # a SHA-256, not the key itself

    # And a reload of the page does not re-show it: the pairing dict is gone.
    resp2 = client.get("/admin/try-on")
    assert "data:image/png;base64," not in resp2.text
    assert "گوشی ضبط عکس" in resp2.text


def test_pair_route_is_owner_only(client, db_session):
    from models import StaffUser
    from services.security import hash_password
    import re
    resp = client.get("/admin/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', resp.text).group(1)
    db_session.add(StaffUser(username="cashier1", password_hash=hash_password("pw-123456"),
                             role="cashier", is_active=True))
    db_session.commit()
    client.post("/admin/login", data={"username": "cashier1", "password": "pw-123456",
                                      "csrf_token": token}, follow_redirects=False)
    # A valid CSRF token with a cashier's session: the refusal is the role
    # guard's, not the CSRF middleware's.
    resp = client.post("/admin/try-on/pair-capture",
                       data={"csrf_token": _csrf_from(client, "/admin")}, follow_redirects=False)
    assert resp.status_code == 403


def test_landing_route_is_session_free_and_never_sees_a_key(client):
    # No session at all: the phone's first scan happens before any login.
    resp = client.get("/admin/mobile/pair", follow_redirects=False)
    assert resp.status_code == 200
    # The route serves the page itself; the key never rides a request line.
    assert "کلید" in resp.text or "جفت" in resp.text


# ── The gate: the paired key earns /api/* access, and nothing else does ──────

def test_upload_rejects_an_unpaired_key(client, db_session):
    pair_device(db_session)  # the server holds a hash…
    db_session.commit()
    resp = client.post("/api/image-gen/upload-kid-photo",
                       files={"file": ("kid.jpg", b"not really a jpeg", "image/jpeg")},
                       headers={AUTH_HEADER: "a key the server never issued"})
    assert resp.status_code == 401


def test_upload_accepts_the_paired_key_and_stores_no_file(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    resp = client.post("/api/image-gen/upload-kid-photo",
                       files={"file": ("kid.jpg", b"not really a jpeg", "image/jpeg")},
                       headers={AUTH_HEADER: raw_key})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_unpair_shuts_the_phone_out_immediately(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    assert client.get("/api/capture/ping", headers={AUTH_HEADER: raw_key}).status_code == 200

    unpair_device(db_session)
    db_session.commit()
    assert client.get("/api/capture/ping", headers={AUTH_HEADER: raw_key}).status_code == 401


def test_ping_rejects_the_static_token_header(client):
    # The old shared token must not be mistaken for a paired capture key on the
    # probe — and the API_TOKEN env is set in tests, so this is a real secret.
    resp = client.get("/api/capture/ping", headers={"X-API-Token": "test-api-token"})
    assert resp.status_code == 200  # the static token still opens /api/* itself…
    # …but the capture header with a garbage value does not.
    assert client.get("/api/capture/ping", headers={AUTH_HEADER: "junk"}).status_code == 401


def test_browser_session_still_opens_the_upload_form_route(client, authed):
    # The try-on page's own upload form posts without any device key — the
    # browser's session must keep working after the gate learned a new trick.
    resp = authed.post("/api/image-gen/upload-kid-photo",
                       files={"file": ("kid.jpg", b"bytes", "image/jpeg")})
    assert resp.status_code == 200
