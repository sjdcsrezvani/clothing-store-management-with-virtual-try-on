"""HTMX phase 5 — the phone capture app: pair screen, connection strip and
the photo upload all speak htmx. These pages load no shell — no sidebar, no
base scripts — so they own a live `__captureKey` getter htmx reads as the
X-Capture-Key header, and the server answers with fragments, not JSON, when
HX is calling."""
from tests.test_capture_pairing import AUTH_HEADER, pair_device


def test_pair_page_asks_htmx_to_probe(client, db_session):
    page = client.get("/admin/mobile/pair").text
    assert 'hx-get="/api/capture/ping"' in page
    assert '"X-Capture-Key"' in page  # htmx reads the live getter, not a copy
    assert "htmx.min.js" in page
    # The transport is htmx; nothing here speaks fetch.
    assert "fetch(" not in page and "XMLHttpRequest" not in page


def test_ping_htx_answers_the_status_strip(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    resp = client.get("/api/capture/ping",
                      headers={"HX-Request": "true", AUTH_HEADER: raw_key})
    body = resp.text.lstrip()
    assert body.startswith('<div class="status-bar" id="status-area"')
    assert 'class="status-dot online"' in body          # the dot lights
    # The one-line message rides out of band; the strip itself re-arms.
    assert 'id="status-line"' in body and 'hx-swap-oob="outerHTML"' in body


def test_ping_json_contract_unchanged_for_other_callers(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    data = client.get("/api/capture/ping", headers={AUTH_HEADER: raw_key}).json()
    assert data == {"status": "ok"}


def test_capture_page_uploads_through_htmx(client, db_session):
    client.get("/admin/mobile")  # any guard shape the route keeps — page follows
    page = client.get("/admin/mobile").text
    assert 'hx-post="/api/image-gen/upload-kid-photo"' in page
    assert 'hx-encoding="multipart/form-data"' in page
    assert "htmx.min.js" in page
    assert "fetch(" not in page and "XMLHttpRequest" not in page
    # The upload's verdict rides an attribute, the imperative flow reads it.
    assert 'id="send-status"' in page


def test_upload_without_a_valid_key_never_swaps(client, db_session):
    resp = client.post(
        "/api/image-gen/upload-kid-photo",
        files={"file": ("kid.jpg", b"not really a jpeg", "image/jpeg")},
        headers={"HX-Request": "true", AUTH_HEADER: "junk"})
    # The gate raises before the handler sees anything: no fragment, no swap.
    assert resp.status_code == 401


def test_upload_htx_error_text_served_as_fragment(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    resp = client.post(
        "/api/image-gen/upload-kid-photo",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
        headers={"HX-Request": "true", AUTH_HEADER: raw_key})
    body = resp.text.lstrip()
    assert body.startswith('<div class="send-status is-err" id="send-status" data-status="err">')
    assert "فایل باید عکس باشد." in body  # the JSON detail, as copy on screen


def test_upload_htx_success_fragment(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    resp = client.post(
        "/api/image-gen/upload-kid-photo",
        files={"file": ("kid.jpg", b"\xff\xd8\xff\xe0 jpeg bytes", "image/jpeg")},
        headers={"HX-Request": "true", AUTH_HEADER: raw_key})
    body = resp.text.lstrip()
    assert body.startswith('<div class="send-status is-ok" id="send-status" data-status="ok">')
    assert "عکس ارسال شد." in body


def test_upload_json_contract_unchanged(client, db_session):
    raw_key = pair_device(db_session)
    db_session.commit()
    resp = client.post(
        "/api/image-gen/upload-kid-photo",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
        headers={AUTH_HEADER: raw_key})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "فایل باید عکس باشد."
