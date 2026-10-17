"""HTMX phase 2 — poll islands: the dashboard's live region and the audit
trail's fresh-rows pill, both converted from fetch loops to htmx."""
from tests.test_admin_logs import _log
from tests.test_staff_phase_b import _owner_client


def test_dashboard_lives_in_one_swappable_region(client, db_session):
    _owner_client(client, db_session, name="htmx2-dash")
    page = client.get("/admin").text
    assert 'id="dash-live"' in page
    assert 'hx-get="/admin"' in page
    assert 'hx-trigger="every 5min' in page
    assert 'hx-select="#dash-live"' in page
    # The old keyed patching names are gone; the region swap replaces them all.
    assert "data-poll-value" not in page
    assert "data-poll-greeting" not in page
    assert "data-poll-sub" not in page


def test_logs_pill_polls_as_htmx_fragment_requester(client, db_session):
    _owner_client(client, db_session, name="htmx2-pill")
    page = client.get("/admin/logs").text
    assert 'id="logs-fresh"' in page
    assert 'hx-trigger="every 30s' in page
    assert 'hx-swap="outerHTML"' in page
    # The baselines ride the poll url; the old fetcher is unloaded for good.
    assert "first_action=" in page and "first_event=" in page
    assert "logs_poll.js" not in page


def test_latest_json_contract_unchanged(client, db_session):
    _owner_client(client, db_session, name="htmx2-json")
    data = client.get("/admin/logs/latest").json()
    assert set(data) == {"actions_max", "events_max"}


def test_latest_htx_answers_the_pill_not_a_page(client, db_session):
    _owner_client(client, db_session, name="htmx2-hxpill")
    # Baselines far above any test row: nothing is fresher — hidden pill.
    resp = client.get("/admin/logs/latest?first_action=9999999&first_event=9999999",
                      headers={"HX-Request": "true"})
    body = resp.text.lstrip()
    assert body.startswith('<p class="empty-state" id="logs-fresh" hidden')
    assert 'hx-trigger="every 30s' in body  # the pill re-arms its own poller
    assert "<html" not in body and "card" not in body  # a fragment, not the page


def test_latest_htx_reveals_pill_when_rows_arrived(client, db_session):
    _owner_client(client, db_session, name="htmx2-hxfresh")
    _log(db_session, "logout", "ردیف تازه")

    # Baseline below the new row id: the pill stands, and its link is the
    # very page the user was on, baselines stripped.
    resp = client.get("/admin/logs/latest?first_action=0&first_event=0",
                      headers={"HX-Request": "true"})
    body = resp.text.lstrip()
    assert body.startswith('<p class="empty-state" id="logs-fresh"\n')
    assert 'href="/admin/logs"' in body  # reload is the current view, no junk


def test_pos_pill_probes_once_and_settles(client, db_session):
    import re
    _owner_client(client, db_session, name="htmx4-pospill")
    page = client.get("/admin/pos-reconciliation").text
    assert 'id="pos-terminal-pill"' in page
    assert 'hx-get="/sales/terminal-status"' in page
    # The old inline probe script is gone.
    assert "XMLHttpRequest" not in page and "fetch(" not in page

    # The probe for this pill answers as a plain line: no polling wiring,
    # so a visit asks once and the pill settles.
    m = re.search(r'id="pos-terminal-pill"[^>]*hx-trigger="load"', page)
    assert m
    resp = client.get("/sales/terminal-status",
                      headers={"HX-Request": "true", "HX-Target": "pos-terminal-pill"})
    body = resp.text.lstrip()
    assert body.startswith('<span id="pos-terminal-pill" data-terminal-pill>')
    assert "hx-" not in body  # nothing to re-arm: one probe per visit


def test_settings_test_key_swaps_the_status_line(client, db_session):
    _owner_client(client, db_session, name="htmx4-testkey")
    page = client.get("/admin/settings?tab=messaging").text
    assert 'id="tryon-test-key" hx-post="/admin/tryon/test-key"' in page
    assert '<script>' not in page.split("tryon-test-key")[0].split("{% if active_tab")[0]

    from tests.conftest import csrf_token
    resp = client.post("/admin/tryon/test-key",
                       headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)})
    body = resp.text.lstrip()
    assert 'id="tryon-test-result" role="status"' in body
    assert "<html" not in body  # a status line, not a page
