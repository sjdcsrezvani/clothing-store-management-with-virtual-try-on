"""The dashboard as data, so the viewer's role decides what exists at all.

The old page was a manager's page full of owner-only buttons: its actions POSTed
to ``owner`` routes, so a manager who clicked one got FastAPI's raw JSON
``{"detail": ...}`` instead of a page. It also printed numbers for a different
shop than the pages it linked to, because it counted rows itself instead of
asking the helper the destination page uses.

Four rules here:

* **A card a role may not see is never rendered *and never computed*.** This
  module returns only the cards the viewer is allowed, already formatted. The
  template has no branch on the viewer's role, so it cannot leak a margin, and
  because each card's number is built only when the card is going to be shown, a
  manager's dashboard never pays for the owner-only work.
* **The count and the page it links to are one number.** Every figure comes from
  the same helper the destination page calls — never from a second query written
  beside it that could drift.
* **The dashboard does not change anything.** It reports and it links. Every
  action it used to carry either turned out to be a duplicate of a job that
  already ran, or belonged on the page that owns the data; both are now there.
* **A number never borrows another period's clothes.** Figures are grouped by
  the span they measure, and each one that has a fair comparison states what it
  is being compared against rather than leaving the reader to guess.

``reconciliation_checks``, ``list_backups``, ``follow_up_plans`` and
``get_revenue_summary`` are deliberately out of reach: they verify files, walk
every sale or render every message, which is right for their own pages and wrong
for a page opened all day. A test says so.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from functools import cached_property

import jdatetime
from sqlalchemy.orm import Session

from models import CashSession
from services._common import fmt, jalali_str, percent
from services.accounting import (
    as_utc, debt_totals, get_cashbox, get_opening_balance, open_cash_session,
)
from services.analytics import get_date_range, get_top_products
from services.backup import latest_backup
from services.checks import check_alert_summary
from services.customers import customer_overview
from services.inventory import stock_alerts
from services.pos_reconciliation import unresolved_transactions
from services.reporting import canonical_report
from services.security import ROLE_LABELS, role_allows
from services.sms import device_status_label
from services.sms_send import sms_queue_snapshot
from services.sms_triggers import follow_up_due_count
from services.tier import downgrade_candidates, tier_up_candidates

TOP_PRODUCT_LIMIT = 5

# How the two shop spans are named when a figure is compared against them.
YESTERDAY_LABEL = "همین بازه دیروز"
LAST_MONTH_LABEL = "همین بازه ماه گذشته"


def _money(amount) -> str:
    return f"{fmt(int(amount or 0))} ت"


def _delta(current, previous, *, label: str, lower_is_better: bool = False) -> dict | None:
    """How a figure moved, or nothing when there is no fair comparison.

    A change measured against zero is not a percentage, and neither is one
    measured against a loss or taken from a period that had none — so all three
    are reported as no comparison rather than as a number nobody could act on.
    The last guard matters most: a month that went into the red against a month
    that only broke even is a sign change, and rendering it as «۲۰۰۰٪ کمتر»
    turns a bad month into a catastrophe that never happened.

    ``lower_is_better`` is the whole reason this returns a verdict instead of
    leaving the colour to the direction of the arrow: spending less moves the
    same way as earning more, and only the card's owner knows which is good news.
    """
    if previous is None or previous <= 0 or current < 0:
        return None
    change = int(round((current - previous) / previous * 100))
    if abs(change) > 999:
        # The base was so small that the ratio is arithmetic rather than
        # information — «۴۰۰۰٪ بیشتر» is a rounding artefact, not a trend.
        return None
    if change == 0:
        return {"text": f"بدون تغییر نسبت به {label}", "good": None}
    good = (change < 0) if lower_is_better else (change > 0)
    word = "بیشتر" if change > 0 else "کمتر"
    return {"text": f"{abs(change)}٪ {word} از {label}", "good": good}


def _previous_jalali_month_start(month_start: datetime) -> datetime:
    """The first day of the month before the one ``month_start`` falls in."""
    first_of_this = jdatetime.date.fromgregorian(date=month_start.date())
    last_of_previous = first_of_this - jdatetime.timedelta(days=1)
    first_of_previous = jdatetime.date(last_of_previous.year, last_of_previous.month, 1)
    return datetime.combine(first_of_previous.togregorian(), time.min, tzinfo=timezone.utc)


class _Numbers:
    """The figures the cards are built from, each computed on first use.

    Laziness is the point rather than a nicety: a card the viewer may not see is
    never built, so a manager's page must not run the owner-only queries — and a
    comparison is only paid for when a card that shows one is actually drawn.
    """

    def __init__(self, db: Session):
        self.db = db

    @cached_property
    def _today_span(self) -> tuple[datetime, datetime]:
        return get_date_range("today")

    @cached_property
    def _month_span(self) -> tuple[datetime, datetime]:
        return get_date_range("month")

    @cached_property
    def today(self) -> dict:
        return canonical_report(self.db, *self._today_span)

    @cached_property
    def yesterday(self) -> dict:
        """The same elapsed span one day earlier — the only fair comparison."""
        start, end = self._today_span
        return canonical_report(self.db, start - timedelta(days=1), end - timedelta(days=1))

    @cached_property
    def month(self) -> dict:
        return canonical_report(self.db, *self._month_span)

    @cached_property
    def previous_month(self) -> dict:
        """The same number of days into the previous month, not the whole of it."""
        start, end = self._month_span
        previous_start = _previous_jalali_month_start(start)
        return canonical_report(self.db, previous_start, previous_start + (end - start))

    @cached_property
    def cash(self) -> dict:
        return get_cashbox(
            self.db, *self._today_span,
            opening_balance=get_opening_balance(self.db),
        )

    @cached_property
    def open_shift(self):
        """The drawer, if somebody is standing at it right now."""
        return open_cash_session(self.db)

    @cached_property
    def last_closed_shift(self):
        """The most recent shift that was counted — where a difference would show."""
        return (self.db.query(CashSession)
                .filter(CashSession.status == "closed", CashSession.variance.isnot(None))
                .order_by(CashSession.closed_at.desc(), CashSession.id.desc())
                .first())

    @cached_property
    def debts(self) -> dict:
        return debt_totals(self.db)

    @cached_property
    def club(self) -> dict:
        return customer_overview(self.db)

    @cached_property
    def checks(self) -> dict:
        return check_alert_summary(self.db)

    @cached_property
    def sms(self) -> dict:
        return sms_queue_snapshot(self.db)

    @cached_property
    def downgrades(self) -> dict:
        return downgrade_candidates(self.db)

    @cached_property
    def top_products(self) -> list[dict]:
        return get_top_products(self.db, *self._month_span, limit=TOP_PRODUCT_LIMIT)


# ── نیازمند توجه ─────────────────────────────────────────────────────────────
# Each of these is a link that used to be decoration: the old page showed four
# destinations with no idea how much was waiting behind them. Every one now
# carries the count, and when the count is zero the card says so in words rather
# than hiding — an empty attention row is good news worth being able to read.
#
# `clear` means «nothing needs attention» and only these cards may carry it. It
# is deliberately not called `zero`: a shop that sold nothing today is a zero,
# and it is not good news, so money cards must never be able to borrow this
# styling by accident.


def _build_stock(numbers: _Numbers, role: str) -> dict:
    counts = stock_alerts(numbers.db)
    low, out = counts["low_count"], counts["out_count"]
    if not low:
        detail = "همه تنوع‌ها موجودند"
    elif out:
        detail = f"{out} تنوع کاملاً تمام شده"
    else:
        detail = "برای سفارش مجدد بررسی کنید"
    return {"value": str(low), "sub": detail, "clear": not low}


def _build_pos_unresolved(numbers: _Numbers, role: str) -> dict:
    count = unresolved_transactions(numbers.db)
    return {
        "value": str(count),
        "sub": "بدون نتیجه قطعی" if count else "همه تعیین تکلیف شده‌اند",
        "clear": not count,
    }


def _build_credit_overdue(numbers: _Numbers, role: str) -> dict:
    debts = numbers.debts
    overdue = debts["overdue_customers"]
    return {
        "value": str(overdue),
        "sub": f"{_money(debts['overdue_amount'])} مانده" if overdue else "بدهی سررسیدشده‌ای نیست",
        "clear": not overdue,
    }


def _build_checks(numbers: _Numbers, role: str) -> dict:
    """The urgent number, and the same axis one step ahead of it.

    The detail stays on the checks' own axis — the next two weeks — rather than
    praising the absence of something: a card whose label reads «چک سررسیدشده»
    should not answer “there is none” while showing a one.
    """
    summary = numbers.checks
    overdue, upcoming = summary["overdue_count"], summary["upcoming_count"]
    detail = (f"{upcoming} چک تا دو هفته آینده" if upcoming
              else "هیچ چکی تا دو هفته آینده سررسید نمی‌شود")
    return {"value": str(overdue), "sub": detail, "clear": not overdue}


def _build_backup(numbers: _Numbers, role: str) -> dict:
    latest = latest_backup()
    if latest is None:
        # Never having backed up is a problem, not a quiet week, so it is not
        # reported as a zero.
        return {"value": "—", "sub": "هنوز هیچ نسخه‌ای ساخته نشده", "clear": False}
    days = (datetime.now() - latest).days
    if days <= 0:
        value, clear = "امروز", True
    elif days == 1:
        value, clear = "دیروز", True
    else:
        value, clear = f"{days} روز پیش", days <= 7
    return {"value": value, "sub": f"آخرین نسخه: {jalali_str(latest)}", "clear": clear}


def _build_sms(numbers: _Numbers, role: str) -> dict:
    """How much is waiting to go out, and whether the phone can send it.

    The value is a number like every other card's — the connection state, which
    is a word, describes *why* the queue looks the way it does, so it belongs in
    the detail line rather than in the slot the reader scans down.
    """
    queue = numbers.sms
    connection = device_status_label(numbers.db)
    if queue["failed"]:
        device_note = f"{queue['failed']} ارسال ناموفق"
    elif connection is None:
        device_note = "دستگاهی وصل نشده"
    elif connection == "آنلاین":
        device_note = "گوشی آنلاین است"
    elif connection == "آفلاین":
        device_note = "گوشی آفلاین است — تا وصل شدن می‌مانند"
    else:
        device_note = "در انتظار اتصال گوشی"
    healthy = not queue["queued"] and not queue["failed"] and connection == "آنلاین"
    return {"value": str(queue["queued"]), "sub": device_note, "clear": healthy}


def _build_birthdays_due(numbers: _Numbers, role: str) -> dict:
    count = numbers.club["birthday_count"]
    return {"value": str(count), "sub": "مشتری در آستانه تولد", "clear": not count}


def _build_follow_ups_due(numbers: _Numbers, role: str) -> dict:
    count = follow_up_due_count(numbers.db)
    return {"value": str(count), "sub": "مشتری در انتظار پیگیری", "clear": not count}


def _build_tier_ups_due(numbers: _Numbers, role: str) -> dict:
    count = len(tier_up_candidates(numbers.db))
    return {"value": str(count), "sub": "مشتری آماده ارتقا", "clear": not count}


def _build_downgrades_due(numbers: _Numbers, role: str) -> dict:
    """The one card whose job is to say that nothing happens on its own.

    The downgrade rule has no clock behind it, so a shop that never opens the
    page never sees it applied. That is the intent, and the sub says so; when the
    rule is switched off the card says *that* rather than reporting an empty list
    as if it had checked and found nobody.
    """
    plan = numbers.downgrades
    if not plan["enabled"]:
        return {"value": "—", "sub": "قاعده خاموش است — تنظیمات", "clear": False}
    count = len(plan["rows"])
    return {
        "value": str(count),
        # Latin figures, like every other number on this page; the review page
        # spells the same window in Persian digits, as that family of pages does.
        "sub": (f"بیش از {plan['months']} ماه بدون خرید · با تأیید شما" if count
                else "کسی واجد شرایط نیست"),
        "clear": not count,
    }


# ── money ────────────────────────────────────────────────────────────────────
# Split by the span each figure measures, so the eye cannot compare a day against
# a month. The section supplies the period and the card supplies the subject:
# «امروز ← فروش» reads in one glance, where three labels each repeating «امروز»
# read as three separate facts.
#
# The four a manager may see are the shop's takings; the four that are the
# owner's are what those takings cost and what is left of them. Every link goes
# to a page that role can actually open — the analytics page is owner-only, so
# the shop's own figures link to سود و زیان instead.


def _build_today_sales(numbers: _Numbers, role: str) -> dict:
    today = numbers.today
    return {"value": _money(today["net_sales"]),
            "sub": f"{today['sale_count']} فاکتور",
            "delta": _delta(today["net_sales"], numbers.yesterday["net_sales"],
                            label=YESTERDAY_LABEL)}


def _build_cashbox(numbers: _Numbers, role: str) -> dict:
    """The till as it stands, not as a period would compute it.

    The card used to show «the settings float plus today's movements», which
    looks like what is in the drawer and is not: when no shift has been opened
    nothing has been counted, so no float is known and the arithmetic describes
    a drawer that was never there. With a shift open the figure is the shift's
    own; with none, the card says the drawer is shut and reports the day's cash
    movement instead — a fact, rather than a guess wearing a balance's clothes.
    """
    session = numbers.open_shift
    if session:
        register = get_cashbox(numbers.db, session.opened_at, datetime.now(timezone.utc),
                               session.opening_balance, session.id)
        return {"value": _money(register["closing"]),
                "sub": f"صندوق باز از {jalali_str(session.opened_at)}"}
    cash = numbers.cash
    movement = (f"امروز {_money(cash['cash_in'])} ورود · {_money(cash['cash_out'])} خروج"
                if cash["cash_in"] or cash["cash_out"] else "امروز ورود و خروجی نداشته")
    return {"value": "باز نشده", "sub": movement}


def _build_cash_variance(numbers: _Numbers, role: str) -> dict | None:
    """A count that did not match, while it is still worth knowing about.

    Hidden entirely when the last shift balanced, so the attention section never
    asks for a decision that does not exist.
    """
    session = numbers.last_closed_shift
    if session is None or not session.variance:
        return None
    short = session.variance < 0
    return {"value": _money(abs(session.variance)),
            # A drawer short is money missing; a drawer over is a mistake to
            # explain. Both need attention, not the same amount of alarm.
            "tone": "danger" if short else "warning",
            "sub": (f"{'کسری' if short else 'اضافه'} در شیفت #{session.id} "
                    f"({jalali_str(session.closed_at, with_time=False)})")}


def _build_cash_stale(numbers: _Numbers, role: str) -> dict | None:
    """A drawer left open from an earlier day — the shift nobody closed."""
    session = numbers.open_shift
    # `as_utc` because SQLite hands the row back without a timezone while the
    # day boundary is aware; comparing the two raises rather than answering.
    if session is None or as_utc(session.opened_at) >= numbers._today_span[0]:
        return None
    return {"value": "باز مانده",
            "sub": f"از {jalali_str(session.opened_at, with_time=False)}"}


def _build_today_profit(numbers: _Numbers, role: str) -> dict:
    today = numbers.today
    return {"value": _money(today["gross_profit"]),
            # «—» before the day's first sale: a morning with no sales has no
            # margin, and «حاشیه 0٪» is the one thing it does not have.
            "sub": f"حاشیه {percent(today['gross_margin'])}",
            "delta": _delta(today["gross_profit"], numbers.yesterday["gross_profit"],
                            label=YESTERDAY_LABEL)}


def _build_month_sales(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["net_sales"]),
            "sub": f"{month['sale_count']} فاکتور",
            "delta": _delta(month["net_sales"], numbers.previous_month["net_sales"],
                            label=LAST_MONTH_LABEL)}


def _build_month_profit(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["net_profit"]),
            "sub": f"پس از کسر {_money(month['operating_expenses'])} هزینه",
            "delta": _delta(month["net_profit"], numbers.previous_month["net_profit"],
                            label=LAST_MONTH_LABEL)}


def _build_month_expenses(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["operating_expenses"]),
            "sub": "هزینه‌های ثبت‌شده",
            # Spending less is the good news here, which is why the verdict is
            # computed rather than left to the direction of an arrow.
            "delta": _delta(month["operating_expenses"],
                            numbers.previous_month["operating_expenses"],
                            label=LAST_MONTH_LABEL, lower_is_better=True)}


def _build_debt(numbers: _Numbers, role: str) -> dict:
    debts = numbers.debts
    return {"value": _money(debts["total_debt"]),
            "sub": f"{debts['debtor_count']} مشتری بدهکار"}


def _build_inventory_value(numbers: _Numbers, role: str) -> dict:
    return {"value": _money(numbers.month["inventory_value"]),
            "sub": "به قیمت خرید"}


# ── the club ─────────────────────────────────────────────────────────────────


def _build_club_total(numbers: _Numbers, role: str) -> dict:
    club = numbers.club
    archived = club["archived_count"]
    detail = f"{archived} بایگانی‌شده جدا از این عدد" if archived else "همه اعضای فعال"
    return {"value": str(club["total"]), "sub": detail}


def _build_club_active(numbers: _Numbers, role: str) -> dict:
    return {"value": str(numbers.club["active"]), "sub": "خرید در ۳۰ روز اخیر"}


def _build_club_new(numbers: _Numbers, role: str) -> dict:
    return {"value": str(numbers.club["new_this_month"]), "sub": "عضو جدید در این ماه"}


def _build_club_debtors(numbers: _Numbers, role: str) -> dict:
    return {"value": str(numbers.club["debtor_count"]), "sub": "مشتری با مانده بدهی"}


def _card(key: str, section: str, label: str, build, *,
          min_role: str = "manager", tone: str | None = None,
          href: str | None = None) -> dict:
    return {"key": key, "section": section, "label": label, "build": build,
            "min_role": min_role, "tone": tone, "href": href}


CARDS: tuple[dict, ...] = (
    # نیازمند توجه
    _card("stock", "attention", "تنوع کم‌موجود", _build_stock,
          tone="warning", href="/admin/products"),
    _card("pos", "attention", "تراکنش کارتخوان", _build_pos_unresolved,
          tone="danger", href="/admin/pos-reconciliation"),
    _card("credit", "attention", "نسیه سررسیدشده", _build_credit_overdue,
          tone="info", href="/admin/credit?status=overdue"),
    _card("checks", "attention", "چک سررسیدشده", _build_checks,
          tone="info", href="/admin/checks"),
    _card("cash_variance", "attention", "اختلاف صندوق", _build_cash_variance,
          tone="danger", href="/admin/cashbox"),
    _card("cash_stale", "attention", "صندوق باز مانده", _build_cash_stale,
          tone="warning", href="/admin/cashbox"),
    _card("backup", "attention", "پشتیبان‌گیری", _build_backup,
          min_role="owner", tone="success", href="/admin/backups"),
    _card("sms", "attention", "پیامک", _build_sms,
          tone="warning", href="/admin/sms"),
    # امروز
    _card("today_sales", "today", "فروش", _build_today_sales,
          tone="pine", href="/admin/accounting"),
    _card("cashbox", "today", "صندوق", _build_cashbox,
          tone="saffron", href="/admin/cashbox"),
    _card("today_profit", "today", "سود ناخالص", _build_today_profit,
          min_role="owner", tone="pine"),
    # این ماه
    _card("month_sales", "month", "فروش", _build_month_sales,
          tone="sky", href="/admin/accounting"),
    _card("month_profit", "month", "سود خالص", _build_month_profit,
          min_role="owner", tone="pine"),
    _card("month_expenses", "month", "هزینه‌ها", _build_month_expenses,
          min_role="owner", tone="saffron", href="/admin/expenses"),
    # مانده‌ها — balances, which belong to no single day or month
    _card("debt", "balances", "نسیه در گردش", _build_debt,
          tone="persimmon", href="/admin/credit"),
    _card("inventory_value", "balances", "ارزش موجودی انبار", _build_inventory_value,
          min_role="owner", tone="ink", href="/admin/products"),
    # کارهای دوره‌ای — all owner-only, because every destination is owner-only
    _card("birthdays_due", "periodic", "پیامک تولد", _build_birthdays_due,
          min_role="owner", href="/admin/birthdays"),
    _card("follow_ups_due", "periodic", "پیگیری مشتریان", _build_follow_ups_due,
          min_role="owner", href="/admin/follow-ups"),
    _card("tier_ups_due", "periodic", "ارتقای سطح", _build_tier_ups_due,
          min_role="owner", href="/admin/tier-up"),
    _card("downgrades_due", "periodic", "کاهش سطح", _build_downgrades_due,
          min_role="owner", tone="danger", href="/admin/tier-downgrades"),
    # باشگاه مشتریان
    _card("club_total", "club", "اعضای باشگاه", _build_club_total,
          href="/admin/customers"),
    _card("club_active", "club", "مشتریان فعال", _build_club_active,
          href="/admin/customers?status=active"),
    _card("club_new", "club", "عضو جدید این ماه", _build_club_new,
          href="/admin/customers"),
    _card("club_debtors", "club", "مشتریان بدهکار", _build_club_debtors,
          href="/admin/credit"),
    # There is deliberately no birthday card here. The number is the same one
    # the owner's «پیامک تولد» card shows, and showing one fact twice on a page
    # this short trains the reader to stop reading it. The customers page keeps
    # «تولد نزدیک» as a one-click filter for everybody.
)

# Each section names the span or the kind of number it holds, so a reader never
# has to work out which of two periods a card belongs to. An eyebrow is only
# present where it adds a fact the title does not already carry.
SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("attention", "نیازمند توجه", "اقدام‌های امروز", "attention"),
    ("today", "امروز", "", "stats"),
    ("month", "این ماه", "", "stats"),
    ("balances", "مانده‌ها", "", "stats"),
    ("periodic", "کارهای دوره‌ای", "", "stats"),
    ("club", "باشگاه مشتریان", "", "stats"),
    ("top", "پرفروش‌ترین‌های این ماه", "", "top"),
)


def _top_rows(numbers: _Numbers, role: str) -> list[dict]:
    """The month's best sellers, ranked. The profit figure is the owner's alone.

    Chosen here rather than in the template: a row simply has no profit key when
    the viewer may not see it, so no template edit can put it back.
    """
    owner = role_allows(role, "owner")
    rows = []
    for position, product in enumerate(numbers.top_products, start=1):
        rows.append({
            "rank": position,
            "name": product["name"],
            "category": product["category"],
            "qty": product["qty_sold"],
            "revenue": fmt(product["revenue"]),
            "profit": fmt(product["profit"]) if owner else None,
        })
    return rows


def dashboard_overview(db: Session, *, role: str = "manager") -> dict:
    """Every card this viewer may see, already formatted, in reading order.

    Cards for other roles are not built at all, and a section with nothing left
    in it disappears — so a manager is never handed an empty heading implying
    there is something to do that they cannot reach.
    """
    numbers = _Numbers(db)
    sections = []
    for key, title, eyebrow, kind in SECTIONS:
        section = {"key": key, "title": title, "eyebrow": eyebrow, "kind": kind,
                   "cards": [], "rows": [], "show_profit": False}
        if kind == "top":
            if not role_allows(role, "manager"):
                continue
            section["rows"] = _top_rows(numbers, role)
            section["show_profit"] = role_allows(role, "owner")
        else:
            for spec in CARDS:
                if spec["section"] != key or not role_allows(role, spec["min_role"]):
                    continue
                card = spec["build"](numbers, role)
                if card is None:
                    continue
                section["cards"].append({
                    "key": spec["key"], "label": spec["label"],
                    "tone": spec["tone"], "href": spec["href"],
                    "delta": None, "clear": False, **card,
                })
        if section["cards"] or section["rows"] or kind == "top":
            sections.append(section)
    return {
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "sections": sections,
    }
