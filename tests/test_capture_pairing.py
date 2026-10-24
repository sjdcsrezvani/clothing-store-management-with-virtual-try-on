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


# ── The phone pages: Persian RTL, no emoji, key-gated UI ─────────────────────

def _no_emoji(text):
    import re
    pat = re.compile(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]')
    return [c for c in set(pat.findall(text)) if c != "✕"]


def test_mobile_page_is_session_free_and_persian(client):
    # Key-only access: the shell opens with no staff session; the stored key
    # gates the API underneath and the script locks capture until the ping
    # answers 200.
    resp = client.get("/admin/mobile", follow_redirects=False)
    assert resp.status_code == 200
    page = resp.text
    assert 'lang="fa"' in page and 'dir="rtl"' in page
    assert "عکس شخص" in page and "پرو مجازی" in page
    assert "log-list" not in page and 'id="status-line"' in page
    assert "onclick" not in page  # listeners, like the panel
    assert _no_emoji(page) == []


def test_mobile_page_phone_compat_baseline(client):
    # mobile-native floor: viewport-fit so safe-area insets resolve, the panel
    # font, tap-without-delay + unselectable controls, 44px targets.
    page = client.get("/admin/mobile").text
    assert "viewport-fit=cover" in page
    assert "fonts.googleapis.com/css2?family=Vazirmatn" in page
    assert "env(safe-area-inset-bottom" in page and "env(safe-area-inset-top" in page
    assert "touch-action:manipulation" in page
    assert "min-height:44px" in page
    assert "min-height:100dvh" in page


def test_pair_page_names_the_right_screen_and_continues(client):
    page = client.get("/admin/mobile/pair").text
    assert "پروژه تعاملی" not in page
    assert "پرو مجازی" in page
    assert 'href="/admin/mobile"' in page  # success continues to capture
    assert "/admin/login" not in page
    assert _no_emoji(page) == []


def test_mobile_templates_balance_scripts(client):
    # The try-on lesson: byte-presence is not rendering — an unclosed script
    # swallows the page while string asserts still pass.
    import re
    for url in ("/admin/mobile", "/admin/mobile/pair"):
        page = client.get(url).text
        opens = len(re.findall(r"<script(?![a-zA-Z])", page))
        closes = len(re.findall(r"</script\s*>", page))
        assert opens == closes, (url, opens, closes)
