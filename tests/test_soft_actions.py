"""Soft actions: a POST that lands back on its own page swaps the view in
place instead of reloading the document — same filters, same tab, same scroll.

The contract lives in one module (static/js/soft.js) and every page-owned
confirm dialog hands its accepted form to it. Native pages opt out with
body.native-actions or data-native.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_mounts_the_soft_layer_once_after_the_dialog():
    base = (ROOT / "templates/base.html").read_text()
    assert base.count("/static/js/soft.js") == 1
    assert base.index("/static/js/confirm.js") < base.index("/static/js/soft.js")


def test_soft_module_served_with_the_full_contract(client):
    resp = client.get("/static/js/soft.js")
    assert resp.status_code == 200
    for snippet in (
        "RaykidSoft", "window.addEventListener('submit'", "defaultPrevented",
        "data-list-region", "#app-main", "history.replaceState",
        "data-native", "native-actions", "htmx.ajax", "htmx.swap",
        "keepView", "is-soft-busy",
    ):
        assert snippet in resp.text, snippet


def test_confirm_layer_hands_the_answer_to_the_soft_layer():
    confirm = (ROOT / "static/js/confirm.js").read_text()
    assert "RaykidSoft.submit" in confirm
    assert "record.submitter" in confirm


def test_page_dialog_scripts_do_not_bypass_the_soft_layer():
    guarded = [
        "static/js/checks.js", "static/js/cashbox.js", "static/js/pos.js",
        "static/js/expenses.js", "static/js/bulk_list.js",
        "static/js/settings_tabs.js",
        "templates/admin/campaign_detail.html",
        "templates/admin/variant_form.html",
        "templates/admin/product_form.html",
        "templates/admin/sms.html", "templates/admin/sms_message.html",
        "templates/admin/sms_send.html", "templates/admin/follow-ups.html",
        "templates/admin/tryon.html", "templates/admin/tryon_saved.html",
        "templates/admin/staff.html", "templates/admin/staff_profile.html",
        "templates/admin/backups.html", "templates/sales/invoice.html",
    ]
    for rel in guarded:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "RaykidSoft.submit" in text, rel
        assert "form.submit();" not in text.replace("this.form.submit();", "") \
            .replace("sel.form.submit();", ""), rel


def test_native_opt_outs_are_marked():
    checkout = (ROOT / "templates/sales/checkout.html").read_text()
    assert "native-actions" in checkout
    appearance = (ROOT / "templates/admin/settings_appearance.html").read_text()
    assert "native-actions" in appearance
    backups = (ROOT / "templates/admin/backups.html").read_text()
    restore = backups[backups.index("/admin/backups/restore"):]
    assert "data-native" in restore[:restore.index(">")]


def test_soft_busy_bar_is_motion_safe():
    style = (ROOT / "static/css/style.css").read_text()
    assert "body.is-soft-busy::before" in style
    assert "@keyframes soft-progress" in style
    assert "prefers-reduced-motion" in style
