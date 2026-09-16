import re
from pathlib import Path

from main import app
from services.themes import (
    DEFAULT_THEME_ID,
    THEME_SETTING_KEY,
    THEMES,
    contrast_ratio,
    custom_tokens,
    theme_preview,
)

ROOT = Path(__file__).resolve().parents[1]

# A custom property read without a fallback. `var(--tag-scale, 1)` is not one of
# these: the fallback makes it optional, so only the bare form has to exist.
BARE_VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
DECLARED = re.compile(r"(--[a-z0-9-]+)\s*:")
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _stylesheet() -> str:
    """style.css with its comments removed: the palette is documented at the top
    of the file in exactly the shape of a declaration, and a comment is not one."""
    return COMMENT.sub("", (ROOT / "static" / "css" / "style.css").read_text())


STYLE_CSS = _stylesheet()


def test_theme_catalog_has_ten_complete_presets():
    assert len(THEMES) == 10
    for theme_id, theme in THEMES.items():
        assert theme["name"]
        assert theme["mode"] in {"light", "dark-shell", "dark", "high-contrast"}
        tokens = theme["tokens"]
        for key in ("--bg", "--card", "--ink", "--ink-soft", "--rule", "--candy", "--sky", "--mint", "--sidebar-bg", "--sidebar-text"):
            assert key in tokens, (theme_id, key)


def test_unknown_theme_preview_falls_back_to_default():
    preview = theme_preview("missing-theme")
    assert preview["id"] == DEFAULT_THEME_ID
    assert preview["name"] == THEMES[DEFAULT_THEME_ID]["name"]


def test_custom_brand_derives_accessible_button_text():
    tokens = custom_tokens("#003D66", "#0F766E")
    assert tokens["--candy"] == "#003D66"
    assert tokens["--sky"] == "#0F766E"
    assert tokens["--primary-contrast"] == "#FFFFFF"
    assert contrast_ratio(tokens["--candy"], tokens["--primary-contrast"]) >= 4.5


def test_custom_brand_rejects_invalid_or_low_contrast_colors():
    try:
        custom_tokens("#12", "#0F766E")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid hex color was accepted")

    tokens = custom_tokens("#777777", "#888888")
    assert tokens["--primary-contrast"] == "#000000"
    assert tokens["--secondary-contrast"] == "#000000"


# Values the shell needs before any theme is applied, and that no theme varies:
# the sidebar's width and the minimum touch target. They live in `:root` alone, so
# they are the only properties a theme is allowed to leave out — everything else
# the stylesheet reads bare has to be in all ten.
#
# They are also the only properties a *page* may read without its theme defining
# them, and only while that page loads the stylesheet: `:root` is a rule in
# style.css, so it reaches a document that links it and nothing else. Everything
# else a page reads has to come from its own palette or from the page itself.
# `--button-text` used to sit in this set, and the phone capture tool read it on a
# document that loads no stylesheet, so its buttons printed their labels in the
# inherited ink rather than white. It is a theme token now, in `_BASE`.
GLOBAL_ONLY = {"--sidebar-width", "--touch-target"}


def _root_properties() -> set[str]:
    root = re.search(r":root\s*\{(.*?)\}", STYLE_CSS, re.S)
    assert root, "style.css no longer has a :root block"
    return set(DECLARED.findall(root.group(1)))


def _stylesheet_local_properties() -> set[str]:
    """Properties the stylesheet declares for itself outside `:root` — a section
    that needs one value in two places (the theme gallery's gap)."""
    without_root = re.sub(r":root\s*\{.*?\}", "", STYLE_CSS, count=1, flags=re.S)
    return set(DECLARED.findall(without_root))


def _template_sources() -> list[str]:
    return [COMMENT.sub("", path.read_text()) for path in sorted((ROOT / "templates").rglob("*.html"))]


def _template_properties() -> set[str]:
    """Properties templates declare: their own `:root`, their `<style>` blocks,
    and the ones a page sets inline on an element at render time."""
    found: set[str] = set()
    for source in _template_sources():
        found |= set(DECLARED.findall(source))
    return found


def test_theme_catalog_defines_every_property_the_stylesheet_reads():
    """An undefined custom property does not fall back to a sibling token — the
    declaration is dropped and the element inherits its parent's colour, so the
    page looks deliberate while showing the wrong thing. `--persimmon` went
    missing that way for eleven declarations and nothing failed. Every theme has
    to define every property the stylesheet reads bare.
    """
    exempt = GLOBAL_ONLY | _stylesheet_local_properties()
    reads = {name for name in BARE_VAR.findall(STYLE_CSS) if name not in exempt}
    # The parser has to be finding the reads at all, and persimmon has to be one
    # of them, or this test would pass on an empty set.
    assert "--persimmon" in reads
    assert "--card" in reads and "--ink-soft" in reads
    for theme_id, theme in THEMES.items():
        missing = sorted(reads - set(theme["tokens"]))
        assert not missing, (theme_id, missing)


def test_no_template_reads_a_property_nothing_defines():
    """The same rule for the markup, where a page can also carry its own palette
    or set a property inline (the barcode preview's frame, the gallery's gap)."""
    theme_level = set.intersection(*[set(theme["tokens"]) for theme in THEMES.values()])
    known = theme_level | _root_properties() | _stylesheet_local_properties() | _template_properties()
    unknown: set[str] = set()
    for source in _template_sources():
        unknown |= set(BARE_VAR.findall(source))
    assert not unknown - known, sorted(unknown - known)


HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# Hex literals in a themed page that are not a colour *of that page*, each with
# the reason it is allowed. The test below fails if one of these stops matching,
# so an exemption cannot quietly outlive what it was written for.
NOT_A_COLOUR: dict[str, tuple[tuple[str, str], ...]] = {
    "templates/admin/barcode_print.html": ((
        "background: #fff",
        "the A4 sheet handed to the printer is paper, not a surface: white in every theme",
    ),),
    "templates/admin/product_form.html": ((
        'placeholder="#4AA3DF"',
        "the example text a shop types into a colour-code field, not a colour of the page",
    ),),
    "templates/admin/variant_form.html": ((
        'placeholder="#4AA3DF"',
        "the same example on the single-variant form",
    ),),
    "static/js/app.js": ((
        "#abc",
        "a comment explaining how a short colour code expands",
    ),),
}


def _colour_sources() -> list[tuple[str, str]]:
    """Every file that paints something: the templates and the scripts beside
    them, as (path relative to the project, text)."""
    paths = list((ROOT / "templates").rglob("*.html")) + list((ROOT / "static" / "js").glob("*.js"))
    return [(str(path.relative_to(ROOT)), path.read_text()) for path in sorted(paths)]


def test_no_page_of_the_shell_carries_its_own_colours():
    """A themed page reads its colours from the active theme, so a shop that runs
    the dark or high-contrast palette does not meet a pink chart, a beige panel or
    a white card in a corner of it. Every hex literal is a colour that cannot
    follow the theme, so a page has to say why it needs one — and there is no
    longer a page excused from this: the phone capture tool was the last one, and
    it reads the shop's palette too.
    """
    offenders: list[str] = []
    for relative, source in _colour_sources():
        excused = tuple(needle for needle, _ in NOT_A_COLOUR.get(relative, ()))
        for number, line in enumerate(source.splitlines(), 1):
            if any(needle in line for needle in excused):
                continue
            for match in HEX_COLOUR.findall(line):
                offenders.append(f"{relative}:{number}: {match} — {line.strip()[:90]}")
    assert not offenders, "\n".join(offenders)


def test_the_colour_exemptions_still_excuse_something():
    """An allowlist that has outlived its reason is how a rule quietly dies."""
    for relative, entries in NOT_A_COLOUR.items():
        source = (ROOT / relative).read_text()
        for needle, reason in entries:
            assert reason, (relative, needle)
            assert needle in source, f"{relative} no longer contains {needle!r}"


def test_the_chart_renderer_draws_with_the_active_theme():
    """The offline canvas renderer is a fallback for a page whose chart library
    did not load, and it used to paint a palette of its own — so the shop on the
    dark theme got bars in the daylight colours. It reads the tokens instead.
    """
    script = (ROOT / "static" / "js" / "charts.js").read_text()
    assert "themeTones" in script and "window.themeTones" in script
    for token in ("--candy", "--sky", "--sunshine", "--mint", "--lavender", "--persimmon", "--rule", "--ink-soft"):
        assert token in script, token
    assert "palette = ['#" not in script


def _mix_toward(colour: str, background: str, share: float) -> str:
    """`color-mix(in srgb, colour share, background)` in sRGB, the way Chrome
    resolves the badge tints these tests are about."""
    channels = []
    for index in (1, 3, 5):
        channel = int(colour[index:index + 2], 16) * share + int(background[index:index + 2], 16) * (1 - share)
        channels.append(round(channel))
    return "#%02X%02X%02X" % tuple(channels)


def test_persimmon_is_legible_in_every_theme_on_its_card_and_its_own_badge_tint():
    """Persimmon is the attention colour — money owed, an overdue payment, a
    failed message, text past its limit — so it is read as small text in all
    three roles a theme can be: light surfaces, a dark workspace, and the
    high-contrast palette. The state badges paint it on a 20% tint of itself,
    which is the harder surface of the two, so both are measured.
    """
    assert len(THEMES) == 10
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        assert "--persimmon" in tokens, theme_id
        card = tokens["--card"]
        assert contrast_ratio(tokens["--persimmon"], card) >= 4.5, theme_id
        tint = _mix_toward(tokens["--persimmon"], card, 0.20)
        assert contrast_ratio(tokens["--persimmon"], tint) >= 4.5, (theme_id, tint)


def test_persimmon_is_not_the_brand_colour_in_any_theme():
    """It must not read as a button or a link: an overdue debt painted in
    `--candy` would look clickable, which is why the slot exists."""
    for theme_id in THEMES:
        tokens = theme_preview(theme_id)["tokens"]
        assert tokens["--persimmon"].upper() != tokens["--candy"].upper(), theme_id
        assert tokens["--persimmon"].upper() != tokens["--candy-dark"].upper(), theme_id


def test_the_dark_and_high_contrast_themes_lift_or_deepen_the_attention_hue():
    """A light theme's persimmon on a dark card is muddy; the two themes with
    inverted or extreme surfaces carry their own value, and both were chosen for
    their own background rather than inherited from `_BASE`."""
    base = THEMES[DEFAULT_THEME_ID]["tokens"]["--persimmon"]
    assert THEMES["midnight-operations"]["tokens"]["--persimmon"] != base
    assert THEMES["high-contrast"]["tokens"]["--persimmon"] != base
    dark = THEMES["midnight-operations"]["tokens"]["--persimmon"]
    assert contrast_ratio(dark, THEMES["midnight-operations"]["tokens"]["--bg"]) >= 4.5


def test_owner_appearance_page_is_protected(client, db_session):
    from tests.test_roles import _staff, _session_as

    cashier, password = _staff(db_session, "theme-cashier", "cashier")
    _session_as(client, cashier, password)
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403


def test_shared_shell_and_theme_settings_do_not_render_or_upload_logo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    base = (root / "templates/base.html").read_text()
    settings = (root / "templates/admin/settings.html").read_text()
    assert "logo_path" not in base
    assert "/static/logo.png" not in base
    assert "theme_logo" not in settings
    assert "store_logo_path" not in settings
    assert "multipart/form-data" not in settings



def test_owner_can_persist_theme_and_shared_shell_loads_it(client, db_session):
    from tests.conftest import csrf_token
    from tests.test_roles import _staff, _session_as
    from models import Settings

    owner, password = _staff(db_session, "theme-saver", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/admin/settings/appearance")
    response = client.post(
        "/admin/settings/appearance",
        data={"csrf_token": token, "ui_theme": "midnight-operations", "theme_custom_primary": "#C94B68", "theme_custom_secondary": "#197A8C"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "ui_theme", Settings.value == "midnight-operations").first()
    page = client.get("/admin/settings")
    assert 'data-theme="midnight-operations"' in page.text
    assert 'data-theme-mode="dark"' in page.text


def test_owner_can_load_appearance_gallery(client, db_session):
    from tests.test_roles import _staff, _session_as

    owner, password = _staff(db_session, "theme-owner", "owner")
    _session_as(client, owner, password)
    page = client.get("/admin/settings/appearance")
    assert page.status_code == 200
    assert page.text.count('data-theme-id="') == 10
    assert "ui-theme-input" in page.text


def test_general_settings_links_to_appearance_page(client, db_session):
    from tests.test_roles import _staff, _session_as

    owner, password = _staff(db_session, "theme-settings-link", "owner")
    _session_as(client, owner, password)
    response = client.get("/admin/settings")
    assert response.status_code == 200
    assert "/admin/settings/appearance" in response.text
    assert 'data-theme-id="' not in response.text


# ── the whole shell, every role, every theme ─────────────────────────────────
#
# `--persimmon` was read by eleven declarations and defined by no theme for a
# whole release. The pages that used it looked deliberate while printing an
# overdue debt in the ordinary ink colour, and nothing failed — the bug had to
# be *seen* on a page before anyone could know about it. The tests above close
# that hole by reading the sources: the stylesheet, the templates, the renderer.
# This one closes it on what the shop actually receives — it opens every address
# the shell serves, as every role, with each of the ten themes active, and
# resolves every custom property the rendered page reads against the palette that
# theme supplies. The stylesheet's `:root` is deliberately not allowed to answer
# for a theme: it is the fallback for a page rendered with no theme at all, so if
# it counted as a definition here, nine themes could drop a colour and every page
# would keep rendering a plausible one — which is precisely how the bug survived.
#
# A read nothing defines is not a fallback to a sibling token: the declaration is
# invalid at computed-value time, so the property inherits its parent's value and
# the page renders in a colour nobody chose. That is an invisible failure, which
# is why it is checked as a build step rather than trusted to a designer's eye.
#
# The matrix is real traffic through the real app — one request per address, per
# role, per theme, no sample of the palettes — so it is also where a page that
# crashes for a role, or one that loads without the theme it was asked for, or
# one that answers differently depending on the palette, fails the build.

ROLES = ("cashier", "manager", "owner")

# Addresses that are not pages of the shell, each with the reason it is not one.
# The test fails if one of them starts drawing a page — and if one stops being
# served at all — so an excuse cannot outlive the thing it excuses.
NOT_A_PAGE: dict[str, str] = {
    "/admin/accounting/export": "the سود و زیان export is a CSV for the shop's books program",
    "/admin/backups/download": "the download is the backup file itself",
    "/admin/try-on/download": "the try-on engine answers with its own response",
    "/admin/collections": "a shortcut that redirects to the invoices it collects",
    "/admin/sales": "a shortcut that redirects to the sales history it counts",
    "/admin/setup": "the demo build's first-run wizard, and this is the owner's private build",
    "/sales/api/barcode/{barcode}": "the scanner's lookup, which answers in JSON",
    "/admin/try-on/saved/{img_id}/download": "the saved photo itself, not a page",
}

# Addresses the matrix does not open at all, each with the reason. The till's
# long poll holds the request open for two seconds waiting for a card terminal and
# answers in JSON, so opening it once per role per theme would add a minute to the
# suite and still would not draw a page. The guard still insists the app serves
# it, so a renamed route cannot leave a stale excuse behind.
UNPOLLED: dict[str, str] = {
    "/sales/terminal-status": "the till's long poll: two seconds of waiting, and JSON when it answers",
}

# The one thing a document loads from the shell. `:root` is a rule inside this
# file, so a page that does not link it cannot resolve anything the stylesheet
# declares — and the phone capture tool is deliberately self-contained.
STYLESHEET_LINK = "/static/css/style.css"

# Statuses that carry a document. A refusal and a not-found are pages as well: the
# Persian 403 and 404 the shell serves are drawn like any other page, with the
# sidebar, the breadcrumb and the theme — so they are colour-checked too.
HTML_ANSWERS = (200, 403, 404)

# The one address that needs a different record of a kind the seed already has: an
# invoice that has been finalised has no editable form, the page redirects away
# from it, so this address is pointed at the draft kept beside it.
DRAFT_EDIT = "/admin/purchases/{purchase_id}/edit"


def _served_addresses() -> list[str]:
    """Every GET the shell serves, taken from the app's own route table.

    Read from the schema rather than written out by hand, so a page added
    tomorrow is in the matrix without anyone remembering to add it here. The
    guard below states out loud which pages the walk is expected to find, so a
    walk that stops seeing them fails instead of checking an empty list.
    """
    return sorted(path for path, operations in app.openapi()["paths"].items()
                  if "get" in operations and path.startswith(("/admin", "/sales")))


def _filled_addresses(ids: dict[str, object]) -> list[tuple[str, str]]:
    """``(the address as the route table spells it, the same address with real ids)``."""
    filled, unresolved = [], []
    for template in _served_addresses():
        if template in UNPOLLED:
            continue
        page_ids = dict(ids)
        if template == DRAFT_EDIT:
            page_ids["purchase_id"] = ids["draft_purchase_id"]
        address = re.sub(r"\{(\w+)\}",
                         lambda match: str(page_ids.get(match.group(1), match.group(0))), template)
        (filled if "{" not in address else unresolved).append((template, address))
    assert not unresolved, (
        "the seed has no id for " + ", ".join(address for _template, address in unresolved))
    return filled


def _activate_theme(db, theme_id: str) -> None:
    """Put the shop on one of the ten palettes, the way the appearance page does."""
    from models import Settings

    row = db.query(Settings).filter(Settings.key == THEME_SETTING_KEY).first()
    if row is None:
        db.add(Settings(key=THEME_SETTING_KEY, value=theme_id))
    else:
        row.value = theme_id
    db.commit()


def _page_colours(html: str) -> tuple[set[str], set[str]]:
    """``(the properties a page's own markup reads, the ones it declares)``.

    Only the page's own `style` attributes and `<style>` blocks. The stylesheet is
    a separate matter: it is one file for all ten themes and the tests above
    check it against every one of them. A read that carries a fallback —
    `var(--tag-scale, 1)` — is deliberately not collected, because the fallback
    is exactly what makes a missing definition harmless there.
    """
    markup = COMMENT.sub("", " ".join(
        re.findall(r'style="([^"]*)"', html)
        + re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)))
    return set(BARE_VAR.findall(markup)), set(DECLARED.findall(markup))


def _one_of_everything(db_session, staff: dict) -> dict[str, object]:
    """One record of each kind, so every address can be opened for real.

    The seed is chosen for the states it puts on the page, not for volume, because
    that is where the colours are: a page rendered only in its calm state leaves
    the markup of its alert state unchecked. The invoice line naming the نسیه debt
    is drawn only for an unpaid credit sale, the category bar on سود و زیان only
    when expenses are categorised, the wage only when it was paid on the card. A
    paid invoice and a debt-free shop would have left all of those unrendered, so
    a missing colour would have shipped with the matrix passing — which is exactly
    how `--persimmon` survived a release. Hence: a نسیه past its due date, a debt
    over the customer's limit, a message that failed, a check that is overdue, a
    shift counted short, a wage paid on the card.
    """
    from datetime import datetime, timedelta, timezone

    from models import (Campaign, CashSession, CashSessionEntry, CheckRecord, Customer,
                        Expense, GeneratedImage, Product, ProductVariant, Purchase,
                        PurchaseItem, Sale, SaleItem, SalaryPayment, SmsMessage,
                        SmsTemplate, Supplier)

    now = datetime.now(timezone.utc)
    product = Product(name="کتانی تست", category="کفش")
    supplier = Supplier(name="عمده‌فروشی تست", phone="02112345678")
    customer = Customer(phone="09120000001", first_name="سارا", last_name="تست",
                        referral_code="MATRIX01", tier="gold", credit_limit=1_000_000,
                        total_debt=1_500_000, total_spent=2_480_000, total_purchases=2,
                        total_points=120, birth_month_day="05-12", birth_year=1370,
                        last_purchase_date=now - timedelta(days=45))
    campaign = Campaign(name="کمپین زمستانه", code="MATRIX10", discount_percent=10, is_active=True)
    template = SmsTemplate(
        key="matrix-follow-up", name="پیگیری تست", category="custom",
        body="دلمان برایتان تنگ شده؛ {var1} جان منتظرتان هستیم.",
        variables='[{"token": "var1", "label": "نام مشتری", "sample": "سارا", "field": "first_name"}]',
        trigger_key="purchase", is_active=True)
    db_session.add_all([product, supplier, customer, campaign, template])
    db_session.flush()
    variant = ProductVariant(product_id=product.id, price=980_000, cost_price=420_000,
                             stock_quantity=1, reorder_point=5, size="۳۸", color="مشکی",
                             barcode="MATRIX-BARCODE")
    db_session.add(variant)
    db_session.flush()

    # Two invoices: one نسیه past its due date (the ageing pill, the debt card),
    # one settled on the card (the top sellers, the drawer's own takings).
    credit_sale = Sale(customer_id=customer.id, total_amount=1_500_000, final_amount=1_500_000,
                       payment_method="credit", payment_confirmed=True,
                       credit_due_date=now - timedelta(days=45),
                       created_at=now - timedelta(days=50))
    card_sale = Sale(customer_id=customer.id, total_amount=980_000, final_amount=980_000,
                     payment_method="card", payment_confirmed=True,
                     created_at=now - timedelta(days=2))
    db_session.add_all([credit_sale, card_sale])
    db_session.flush()
    db_session.add_all([
        SaleItem(sale_id=credit_sale.id, product_id=product.id, variant_id=variant.id,
                 quantity=1, unit_price=1_500_000, unit_cost=420_000, total_price=1_500_000),
        SaleItem(sale_id=card_sale.id, product_id=product.id, variant_id=variant.id,
                 quantity=1, unit_price=980_000, unit_cost=420_000, total_price=980_000),
    ])

    purchase = Purchase(supplier_id=supplier.id, total_cost=4_200_000, note="فاکتور تست",
                        purchase_date=now - timedelta(days=10), amount_paid=4_200_000)
    # …and one still being assembled, because a finalised invoice has no editable
    # form: the only way to reach that page is with a draft.
    draft = Purchase(supplier_id=supplier.id, total_cost=1_260_000, note="پیش‌نویس تست",
                     is_draft=True, purchase_date=now - timedelta(days=1))
    db_session.add_all([purchase, draft])
    db_session.flush()
    db_session.add_all([
        PurchaseItem(purchase_id=purchase.id, variant_id=variant.id,
                     product_id=product.id, quantity=5, unit_cost=420_000),
        PurchaseItem(purchase_id=draft.id, variant_id=variant.id,
                     product_id=product.id, quantity=3, unit_cost=420_000),
    ])

    # Two shifts: one closed with a shortage (the history and its statement), one
    # still open (the drawer's own page, and the dashboard's «صندوق باز مانده»).
    closed_shift = CashSession(
        cashier_user_id=staff["cashier"].id, opening_balance=2_000_000,
        opened_at=now - timedelta(days=3), closed_at=now - timedelta(days=3) + timedelta(hours=8),
        expected_closing_balance=3_450_000, counted_closing_balance=3_400_000,
        manager_user_id=staff["manager"].id, variance=-50_000, status="closed")
    open_shift = CashSession(cashier_user_id=staff["cashier"].id, opening_balance=1_500_000,
                             opened_at=now - timedelta(hours=2), status="open")
    db_session.add_all([closed_shift, open_shift])
    db_session.flush()
    db_session.add(CashSessionEntry(cash_session_id=open_shift.id, entry_type="withdrawal",
                                    amount=250_000, reason="واریز به بانک",
                                    operator_user_id=staff["cashier"].id))

    # Money leaving the business in both payment methods and in categories: the
    # breakdown on سود و زیان has bars to draw, and the card-paid wage is the one
    # that must not be taken out of the drawer.
    rent = Expense(amount=800_000, category="اجاره", payment_method="cash")
    internet = Expense(amount=300_000, category="اینترنت", payment_method="card")
    wage = Expense(amount=11_500_000, category="حقوق", payment_method="cash")
    db_session.add_all([rent, internet, wage])
    db_session.flush()
    salary = SalaryPayment(staff_user_id=staff["cashier"].id, period_key="1405-05",
                           gross_amount=12_000_000, deductions=500_000, net_amount=11_500_000,
                           payment_method="cash", operator_user_id=staff["owner"].id,
                           expense_id=wage.id, cash_session_id=open_shift.id,
                           paid_at=now - timedelta(days=1), note="حقوق مرداد")
    db_session.add(salary)

    # A check past its date: the checks page counts it and prints its days late
    # in the attention colour.
    db_session.add(CheckRecord(supplier_id=supplier.id, provider_name="چاپخانه تست",
                               check_number="123456", amount_rials=5_000_000,
                               issue_at=now - timedelta(days=40), due_at=now - timedelta(days=5),
                               operator_user_id=staff["owner"].id, status="issued"))

    # One message that went out and one that failed: the history prints the
    # failure in the attention colour, and both carry the values behind them.
    db_session.add_all([
        SmsMessage(template_id=template.id, template_key=template.key, template_name=template.name,
                   customer_id=customer.id, phone=customer.phone,
                   body="دلمان برایتان تنگ شده؛ سارا جان منتظرتان هستیم.", status="sent",
                   kind="transactional", source="purchase", ref=f"sale:{card_sale.id}",
                   values_json='[{"token": "var1", "label": "نام مشتری", "value": "سارا"}]',
                   sent_at=now - timedelta(days=1)),
        SmsMessage(customer_id=customer.id, phone=customer.phone, body="یادآوری بدهی",
                   status="failed", error="پاسخ درگاه: اعتبار کافی نیست",
                   kind="transactional", source="manual"),
    ])

    image = GeneratedImage(customer_id=customer.id, image_path="generated/matrix.png",
                           prompt_used="پرو تست", created_at=now - timedelta(hours=3))
    db_session.add(image)
    db_session.commit()

    return {
        "customer_id": customer.id, "product_id": product.id, "variant_id": variant.id,
        "purchase_id": purchase.id, "draft_purchase_id": draft.id, "campaign_id": campaign.id,
        "staff_id": staff["cashier"].id, "payment_id": salary.id,
        "template_id": template.id, "session_id": closed_shift.id, "img_id": image.id,
        "sale_id": credit_sale.id, "barcode": variant.barcode,
    }


def test_the_matrix_still_finds_the_pages_it_was_written_for():
    """A test that opens nothing passes for the wrong reason.

    The addresses come from the app's schema, so this states out loud which pages
    the walk is expected to find — and that the schema still spells the
    placeholders the seed supplies, which is what keeps a new parameterised route
    from being quietly dropped from the matrix.
    """
    templates = _served_addresses()
    assert len(templates) >= 60, templates
    for address, reason in UNPOLLED.items():
        assert address in templates, address
        assert reason, address
    for address in ("/admin/", "/admin/cashbox", "/admin/settings/appearance",
                    "/admin/sms/send", "/sales/new", "/admin/mobile",
                    "/sales/invoice/{sale_id}", "/admin/cashbox/sessions/{session_id}"):
        assert address in templates, address
    assert {name for template in templates for name in re.findall(r"\{(\w+)\}", template)}
    for excuse, reason in NOT_A_PAGE.items():
        assert reason, excuse


def test_every_page_the_shell_serves_reads_only_colours_its_theme_defines(client, db_session):
    """Every address, as every role, on each of the ten palettes.

    Nothing is sampled and nothing is inferred: each cell of the matrix is a real
    request to the real page. A page is drawn with the theme that was asked for,
    answers the same way in all ten, and every custom property its own markup
    reads is defined by that palette, by one of the global values no theme varies,
    or by the page itself.
    """
    from tests.test_roles import _session_as, _staff

    accounts = {role: _staff(db_session, f"matrix-{role}", role) for role in ROLES}
    ids = _one_of_everything(db_session, {role: user for role, (user, _password) in accounts.items()})
    addresses = _filled_addresses(ids)

    # What a page may read: the palette the theme itself supplies, and — only if
    # the document actually loads the stylesheet — the values the stylesheet
    # declares for itself. Deliberately *not* the stylesheet's `:root` as a way to
    # answer for a palette: that block is the fallback for a page rendered without
    # a theme, and letting it answer for one is how `--persimmon` went missing from
    # nine themes while every page kept rendering a plausible colour — the light
    # theme's value, on a dark card, chosen by nobody. The phone capture tool is
    # why the link is checked rather than assumed: it is handed a palette and
    # nothing else, so `:root` was never available to it.
    sheet_only = GLOBAL_ONLY | _stylesheet_local_properties()
    palettes = {theme_id: set(theme_preview(theme_id)["tokens"]) for theme_id in THEMES}

    answers: dict[tuple[str, str], tuple[int, bool]] = {}
    offenders: list[str] = []
    rendered = 0
    for role in ROLES:
        user, password = accounts[role]
        _session_as(client, user, password)
        for theme_id in THEMES:
            _activate_theme(db_session, theme_id)
            for template, address in addresses:
                response = client.get(address, follow_redirects=False)
                drawn = (response.status_code in HTML_ANSWERS
                         and "text/html" in response.headers.get("content-type", ""))
                answer = (response.status_code, drawn)
                if response.status_code >= 500:
                    offenders.append(f"{role} {address} answered {response.status_code}")
                if (role, template) in answers and answers[(role, template)] != answer:
                    offenders.append(
                        f"{role} {address} answered {answer} on {theme_id} and "
                        f"{answers[(role, template)]} on another theme")
                answers.setdefault((role, template), answer)
                if not drawn:
                    continue
                rendered += 1
                if f'data-theme="{theme_id}"' not in response.text:
                    offenders.append(f"{role} {address} loaded without the {theme_id} theme")
                reads, declares = _page_colours(response.text)
                allowed = palettes[theme_id]
                if STYLESHEET_LINK in response.text:
                    allowed = allowed | sheet_only
                unknown = reads - allowed - declares
                if unknown:
                    offenders.append(
                        f"{role} {address} on {theme_id} reads {sorted(unknown)}, which "
                        "that palette does not define — so the declaration is dropped and "
                        "the colour is inherited, or a fallback from outside the theme "
                        "answers for it")

    # Coverage, so the matrix cannot shrink quietly: every address was asked for
    # as every role and every answer was the same in all ten themes…
    assert len(answers) == len(addresses) * len(ROLES), (len(answers), len(addresses))
    assert rendered == sum(1 for _key, (_status, drawn) in answers.items() if drawn) * len(THEMES)
    assert rendered > 0

    # …and for the owner every address is a page, or is listed above with the
    # reason it is not one. An excuse that has outlived its address fails too.
    served = {template for _role, template in answers}
    owner_pages = {template for (role, template), (status, drawn) in answers.items()
                   if role == "owner" and status == 200 and drawn}
    unexplained = sorted(served - owner_pages - set(NOT_A_PAGE))
    assert not unexplained, f"these addresses are not pages and nothing says why: {unexplained}"
    stale = sorted(set(NOT_A_PAGE) - (served - owner_pages))
    assert not stale, f"these addresses are excused from being pages and no longer need it: {stale}"

    assert not offenders, "\n".join(offenders[:40])
