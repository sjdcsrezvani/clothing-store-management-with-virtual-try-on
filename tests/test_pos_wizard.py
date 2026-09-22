"""Phase 2: the till flows method → terminal → confirm; try-on is a wizard.

The probe contract is untouched (ids, strings, hide behavior) — only the
order, grouping, and emphasis changed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _checkout() -> str:
    return (ROOT / "templates" / "sales" / "checkout.html").read_text()


def test_payment_flows_in_order():
    source = _checkout()
    method = source.index('role="radiogroup"')
    terminal = source.index('id="pay-step-terminal"')
    cash = source.index('id="cash-calculator"')
    confirm = source.index('id="confirm-form"')
    assert method < terminal < cash < confirm


def test_confirm_is_last_largest_and_alone_in_success():
    source = _checkout()
    assert "btn-confirm" in source
    confirm_line = next(line for line in source.splitlines() if 'id="confirm-submit"' in line)
    assert "style=" not in confirm_line
    assert "۱ روش پرداخت" in source
    assert "۲ تأیید مبلغ" in source
    assert "۳ صدور فاکتور" in source


def test_terminal_send_is_a_primary_step_not_a_ghost():
    source = _checkout()
    assert 'id="terminal-submit" class="btn btn-primary btn-block"' in source
    terminal_line = next(line for line in source.splitlines() if 'id="terminal-submit"' in line and "<button" in line)
    assert "btn-ghost" not in terminal_line


def test_probe_contract_survives_the_rebuild():
    """Ids the headless probe reads, strings it matches, behavior it asserts."""
    source = _checkout()
    for pinned in ('id="terminal-form"', 'id="confirm-submit"',
                   "terminalForm.style.display = isCard ? 'block' : 'none'",
                   "نسیه: مبلغ به حساب بدهی مشتری",
                   "از سقف اعتبار رد می‌شوید"):
        assert pinned in source, pinned
    assert 'id="credit-note"' in source
    assert "creditNote.hidden = !isCredit" in source


def test_tryon_is_a_three_step_wizard():
    source = (ROOT / "templates" / "admin" / "tryon.html").read_text()
    assert "۱ عکس کودک" in source
    assert "۲ محصولات و تنظیمات" in source
    assert "۳ · تصویر نهایی" in source
    assert "۱ · عکس کودک" in source
    assert "۲ · اسکن بارکد محصولات" in source
    # Pairing lives inside step 1 now, not as its own card.
    assert source.count("<h3>گوشی ضبط عکس</h3>") == 0
    assert "گوشی ضبط عکس" in source
    assert 'class="tryon-scan-form"' in source
    # No inline select uniforms anymore.
    assert "padding:0.6rem 0.8rem; border:2px solid" not in source
    # The generate contract: same form, same fields.
    for pinned in ('id="gen-form"', 'name="mode"', 'name="category"',
                   'name="pose"', 'name="face"', 'name="background"',
                   'name="barcode_input"'):
        assert pinned in source, pinned


def test_wizard_css_exists():
    style = (ROOT / "static" / "css" / "style.css").read_text()
    for cls in (".pay-step", ".btn-confirm", ".wizard-timeline",
                ".tryon-pairing", ".tryon-scan-form", ".btn-generate"):
        assert cls in style, cls
