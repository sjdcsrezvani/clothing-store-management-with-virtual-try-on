"""Every asset the shell draws must come from disk, not the internet.

The login page is the one screen a shop sees before it has a working network,
so a render-blocking CDN request is the difference between "instant" and "a
minute of white tab".
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "templates").rglob("*.html"))
EXTERNAL_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
)
# The bytes Google Fonts served on 2026-11-07 for Vazirmatn 33.003. Pinned so
# a different font revision cannot slip in unnoticed.
GOOGLE_FONTS = {
    "Vazirmatn-Arabic.woff2":
        "07c90f3690c05c7722300d9933824191a77f5eb8367e498a07ffc9552f2c2635",
    "Vazirmatn-LatinExt.woff2":
        "a448627c36553c00e67cc488376e13f8f4211f3ddc9d12de22e48a468d306a90",
    "Vazirmatn-Latin.woff2":
        "454d44a1112afe0a95f40216cd231d0fb725269e31c8692ff36003c56eb23d7c",
}


def test_no_template_fetches_an_asset_from_the_internet():
    offenders = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        for host in EXTERNAL_HOSTS:
            if host in text:
                offenders.append(f"{path.relative_to(ROOT)}: {host}")
        if re.search(r'<(?:script|img|iframe)[^>]+src="https?://', text):
            offenders.append(f"{path.relative_to(ROOT)}: remote src")
        if re.search(r'<link[^>]+href="https?://', text):
            offenders.append(f"{path.relative_to(ROOT)}: remote stylesheet")
    assert offenders == []


def test_the_hosted_files_are_the_exact_google_fonts_bytes():
    for name, digest in GOOGLE_FONTS.items():
        path = ROOT / "static/fonts" / name
        assert path.exists(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_the_local_font_sheet_declares_the_three_google_subsets():
    css = (ROOT / "static/css/fonts.css").read_text(encoding="utf-8")
    assert css.count("@font-face") == 3
    assert css.count("font-weight: 100 900;") == 3
    assert css.count("unicode-range:") == 3
    assert "font-display: swap;" in css
    for name in GOOGLE_FONTS:
        assert f"/static/fonts/{name}" in css, name


def test_the_font_file_is_served_from_disk(client):
    resp = client.get("/static/fonts/Vazirmatn-Arabic.woff2")
    assert resp.status_code == 200
    assert resp.content[:4] == b"wOF2"


def test_the_login_and_capture_pages_link_the_local_font_sheet(client):
    for url in ("/admin/login", "/admin/mobile"):
        page = client.get(url).text
        assert "/static/css/fonts.css" in page, url
        assert "fonts.googleapis.com" not in page, url
