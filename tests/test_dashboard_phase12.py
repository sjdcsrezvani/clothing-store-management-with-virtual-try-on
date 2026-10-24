"""Phase 12 polish: Persian labels, 390px CSS, zero states, no emoji,
balanced scripts on the dashboard surfaces."""
import re

_EMOJI = re.compile(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]')
_ALLOWED = {"✕", "✓", "−", "+", "•", "▲", "▼", "—"}


def test_no_emoji_on_dashboard_surfaces():
    for path in ("templates/admin/dashboard.html",):
        found = [c for c in set(_EMOJI.findall(
            open(path, encoding="utf-8").read())) if c not in _ALLOWED]
        assert not found, (path, found)


def test_dashboard_css_covers_new_pieces():
    style = open("static/css/style.css", encoding="utf-8").read()
    for cls in (".dash-greeting", ".history-strip", ".history-day",
                ".progress-track", ".progress-fill", ".rank-toggle",
                ".targets-editor", ".ranked-discount"):
        assert cls in style, cls


def test_dashboard_scripts_balance():
    text = open("templates/admin/dashboard.html", encoding="utf-8").read()
    opens = len(re.findall(r"<script(?![a-zA-Z])", text))
    closes = len(re.findall(r"</script\s*>", text))
    assert opens == closes, (opens, closes)
    assert "setInterval" in text  # silent poll ticks


def test_split_sections_named_in_plain_words():
    import services.dashboard as dash
    sections = dict((s[0], s[1:]) for s in dash.SECTIONS)
    assert sections["urgent"][0] == "اقدام فوری"
    assert sections["attention"][0] == "نیازمند توجه"
    urgent = [c["key"] for c in dash.CARDS if c["section"] == "urgent"]
    assert "cash_variance" not in urgent and "pos" in urgent
    assert "sms_failed" in urgent
