"""Values that render, look deliberate and are wrong.

The palette taught this project the shape of the bug: a token nine themes never
defined did not fail, it fell back to something that painted, so every page kept
showing a plausible colour nobody had chosen. These tests take that shape and
hunt it elsewhere — every place where a missing, unreadable or empty input is
quietly answered with something that looks like an answer:

* a name a page was never given prints as nothing at all (the recorder below);
* a range nobody could read is answered with a *different* range — or with the
  whole ledger — unless the page says which range it used;
* a share over an empty base printed a confident «0٪», which is the opposite of
  the truth about a category nobody bought from;
* a cheque whose date could not be read was dated today;
* a setting the app reads but nothing ever writes answers with its default for
  ever, and looks like a preference the shop chose.

Each kind has a guard here, and each guard was falsified before it was trusted.
"""

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from services._common import pct, read_date_window
from services.analytics import (
    DEFAULT_PERIOD,
    UnreadableRange,
    get_date_range,
    period_range,
)
from services.templating import UNDEFINED_NAMES, templates

ROOT = Path(__file__).resolve().parents[1]


# ===== A name that is not there ============================================
# Jinja renders a name nobody supplied as an empty string, so a page whose
# context lost something prints a gap where a figure belongs. `WatchedUndefined`
# behaves exactly like the default — it prints nothing and pages keep working —
# and records the name, which is what these two tests read.


def test_the_recorder_notices_a_name_that_is_not_there():
    """The guard itself, falsified: it has to catch the thing it is looking for.

    A recorder that quietly recorded nothing would let the sweep below pass for
    the wrong reason, so this states that the behaviour is unchanged *and* that
    the name was caught.
    """
    UNDEFINED_NAMES.clear()
    rendered = templates.env.from_string("[{{ nobodys_value }}|{% if nobodys_value %}y{% endif %}]").render()
    assert rendered == "[|]"
    assert UNDEFINED_NAMES == ["nobodys_value"]


def test_no_page_prints_a_name_it_was_never_given(client, db_session):
    """Every address the shell serves, opened as the owner, watched for blanks.

    This is the sweep the palette needed and did not have: it asks what the page
    printed rather than what its sources say, so a context that lost a key — or a
    template that spells one differently — fails the build instead of leaving an
    empty cell on a page that looks finished. The database holds one record of
    each kind, because a page only reads what it has something to read about.
    """
    from tests.test_roles import _session_as, _staff
    from tests.test_themes import _filled_addresses, _one_of_everything, _served_addresses

    accounts = {role: _staff(db_session, f"silent-{role}", role)
                for role in ("owner", "manager", "cashier")}
    ids = _one_of_everything(db_session, {role: user for role, (user, _password) in accounts.items()})
    _session_as(client, *accounts["owner"])

    blanks = []
    opened = 0
    for _template, address in _filled_addresses(ids):
        UNDEFINED_NAMES.clear()
        response = client.get(address, follow_redirects=False)
        if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
            continue
        opened += 1
        if UNDEFINED_NAMES:
            blanks.append(f"{address} printed {sorted(set(UNDEFINED_NAMES))}")
    # An empty sweep would pass for the wrong reason: this has to have opened the
    # pages, not skipped them.
    assert opened >= 50, f"only {opened} pages were opened"
    assert not blanks, "pages printed names nothing supplied:\n" + "\n".join(blanks)
    assert _served_addresses()


# ===== A range nobody could read ===========================================

def test_an_unknown_period_is_answered_with_the_default_range_and_a_sentence():
    window = period_range("banana")
    assert window.period == DEFAULT_PERIOD
    assert "banana" in window.notice
    # The start of a month is a boundary, so it compares exactly; «now» moves
    # between two calls, so the end compares by the day.
    assert window.start == get_date_range(DEFAULT_PERIOD)[0]
    assert window.end.date() == get_date_range(DEFAULT_PERIOD)[1].date()


def test_an_unreadable_range_is_refused_rather_than_answered_with_another():
    """The function itself refuses: answering «banana» with five years of trade
    is a figure that renders and answers a question nobody asked."""
    with pytest.raises(UnreadableRange):
        get_date_range("quarter")
    with pytest.raises(UnreadableRange):
        get_date_range("custom", "banana", "banana")
    # A period that needs dates and has none is refused too, rather than quietly
    # becoming «همه».
    with pytest.raises(UnreadableRange):
        get_date_range("custom", "", "")
    # The refusal is visible to a page as a sentence, not only as an exception.
    window = period_range("custom", "banana", "banana")
    assert window.period == DEFAULT_PERIOD and window.notice


def test_a_readable_range_is_handed_back_untouched():
    """The guard must not be paid for by refusing what the app does support."""
    start, end = get_date_range("custom", "۱۴۰۵/۰۱/۰۱", "۱۴۰۵/۰۱/۳۱")
    assert start.year == 2026 and start.month == 3
    assert end > start
    window = period_range("custom", "۱۴۰۵/۰۱/۰۱", "۱۴۰۵/۰۱/۳۱")
    assert window.notice == "" and window.period == "custom"
    assert get_date_range("all")[0].year == 2020
    assert get_date_range("today")[0].date() == get_date_range("today")[1].date()


def test_a_half_readable_window_is_dropped_whole_and_said_out_loud():
    start, end, notice = read_date_window("banana", "")
    assert (start, end) == (None, None)
    assert "banana" in notice and notice.endswith(".")
    start, end, notice = read_date_window("۱۴۰۵/۰۱/۰۱", "۱۴۰۵/۰۱/۳۱")
    assert start and end and notice == ""


def test_the_pages_that_take_a_period_say_which_range_they_used(client, authed):
    """A page that refuses a range has to say so *and* show the range it used.

    Both halves matter: the sentence without the range leaves the reader unable
    to tell what they are looking at, and the range without the sentence is the
    silent version of the same defect — including the filter's own select, which
    with a value matching no option displays its first one instead.
    """
    analytics = client.get("/admin/analytics?period=banana").text
    assert "شناخته نشد" in analytics
    assert 'href="/admin/analytics?period=month" class="btn btn-primary' in analytics

    accounting = client.get("/admin/accounting?period=banana").text
    assert "شناخته نشد" in accounting
    assert '<option value="month" selected>' in accounting

    cashbox = client.get("/admin/cashbox?period=quarter").text
    assert "شناخته نشد" in cashbox
    assert '<option value="month" selected>' in cashbox

    # An unreadable custom range is the same story, with the dates left on screen
    # for the reader to correct.
    custom = client.get("/admin/analytics?period=custom&start_date=banana&end_date=banana").text
    assert "خوانده نشد" in custom and 'value="banana"' in custom


def test_a_statement_with_an_unreadable_date_says_the_range_was_not_applied(client, db_session):
    from tests.test_roles import _session_as, _staff
    from tests.test_themes import _one_of_everything

    accounts = {role: _staff(db_session, f"silent-statement-{role}", role)
                for role in ("owner", "manager", "cashier")}
    ids = _one_of_everything(db_session, {role: user for role, (user, _password) in accounts.items()})
    _session_as(client, *accounts["owner"])

    statement = client.get(
        f"/admin/credit/{ids['customer_id']}/statement?start_date=banana&end_date=banana").text
    assert "خوانده نشد" in statement
    # …and the field still shows what was typed, so the correction is obvious.
    assert 'value="banana"' in statement
    # The range line is what leaves the shop on paper, where the notice above it
    # is not printed. It must not restate the unreadable dates as the window.
    assert "بازه: banana" not in statement
    assert "بازه اعمال نشد" in statement


def test_an_export_with_a_range_it_cannot_read_refuses_instead_of_widening(client, authed):
    """The export writes a file the shop keeps; a file of the wrong period is
    worse than no file, because nothing about it looks wrong afterwards."""
    from urllib.parse import unquote

    refused = client.get("/admin/accounting/export?kind=sales&start_date=banana&end_date=banana",
                         follow_redirects=False)
    assert refused.status_code == 303
    assert "خوانده نشد" in unquote(refused.headers["location"])

    # One bound and an empty field is not «everything» either — it is half a
    # window, and it used to read as the whole ledger.
    half = client.get("/admin/accounting/export?kind=sales&start_date=۱۴۰۵/۰۱/۰۱&end_date=",
                      follow_redirects=False)
    assert half.status_code == 303
    assert "با هم وارد کنید" in unquote(half.headers["location"])

    # A range that reads is still exported, so the guard is not a wall.
    good = client.get("/admin/accounting/export?kind=sales&start_date=۱۴۰۵/۰۱/۰۱&end_date=۱۴۰۵/۰۲/۰۱",
                      follow_redirects=False)
    assert good.status_code == 200 and "text/csv" in good.headers.get("content-type", "")


def test_a_statement_that_applied_a_range_still_prints_that_range(client, db_session):
    """The guard must not be paid for by dropping the range off every printout."""
    from tests.test_roles import _session_as, _staff
    from tests.test_themes import _one_of_everything

    accounts = {role: _staff(db_session, f"silent-print-{role}", role)
                for role in ("owner", "manager", "cashier")}
    ids = _one_of_everything(db_session, {role: user for role, (user, _password) in accounts.items()})
    _session_as(client, *accounts["owner"])

    statement = client.get(
        f"/admin/credit/{ids['customer_id']}/statement"
        "?start_date=۱۴۰۵/۰۱/۰۱&end_date=۱۴۰۵/۰۳/۳۱").text
    assert "بازه: ۱۴۰۵/۰۱/۰۱ تا ۱۴۰۵/۰۳/۳۱" in statement
    assert "خوانده نشد" not in statement


# ===== A share of nothing ==================================================

def test_a_share_with_no_base_is_a_dash_not_a_zero():
    assert pct(120, 0) == "—"
    assert pct(0, 0) == "—"
    assert pct(None, None) == "—"
    assert pct(30, 40) == "75٪"
    assert pct(1, 3) == "33.3٪"


def test_no_template_writes_a_share_of_an_empty_base_by_hand():
    """The arithmetic written out in a template is what prints a confident zero.

    ``{{ a / b * 100 if b else 0 }}٪`` renders, looks deliberate and states the
    opposite of the truth; `pct(a, b)` is the same figure with a dash where there
    is nothing to divide.
    """
    pattern = re.compile(r"\{\{[^}]*\belse\s+0\b[^}]*\}\}\s*[٪%]")
    offenders = []
    for path in sorted((ROOT / "templates").rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # A width inside a `style` attribute is a dimension, not a figure:
            # nobody reads «0% wide», and a share bar with nothing to show draws
            # nothing at all. What the rule is after is the printed percentage.
            printed = re.sub(r'style="[^"]*"', "", line)
            if pattern.search(printed):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    assert not offenders, ("a share with a stand-in zero; use pct(part, whole):\n"
                           + "\n".join(offenders))


# ===== A field that fills itself in with the renderer's word ================
# A form field is drawn for a record as well as for a new one, and the idiom that
# handles that answers the missing-*record* case: `{{ product.brand if product
# else '' }}`. It says nothing about a column nobody filled, which is NULL and
# prints as «None» — into the value of an input, or into the body of a textarea,
# where it reads as a field the shop filled in. Saving the form then writes the
# word into the database. Every product on the shop's shelf carried a brand of
# «None» the first time someone opened it and pressed save.
FIELD_EXPR = re.compile(r"\{\{\s*([a-z_]+)\.([a-z_]+)\s+if\s+(.+?)\s+else\s+.+?\}\}")

# Expressions that guard the record and not the column, which are fine because
# the column cannot be null. Anything else has to guard its own attribute.
COLUMN_IS_NEVER_NULL = {
    ("customer", "phone"): "a customer row without a phone cannot exist: the column is NOT NULL",
}


def test_a_field_that_guards_its_record_also_guards_its_column():
    """The fallback has to cover the value, not only the missing row."""
    offenders, excused = [], set()
    for path in sorted((ROOT / "templates").rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for record, attribute, condition in FIELD_EXPR.findall(line):
                if re.search(rf"\b{attribute}\b", condition):
                    continue
                if (record, attribute) in COLUMN_IS_NEVER_NULL:
                    excused.add((record, attribute))
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{number}: "
                                 f"{record}.{attribute} — the fallback covers a missing "
                                 "record, not a column nobody filled: use `or ''`")
    assert not offenders, "\n".join(offenders)
    # An excuse that stops being needed fails, the same as every other list here.
    assert excused == set(COLUMN_IS_NEVER_NULL), (
        "these excuses are no longer used: "
        + ", ".join(sorted(".".join(pair) for pair in set(COLUMN_IS_NEVER_NULL) - excused)))


# ===== A share of nothing, worked out in Python =============================
# The template rule above refuses the arithmetic written out by hand; the same
# figure is worked out one layer down, in the services. `round(p / r * 100) if r
# else 0` answers an empty base with a measured-looking nought, and every page
# that shows it prints «0٪» — the margin of a morning with no sales, the response
# rate of a campaign that reached nobody, the repeat rate of a shop with no
# customers. `share()` answers `None` instead, and `percent()` says «—» for it.
BY_HAND = re.compile(r"\* ?100\b")
FALLBACK = re.compile(r"\bif\b.*\belse\b")

# The one place a nought is the right answer: a heatmap cell's tint, which nobody
# reads as a figure — an empty cell draws at zero opacity — and which is measured
# against the matrix's own maximum.
NOUGHT_IS_RIGHT = {
    "services/analytics.py": "the heatmap cell's tint, which an empty cell draws at nought",
    # …and the helper's own docstring, which quotes the shape it replaces.
    "services/_common.py": "share()'s docstring, quoting the shape this rule refuses",
}


# ===== A figure given a stand-in nought =====================================
# `x or 0` and `x|default(0)` are how a template avoids printing `None`, and they
# trade one wrong thing for another: the page states a measured nought. A customer
# whose spending counter was never computed read as one who had never bought
# anything, on the list that the shop uses to decide who to call; an item whose
# cost was never entered read as one that cost nothing, on the ledger the shop
# uses to value its stock. `figure(x)` prints the number, or «—» when there is no
# number, which is what the same page already does for a debt it has none of.
STAND_IN_ZERO = re.compile(r"\b([a-z_]+)\.([a-z_]+)\s*(?:\|\s*default\(\s*0\s*\)|\bor\s+0\b)")
EXPRESSION = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
OPEN_TAG = re.compile(r"<[^>]*$")
SCRIPT_OPEN = re.compile(r"<(script|style)\b", re.I)
SCRIPT_CLOSE = re.compile(r"</(script|style)>", re.I)

# Columns whose empty means the same as nought — nothing owed against this
# invoice, no points earned on it, no referral, no recorded demand — so the nought
# is the figure the shop would act on anyway. Everything else that can be empty
# belongs to `figure()`. A column that cannot be empty needs no mention: the
# fallback there is dead code rather than a claim.
NULL_IS_NOUGHT = {
    "points_earned": "an invoice that earned no points earned none",
    "referred_discount": "a customer nobody referred has no referral discount",
    "referrer_discount": "a referrer who has introduced nobody has no discount for it",
    "monthly_referral_count": "a customer who introduced nobody this month introduced nobody",
    "demand_count": "an item nobody has asked for has no recorded demand",
}


def _nullable_columns() -> dict[str, bool]:
    """Column → can it be empty, straight out of the models."""
    from models import Base

    nullable: dict[str, bool] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            nullable.setdefault(column.name, bool(column.nullable))
    return nullable


def test_no_figure_is_given_a_stand_in_nought_for_a_column_that_can_be_empty():
    """A printed figure with a nought for an empty column is a claim, not a blank."""
    nullable = _nullable_columns()
    offenders, excused = [], set()
    for path in sorted((ROOT / "templates").rglob("*.html")):
        in_script = 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SCRIPT_CLOSE.search(line):
                in_script = 0
            open_here = in_script == 0 and bool(SCRIPT_OPEN.search(line))
            if open_here:
                in_script += 1
                continue
            if in_script:
                continue
            for match in EXPRESSION.finditer(line):
                expression = match.group(1)
                # A `value=` or a `data-` attribute is form state or a payload:
                # the shop can change a form field, and a script's data is read by
                # the page's own code, not by the owner.
                if OPEN_TAG.search(line[:match.start()]):
                    continue
                for _record, column in STAND_IN_ZERO.findall(expression):
                    if not nullable.get(column, True):
                        continue
                    if column in NULL_IS_NOUGHT:
                        excused.add(column)
                        continue
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{number}: {expression[:80]} — "
                        f"`{column}` can be empty, so the page states a nought for a figure "
                        "the shop never recorded: use figure(...)")
    assert not offenders, "\n".join(offenders)
    assert excused == set(NULL_IS_NOUGHT), (
        "these excuses are no longer used: " + ", ".join(sorted(set(NULL_IS_NOUGHT) - excused)))


def test_no_percentage_is_worked_out_by_hand_in_the_services():
    """A percentage is `share()` or `pct()`; nothing else knows about empty bases."""
    offenders, excused = [], set()
    for folder in ("services", "routers"):
        for path in sorted((ROOT / folder).glob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#")[0].strip()
                if not (BY_HAND.search(code) and FALLBACK.search(code)):
                    continue
                if relative in NOUGHT_IS_RIGHT:
                    excused.add(relative)
                    continue
                offenders.append(f"{relative}:{number}: {code[:90]}")
    assert not offenders, (
        "a share with a stand-in zero, worked out by hand — use share(part, whole) "
        "and print it with percent():\n" + "\n".join(offenders))
    assert excused == set(NOUGHT_IS_RIGHT), (
        "these excuses are no longer needed: " + ", ".join(sorted(set(NOUGHT_IS_RIGHT) - excused)))


def test_a_share_of_nothing_is_a_dash_on_the_pages_that_show_it(client, db_session):
    """The figure and the page agree: `share` says nothing, `percent` says «—».

    This is the shop's first page on the morning before its first sale, where
    «حاشیه 0٪» used to sit under a zero revenue, reading as a margin the owner had
    measured rather than a margin that does not exist yet.
    """
    from services._common import pct, percent, share

    assert share(30, 40) == 75 and share(1, 3) == 33.3
    assert share(10, 0) is None and share(0, 0) is None and share(None, None) is None
    assert pct(30, 40) == "75٪" and pct(10, 0) == "—"
    assert percent(75.0, 0) == "75٪" and percent(None) == "—"

    from tests.test_roles import _session_as, _staff

    _session_as(client, *_staff(db_session, "silent-share", "owner"))
    dashboard = client.get("/admin/").text
    assert "حاشیه —" in dashboard, "the dashboard still states a margin for a day with no sales"
    assert "حاشیه 0٪" not in dashboard

    analytics = client.get("/admin/analytics").text
    assert re.search(r"حاشیه سود.*?</h4><p class=\"kpi-value\">—</p>", analytics, re.S), (
        "the margin card states a figure for a period with no sales")
    assert re.search(r"نرخ بازگشت.*?</h4><p class=\"kpi-value\">—</p>", analytics, re.S), (
        "the repeat-rate card states a rate for a shop with no customers")
    assert ">0٪</p>" not in analytics, "a share of nothing is still printed as nought"


def test_a_customer_whose_counters_were_never_computed_shows_a_dash(client, db_session):
    """The reachable case: a customer row the counters were never written for.

    The list no longer reads the counters, so a NULL counter cannot reach the
    page at all — what it prints comes from the invoices. Two cases: a customer
    whose counters are NULL but who *has* an invoice must print the invoice's
    figures (the old page had no honest answer for that row); and a customer
    with no invoices prints the invoices' own zero — arithmetic over an empty
    set, not a stand-in for a figure nobody computed.
    """
    from models import Customer, Sale

    from tests.test_roles import _session_as, _staff

    _session_as(client, *_staff(db_session, "silent-counter", "owner"))
    db_session.add(Customer(phone="09120000077", first_name="بی‌", last_name="شمارنده",
                            referral_code="SILENT-NULL"))
    db_session.commit()
    # Written as an update on purpose: a column default answers an INSERT that
    # passes None, and only a row that predates the counter holds the NULL that
    # the page has to survive.
    db_session.query(Customer).update({Customer.total_spent: None,
                                       Customer.total_purchases: None,
                                       Customer.total_points: None,
                                       Customer.total_debt: None})
    db_session.commit()

    customer = db_session.query(Customer).filter(
        Customer.referral_code == "SILENT-NULL").one()
    db_session.add(Sale(customer_id=customer.id, total_amount=850_000,
                        final_amount=850_000, payment_method="cash",
                        payment_confirmed=True))
    db_session.commit()

    html = client.get("/admin/customers").text
    # The customer's own row, not the header row above it.
    at = html.index("شمارنده")
    start = html.rindex("<tr>", 0, at)
    while "<th" in html[start:at]:
        start = html.rindex("<tr>", 0, start)
    row = html[start:html.index("</tr>", at)]
    assert "850,000" in row, "the invoice behind NULL counters was not printed"
    assert "1 خرید" in row, "the invoice count behind NULL counters was not printed"

    # A customer with no invoices prints the invoices' zero — real arithmetic
    # over an empty set, not the counter stand-in the old page could not tell
    # from a computed nought.
    db_session.add(Customer(phone="09120000078", first_name="بی‌", last_name="بی‌فاکتور",
                            referral_code="SILENT-NO-SALE"))
    db_session.commit()
    html = client.get("/admin/customers").text
    at = html.index("بی‌فاکتور")
    start = html.rindex("<tr>", 0, at)
    while "<th" in html[start:at]:
        start = html.rindex("<tr>", 0, start)
    row = html[start:html.index("</tr>", at)]
    assert "0 خرید" in row, "an empty set of invoices is a computed zero, not a blank"


def test_a_category_with_no_revenue_shows_a_dash_in_the_margin_column(client, db_session):
    """The reachable case: a category whose only sales in the period were free.

    Its margin is not «0٪» — nobody bought anything, so there is no margin to
    state — and the column has to say so.
    """
    from datetime import datetime, timezone

    from models import Product, ProductVariant, Sale, SaleItem

    from tests.test_roles import _session_as, _staff

    owner, password = _staff(db_session, "silent-margin", "owner")
    _session_as(client, owner, password)
    now = datetime.now(timezone.utc)

    given_away, sold = Product(name="هدیه تست", category="هدیه"), Product(name="فروش تست", category="فروش")
    db_session.add_all([given_away, sold])
    db_session.flush()
    free_variant = ProductVariant(product_id=given_away.id, price=0, cost_price=90_000,
                                  stock_quantity=1, size="۲", color="مشکی", barcode="SILENT-FREE")
    paid_variant = ProductVariant(product_id=sold.id, price=500_000, cost_price=200_000,
                                  stock_quantity=1, size="۳", color="سفید", barcode="SILENT-PAID")
    db_session.add_all([free_variant, paid_variant])
    db_session.flush()
    free_sale = Sale(total_amount=0, final_amount=0, payment_method="cash",
                     payment_confirmed=True, created_at=now)
    paid_sale = Sale(total_amount=500_000, final_amount=500_000, payment_method="cash",
                     payment_confirmed=True, created_at=now)
    db_session.add_all([free_sale, paid_sale])
    db_session.flush()
    db_session.add_all([
        SaleItem(sale_id=free_sale.id, product_id=given_away.id, variant_id=free_variant.id,
                 quantity=1, unit_price=0, unit_cost=90_000, total_price=0),
        SaleItem(sale_id=paid_sale.id, product_id=sold.id, variant_id=paid_variant.id,
                 quantity=1, unit_price=500_000, unit_cost=200_000, total_price=500_000),
    ])
    db_session.commit()

    html = client.get("/admin/analytics?period=all").text
    free_row = re.search(r"<tr><td>هدیه</td>.*?</tr>", html, re.S)
    paid_row = re.search(r"<tr><td>فروش</td>.*?</tr>", html, re.S)
    assert free_row and paid_row, "the categories table did not render both rows"
    assert "<td class=\"tnum\">—</td>" in free_row.group(0)
    assert "٪</td>" in paid_row.group(0)


# ===== A date that becomes today ===========================================

def test_a_check_with_an_unreadable_issue_date_is_refused(client, db_session, authed):
    from models import CheckRecord

    from tests.conftest import csrf_token

    form = {"provider_name": "چاپخانه تست", "supplier_id": "",
            "amount_rials": "1000000", "bank_name": "بانک تست",
            "account_reference": "", "reminder_days": "", "note": "",
            "due_date": "2027/02/01"}

    response = client.post("/admin/checks/add", data={
        **form, "csrf_token": csrf_token(client, "/admin/checks"),
        "check_number": "900001", "issue_date": "banana",
    }, follow_redirects=False)
    assert response.status_code == 303
    refusal = unquote(response.headers["location"])
    assert "err=" in refusal and "تاریخ صدور چک" in refusal
    assert db_session.query(CheckRecord).count() == 0

    # A blank issue date still means «issued now» — the refusal is for a date
    # that was typed and not understood, not for leaving the field out.
    response = client.post("/admin/checks/add", data={
        **form, "csrf_token": csrf_token(client, "/admin/checks"),
        "check_number": "900002", "issue_date": "",
    }, follow_redirects=False)
    assert response.status_code == 303, unquote(response.headers.get("location", ""))
    assert db_session.query(CheckRecord).count() == 1, unquote(response.headers.get("location", ""))


def test_no_parser_result_is_stood_in_for_by_a_timestamp():
    """The pattern that dated a cheque today: a parse that failed, `or`, a value.

    A reader that cannot read what the shop typed must hand back nothing and let
    the route refuse. Anywhere a fallback is written this way, the record keeps a
    date nobody chose.
    """
    pattern = re.compile(r"parse_[a-z_]+\s*\([^)]*\)\s*or\s")
    offenders = []
    for folder in ("routers", "services"):
        for path in sorted((ROOT / folder).glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")
    assert not offenders, ("a failed read is being replaced by a substitute value:\n"
                           + "\n".join(offenders))


# ===== A setting nothing writes ============================================

SETTINGS_READ = re.compile(r'get_setting_(?:int|bool|str|value)\(\s*[^,]+,\s*([A-Za-z_][A-Za-z0-9_]*|"[a-z0-9_]+")')
SETTINGS_WRITTEN = re.compile(r'Settings\(key=("?[A-Za-z_][A-Za-z0-9_]*"?)\s*,')
CONSTANT = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([a-z0-9_]+)"', re.M)


def _python_sources(folder: str) -> list[Path]:
    return sorted(path for path in (ROOT / folder).glob("*.py"))


def _setting_keys_read() -> set[str]:
    """Every key the app asks for, with module constants resolved to their value."""
    keys: set[str] = set()
    for folder in ("services", "routers"):
        for path in _python_sources(folder):
            source = path.read_text(encoding="utf-8")
            constants = dict(CONSTANT.findall(source))
            for name in SETTINGS_READ.findall(source):
                if name.startswith('"'):
                    keys.add(name.strip('"'))
                elif name.lower() != name:
                    # An uppercase name is a module constant and must resolve; a
                    # lowercase one is a parameter, and the literals at its call
                    # sites are what this scan reads.
                    keys.add(constants.get(name, f"<constant {name}>"))
    return keys


def _setting_keys_written() -> set[str]:
    """Every key something writes: a literal write, or a form field name.

    The settings page saves whatever the form posted, so a field name is a write;
    anything else written by code is a literal `Settings(key=...)`. Numeric
    settings fields render through the settings form's num() macro, so their
    names arrive as macro arguments rather than name="..." literals — but the
    macro writes the same input the POST saves, so it counts as the write path.
    """
    keys: set[str] = set()
    for path in _python_sources("services") + _python_sources("routers"):
        for name in SETTINGS_WRITTEN.findall(path.read_text(encoding="utf-8")):
            keys.add(name.strip('"'))
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        keys |= set(re.findall(r'name="([a-z0-9_]+)"', text))
        keys |= set(re.findall(r"num\('([a-z0-9_]+)'", text))
    return keys


def test_every_setting_the_app_reads_is_one_something_writes():
    """A key nothing writes is a preference the shop can never change.

    The read answers with its default, the page shows that default as if it were
    the shop's own choice, and nothing anywhere fails — the same silence as a
    token nobody defined, one layer up from the CSS.
    """
    read = _setting_keys_read()
    written = _setting_keys_written()
    assert len(read) >= 15, read          # the scan is finding the reads
    assert "tier_gold_threshold" in read  # …and this one is among them
    unknown = sorted(key for key in read - written if not key.startswith("<constant"))
    unresolved = sorted(key for key in read if key.startswith("<constant"))
    assert not unknown, ("these settings are read and nothing writes them:\n"
                         + "\n".join(unknown))
    assert not unresolved, ("a setting read through a constant this test cannot resolve:\n"
                            + "\n".join(unresolved))
