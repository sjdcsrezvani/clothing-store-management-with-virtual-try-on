"""Toast stack (Phase B): flash joins the queue instead of the heading.

The redirect contract (?msg=/?err=) is unchanged — only the rendering moved:
page_header renders [data-toast] rows, toast.js adopts them into the single
#toaster. Field-adjacent validation errors stay inline .alert by design.
"""
from pathlib import Path

from tests.conftest import csrf_token

ROOT = Path(__file__).resolve().parents[1]


def _login(client, username="owner", password="test-admin-pass"):
    token = csrf_token(client)
    client.post("/admin/login", data={
        "username": username, "password": password, "csrf_token": token,
    }, follow_redirects=False)


def test_the_shell_mounts_one_toaster():
    """A single #toaster at the document root; a second one would double every toast."""
    base = (ROOT / "templates" / "base.html").read_text()
    assert base.count('id="toaster"') == 1
    assert 'src="/static/js/toast.js' in base


def test_flash_renders_as_data_toast_with_roles():
    """The redirect flash survives with no JS (text in DOM) and carries the
    live-region role each tone needs."""
    header = (ROOT / "templates" / "partials" / "page_header.html").read_text()
    assert 'data-toast' in header
    assert 'role="status"' in header  # confirmations: polite
    assert 'role="alert"' in header  # errors: assertive
    assert 'class="alert alert-' not in header


def test_msg_query_param_becomes_a_toast(client):
    """The server contract is untouched: ?msg= still lands on the page, now as
    a toast row instead of a heading alert."""
    _login(client)
    resp = client.get("/admin/campaigns?msg=سلام-تست")
    assert resp.status_code == 200
    assert 'id="toaster"' in resp.text
    assert 'data-toast' in resp.text
    assert 'سلام-تست' in resp.text


def test_err_query_param_becomes_an_alert_toast(client):
    _login(client)
    resp = client.get("/admin/campaigns?err=خطا-تست")
    assert resp.status_code == 200
    assert 'data-toast-type="error"' in resp.text
    assert 'role="alert"' in resp.text


def test_toast_script_served_with_queue_api(client):
    resp = client.get("/static/js/toast.js")
    assert resp.status_code == 200
    for snippet in ("RaykidToast", "adopt", "visibilitychange", "MAX_VISIBLE",
                    ".t-toast", "is-open", "role"):
        assert snippet in resp.text, snippet


def test_toast_styles_use_only_theme_tokens():
    """The toast shell paints from the palette it is given — no colour of its own."""
    import re
    motion = (ROOT / "static" / "css" / "motion.css").read_text()
    assert ".toaster" in motion
    assert ".toast-item" in motion
    assert ".t-toast" in motion
    assert ".toaster-flash" in motion
    assert "@media (prefers-reduced-motion: reduce)" in motion
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", motion)
    for bad in ("rgba(", "hsla("):
        assert bad not in motion


def test_toaster_hidden_on_paper():
    """Toasts are the screen's reply; the printer gets the record, not the reply."""
    style = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".toaster" in style


def test_field_errors_stay_inline():
    """Form validation next to its field is not a toast: it must not sail away
    on a timer while the cashier is still reading the form."""
    form = (ROOT / "templates" / "admin" / "campaign_form.html").read_text()
    assert 'class="alert alert-error"' in form
    assert "data-toast" not in form


def test_mobile_toaster_is_a_stack():
    """The capture phone keeps its own queue: stacked rows, errors persist,
    same showToast call sites."""
    mobile = (ROOT / "templates" / "mobile" / "index.html").read_text()
    assert 'id="toaster"' in mobile
    assert 'id="toast"' not in mobile
    assert "function showToast(msg, type, ms)" in mobile
    assert "prefers-reduced-motion" in mobile
