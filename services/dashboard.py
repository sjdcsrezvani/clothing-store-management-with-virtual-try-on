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

from models import CashSession, Settings, to_persian_digits
from services._common import fmt, get_setting_int, jalali_str, percent
from services.accounting import (
    as_utc, debt_totals, get_cashbox, get_opening_balance, open_cash_session,
)
from services.analytics import get_date_range, get_top_products
from services.backup import latest_backup
from services.checks import check_alert_summary
from services.customers import customer_overview
from services.inventory import stock_alerts
from services.month_reading import month_reading
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


from services._common import delta as _delta


def _delta(current, previous, *, label: str, lower_is_better: bool = False,
           subject: str = "فروشی"):
    """How a figure moved, shared with سود و زیان. The sentence lives in
    ``services._common`` now; this alias keeps the dashboard's callers and
    pins reading the same name they always have."""
    """How a figure moved — and when no ratio is fair, the fact instead.

    A percentage against a base of zero is not a percentage, and neither is one
    taken from a base so small the ratio is arithmetic. But *saying nothing*
    there was its own wrong: the first days of a month, the first sale after a
    quiet yesterday — exactly the moments an owner is reading for — rendered a
    bare number with no reference at all. So each impossible ratio is replaced
    by the sentence the figure cannot carry:

    * nothing last period, something now — «{label} {subject} نداشت», the
      arrival stated as the news it is (reversed for spending, where the
      arrival of a cost is the bad news);
    * a base too small or a span gone negative — the base itself, «{label} X ت
      بود», so the card is read against a figure rather than against nothing.

    ``lower_is_better`` is the whole reason this returns a verdict instead of
    leaving the colour to the direction of the arrow: spending less moves the
    same way as earning more, and only the card's owner knows which is good news.
    ``subject`` names what the card counts, so «نداشت» can be said about sales,
    profit or expenses in the shop's own grammar.
    """
    if previous is None:
        return None
    if previous <= 0:
        if current > 0:
            return {"text": f"{label} {subject} نداشت", "good": not lower_is_better}
        # Both periods empty — the card's own figures already say it — or a
        # red period standing on nothing. Neither has a comparison inside it.
        return None
    if current < 0:
        # The sign is visible in the value itself; what it needs is its base.
        return {"text": f"{label} {_money(previous)} بود", "good": None}
    change = int(round((current - previous) / previous * 100))
    if abs(change) > 999:
        # The base was so small that the ratio is arithmetic rather than
        # information — «۴۰۰۰٪ بیشتر» is a rounding artefact, not a trend.
        # The base is small enough to say, so it is said.
        return {"text": f"{label} {_money(previous)} بود", "good": None}
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
    window = numbers.club["birthday_window"]
    # The count alone is a number waiting for its window: «۷ نفر» means nothing
    # until «در ۷ روز آینده» says how soon the wishes are due. A window the
    # store has switched off says that, rather than a count of an empty set.
    sub = (f"تولد در {to_persian_digits(window)} روز آینده" if window
           else "پنجره تولد خاموش است — تنظیمات")
    return {"value": str(count), "sub": sub, "clear": not count}


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
                            label=YESTERDAY_LABEL, subject="فروشی")}


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
    notes = [movement]
    # The merged cash card carries what used to be two alert cards: a drawer
    # short/over from the last closed shift, and a drawer left open overnight.
    variance = _build_cash_variance(numbers, role)
    if variance is not None:
        # The merged note names the shortage and its figure: a numeral the
        # drawer is short is the fact worth walking over to check.
        notes.append(f"اختلاف صندوق {variance['value']} · {variance['sub']}")
    stale = _build_cash_stale(numbers, role)
    if stale is not None:
        notes.append(stale["sub"])
    return {"value": "باز نشده", "sub": " · ".join(notes)}


def _build_sms_failed(numbers: _Numbers, role: str) -> dict | None:
    """Messages that tried the gateway and died — money already spent on sends
    that never arrived. Quiet when the failure queue is empty."""
    failed = numbers.sms["failed"]
    if not failed:
        return None
    return {"value": str(failed), "sub": "ارسال ناموفق — بررسی و ارسال دوباره"}


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


def _build_books_disagree(numbers: _Numbers, role: str) -> dict | None:
    """The books disagreed at the last سود و زیان visit, while they still do.

    The dashboard may never run the reconciliation walk itself — a page
    opened all day stays cheap — so it reads the verdict the accounting page
    leaves behind for the price of two settings rows. A card that cried on
    stale news would train the reader to ignore it, so the sub-line says
    when the verdict was established.
    """
    flag = numbers.db.query(Settings).filter(Settings.key == "books_disagree").first()
    if flag is None or flag.value != "1":
        return None
    stamped = numbers.db.query(Settings).filter(Settings.key == "books_checked_at").first()
    when = ""
    if stamped is not None and stamped.value:
        try:
            seen = datetime.fromisoformat(stamped.value)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            when = f" · بررسی {jalali_str(seen, with_time=False)}"
        except (TypeError, ValueError):
            when = ""
    return {"value": "نیازمند بررسی", "tone": "danger",
            "sub": f"دفاتر نمی‌خوانند{when}"}


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
                            label=YESTERDAY_LABEL, subject="سودی")}


def _build_month_sales(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["net_sales"]),
            "sub": f"{month['sale_count']} فاکتور",
            "delta": _delta(month["net_sales"], numbers.previous_month["net_sales"],
                            label=LAST_MONTH_LABEL, subject="فروشی")}


def _build_month_profit(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["net_profit"]),
            "sub": f"پس از کسر {_money(month['operating_expenses'])} هزینه",
            "delta": _delta(month["net_profit"], numbers.previous_month["net_profit"],
                            label=LAST_MONTH_LABEL, subject="سودی")}


def _build_month_expenses(numbers: _Numbers, role: str) -> dict:
    month = numbers.month
    return {"value": _money(month["operating_expenses"]),
            "sub": "هزینه‌های ثبت‌شده",
            # Spending less is the good news here, which is why the verdict is
            # computed rather than left to the direction of an arrow. And the
            # arrival of a cost is the bad news, so «نداشت» flips with it.
            "delta": _delta(month["operating_expenses"],
                            numbers.previous_month["operating_expenses"],
                            label=LAST_MONTH_LABEL, lower_is_better=True,
                            subject="هزینه‌ای")}


def _build_debt(numbers: _Numbers, role: str) -> dict:
    debts = numbers.debts
    overdue = debts["overdue_customers"]
    detail = f"{debts['debtor_count']} مشتری بدهکار"
    if overdue:
        detail += f" · {overdue} سررسیدشده"
    return {"value": _money(debts["total_debt"]), "sub": detail}


def _build_inventory_value(numbers: _Numbers, role: str) -> dict:
    return {"value": _money(numbers.month["inventory_value"]),
            "sub": "به قیمت خرید"}


# ── the club ─────────────────────────────────────────────────────────────────


def _build_club_total(numbers: _Numbers, role: str) -> dict:
    club = numbers.club
    new = club["new_this_month"]
    return {"value": str(club["total"]),
            "sub": f"{new} عضو جدید این ماه"}


def _build_club_active(numbers: _Numbers, role: str) -> dict:
    return {"value": str(numbers.club["active"]), "sub": "خرید در ۳۰ روز اخیر"}


def _card(key: str, section: str, label: str, build, *,
          min_role: str = "manager", tone: str | None = None,
          href: str | None = None) -> dict:
    return {"key": key, "section": section, "label": label, "build": build,
            "min_role": min_role, "tone": tone, "href": href}


CARDS: tuple[dict, ...] = (
    # اقدام فوری — money first: figures that lose value while unread.
    _card("pos", "urgent", "تراکنش کارتخوان", _build_pos_unresolved,
          min_role="owner", tone="danger", href="/admin/pos-reconciliation"),
    _card("credit", "urgent", "نسیه سررسیدشده", _build_credit_overdue,
          tone="info", href="/admin/credit?status=overdue"),
    _card("checks", "urgent", "چک سررسیدشده", _build_checks,
          tone="info", href="/admin/checks"),
    _card("books_disagree", "urgent", "مغایرت دفاتر", _build_books_disagree,
          tone="danger", href="/admin/accounting"),
    _card("sms_failed", "urgent", "پیامک ناموفق", _build_sms_failed,
          tone="danger", href="/admin/sms"),
    # نیازمند توجه — the chores: ops work that waits without costing.
    _card("stock", "attention", "تنوع کم‌موجود", _build_stock,
          tone="warning", href="/admin/products"),
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
          min_role="manager", tone="saffron", href="/admin/expenses"),
    # مانده‌ها — balances, which belong to no single day or month
    _card("debt", "balances", "نسیه در گردش", _build_debt,
          tone="persimmon", href="/admin/credit"),
    _card("inventory_value", "balances", "ارزش موجودی انبار", _build_inventory_value,
          min_role="manager", tone="ink", href="/admin/products"),
    # کارهای دوره‌ای — the sends are manager-operable, so managers see them;
    _card("birthdays_due", "periodic", "پیامک تولد", _build_birthdays_due,
          min_role="manager", href="/admin/birthdays"),
    _card("follow_ups_due", "periodic", "پیگیری مشتریان", _build_follow_ups_due,
          min_role="manager", href="/admin/follow-ups"),
    _card("tier_ups_due", "periodic", "ارتقای سطح", _build_tier_ups_due,
          min_role="manager", href="/admin/tier-up"),
    _card("downgrades_due", "periodic", "کاهش سطح", _build_downgrades_due,
          min_role="manager", tone="danger", href="/admin/tier-downgrades"),
    # باشگاه مشتریان
    _card("club_total", "club", "اعضای باشگاه", _build_club_total,
          href="/admin/customers"),
    _card("club_active", "club", "مشتریان فعال", _build_club_active,
          href="/admin/customers?status=active"),
    # No club_debtors card: the balances debt card already splits current vs
    # overdue, and one fact twice trains the reader to stop reading it.
)

# Each section names the span or the kind of number it holds, so a reader never
# has to work out which of two periods a card belongs to. An eyebrow is only
# present where it adds a fact the title does not already carry.
SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("urgent", "اقدام فوری", "پول", "attention"),
    ("attention", "نیازمند توجه", "عملیات", "attention"),
    ("today", "امروز", "", "stats"),
    ("month", "این ماه", "", "stats"),
    # The month in prose, from the same builders the analytics page and the
    # owner's monthly SMS read — one reading, three places it is said.
    ("reading", "روایت این ماه", "از همان نمودارهای سود و زیان", "reading"),
    ("balances", "مانده‌ها", "", "stats"),
    ("periodic", "کارهای دوره‌ای", "", "stats"),
    ("club", "باشگاه مشتریان", "", "stats"),
    ("top", "پرفروش‌ترین‌های این ماه", "", "top"),
)


def _month_reading(db: Session) -> list[str] | None:
    """The month's sentences, the same words the owner's SMS carries.

    Built from the same ``month_reading`` call the digest fires from, so the
    page and the phone cannot disagree about what the month said. Owner-only
    by construction: the caller never invokes this for a manager, and the
    sentences name profit and margins — the owner's figures.
    """
    start, end = get_date_range("month")
    reading = month_reading(db, start=start, end=end, span="این ماه")
    notes = reading["notes"]
    # The four an owner reads first, in the order the page's own sections put
    # them: takings, the day worth naming, what sold, who bought.
    keys = ("dailyChart", "categoryChart", "tierChart", "customerSegChart")
    sentences = [notes[key] for key in keys if notes.get(key)]
    # The page keeps one line — the day worth naming. The month's own cards
    # carry the rest, and four sentences restate them.
    return sentences[:1] or None


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


def _gateway_status(db: Session) -> dict:
    """The gateway strip's facts, via the one status reader the پیامک page uses."""
    from services.sms_gateway import gateway_status

    return gateway_status(db)


# ── Cashier view: my day, nobody else's figures ────────────────────────────
# A cashier never sees cost, profit, margins, or colleagues' numbers — not
# hidden, never computed. Every figure below is the viewer's own sales (who
# rang it, via the checkout's staffer), plus shop-wide totals without money
# context beyond the day's takings. Refunds are listed, never subtracted.

_FA_WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه",
                "یکشنبه")


def _greeting_stamp() -> tuple[str, str, str]:
    """Time-of-day salutation plus the Jalali date and clock time.

    The hour comes from the shop's own wall clock; the date/time from the
    same ``jalali_str`` every other stamp on this page reads.
    """
    now = datetime.now(timezone.utc)
    hour = datetime.now().hour
    if 5 <= hour < 12:
        salutation = "صبح بخیر"
    elif 12 <= hour < 14:
        salutation = "ظهر بخیر"
    elif 14 <= hour < 19:
        salutation = "عصر بخیر"
    else:
        salutation = "شب بخیر"
    stamp = jalali_str(now)
    date_part, _, time_part = stamp.partition(" ")
    return salutation, date_part, time_part


def _midnight_span(days_ago: int = 0):
    start = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _staff_day_sales(db: Session, user_id, start, end) -> dict:
    """One staffer's day, gross: sales they rang, refunds listed separately."""
    from models import CheckoutSession, Sale, SalePaymentPart
    sales = db.query(Sale).join(
        CheckoutSession, CheckoutSession.sale_id == Sale.id).filter(
        CheckoutSession.staff_user_id == user_id,
        Sale.created_at >= start, Sale.created_at < end,
        Sale.payment_confirmed == True,  # noqa: E712
    ).all()
    live = [s for s in sales if not s.is_refunded]
    refunded = [s for s in sales if s.is_refunded]
    legs = {"cash": 0, "card": 0, "credit": 0}
    manual = 0
    loyalty = 0
    for sale in live:
        parts = db.query(SalePaymentPart).filter(
            SalePaymentPart.sale_id == sale.id).all()
        if parts:
            for part in parts:
                if part.method in legs:
                    legs[part.method] += part.amount or 0
        elif sale.payment_method in legs:
            legs[sale.payment_method] += sale.final_amount or 0
        try:
            import json as _json
            details = _json.loads(sale.discount_details or "[]") or []
        except (ValueError, TypeError):
            details = []
        for line in details:
            # The writer side names manual discounts «تخفیف ویژه» — the one
            # prefix that separates the staffer's hand from loyalty math.
            if str(line).startswith("تخفیف ویژه"):
                manual += _line_amount(str(line))
            else:
                loyalty += _line_amount(str(line))
    return {
        "total": sum(s.final_amount or 0 for s in live),
        "count": len(live),
        "legs": legs,
        "manual": manual,
        "loyalty": loyalty,
        "refund_count": len(refunded),
        "refund_total": sum(s.final_amount or 0 for s in refunded),
    }


def _line_amount(line: str) -> int:
    """The toman figure inside a discount sentence, or 0 if none parses."""
    import re
    match = re.search(r"([\d,٬]+)\s*تومان", line)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", "").replace("٬", ""))
    except (ValueError, TypeError):
        return 0


def _arrow(current, previous) -> dict | None:
    """Direction only, no ratio: the cashier's delta names no amounts."""
    if previous is None or previous <= 0:
        return None if current <= 0 else {"text": "▲", "good": True}
    if current == previous:
        return {"text": "—", "good": None}
    up = current > previous
    return {"text": "▲" if up else "▼", "good": up}


def _cashier_overview(db: Session, *, user_id, user_name: str | None,
                      day_offset: int = 0) -> dict:
    """The counter's own day: greeting, my figures, shop target, history."""
    from models import Customer
    from services.security import ROLE_LABELS
    try:
        offset = max(0, min(6, int(day_offset or 0)))
    except (TypeError, ValueError):
        offset = 0
    start, end = _midnight_span(offset)
    mine = _staff_day_sales(db, user_id, start, end)
    prev_start, prev_end = start - timedelta(days=1), end - timedelta(days=1)
    prev = _staff_day_sales(db, user_id, prev_start, prev_end)
    weekday = _FA_WEEKDAYS[start.weekday()]
    cards = [
        {"key": "my_sales", "label": "فروش امروز من", "tone": "pine",
         "href": "/sales/",
         "value": _money(mine["total"]),
         "sub": (f"{mine['count']} فاکتور · نقدی {_money(mine['legs']['cash'])} · "
                 f"کارتی {_money(mine['legs']['card'])} · "
                 f"نسیه {_money(mine['legs']['credit'])}"),
         "delta": _arrow(mine["total"], prev["total"]), "clear": False},
        {"key": "my_discounts", "label": "تخفیف‌های من", "tone": None,
         "href": "/sales/",
         "value": _money(mine["manual"] + mine["loyalty"]),
         "sub": (f"دستی {_money(mine['manual'])} · "
                 f"باشگاهی {_money(mine['loyalty'])}"),
         "delta": None, "clear": False},
        {"key": "my_refunds", "label": "برگشتی‌های من", "tone": None,
         "href": "/sales/",
         "value": str(mine["refund_count"]),
         "sub": (f"{_money(mine['refund_total'])} برگشت خورد — از فروش کم نشده"
                 if mine["refund_count"] else "برگشتی ثبت نشده"),
         "delta": None, "clear": mine["refund_count"] == 0},
    ]
    signups = 0
    if user_id:
        signups = db.query(Customer).filter(
            Customer.created_by_user_id == user_id,
            Customer.created_at >= start,
            Customer.created_at < end,
        ).count()
    cards.append(
        {"key": "my_signups", "label": "مشتری‌های جدید من", "tone": None,
         "href": "/admin/customers",
         "value": str(signups),
         "sub": "ثبت‌شده امروز توسط من",
         "delta": None, "clear": signups == 0})
    target = get_setting_int(db, f"sales_target_{start.weekday()}", 0)
    if target > 0:
        pct = min(100, int(round(mine["total"] * 100 / target)))
        cards.append(
            {"key": "my_target", "label": "هدف امروز فروشگاه", "tone": "sky",
             "href": None,
             "value": f"٪{pct}",
             "sub": f"{_money(mine['total'])} از {_money(target)}",
             "delta": None, "clear": False,
             "progress": pct})
    from services.reporting import canonical_report
    shop = canonical_report(db, start, end)
    cards.append(
        {"key": "shop_today", "label": "فروش امروز فروشگاه", "tone": "sky",
         "href": "/sales/",
         "value": _money(shop["net_sales"]),
         "sub": f"{shop['sale_count']} فاکتور در کل فروشگاه",
         "delta": None, "clear": False})
    sections = [{
        "key": "myday", "title": "امروز من" if offset == 0 else history_day_title(offset),
        "eyebrow": "", "kind": "stats",
        "cards": cards, "rows": [],
    }]
    history = []
    for ago in range(7):
        day_start, day_end = _midnight_span(ago)
        day_sales = _staff_day_sales(db, user_id, day_start, day_end)
        label = ("امروز" if ago == 0 else
                 "دیروز" if ago == 1 else
                 _FA_WEEKDAYS[day_start.weekday()])
        history.append({
            "label": label,
            "href": f"/admin?day={ago}",
            "sales": _money(day_sales["total"]),
            "active": ago == offset,
        })
    sections.append({
        "key": "history", "title": "", "eyebrow": "", "kind": "history",
        "cards": [], "rows": [], "days": history,
    })
    summary = (f"امروز {summary_word(mine, target > 0)}" if offset == 0
               else f"نمایش {history[offset]['label']}")
    salutation, date_part, time_part = _greeting_stamp()
    return {
        "role": "cashier",
        "role_label": ROLE_LABELS.get("cashier", "cashier"),
        "sections": sections,
        "greeting": {
            "name": user_name or "",
            "weekday": weekday,
            "salutation": salutation,
            "date": date_part,
            "time": time_part,
            "summary": summary,
        },
    }


def history_day_title(offset: int) -> str:
    """The section title when the cashier looks back."""
    start, _ = _midnight_span(offset)
    label = "دیروز من" if offset == 1 else f"{_FA_WEEKDAYS[start.weekday()]} من"
    return label


def summary_word(mine: dict, has_target: bool = False) -> str:
    """The greeting's one line, from the day's own figures."""
    if mine["count"] == 0:
        return "هنوز فروشی ثبت نشده — اولین فاکتور روز را ببندید."
    tail = " — پیشرفت هدف پایین صفحه." if has_target else ""
    return (f"{mine['count']} فاکتور به مبلغ {_money(mine['total'])}"
            f"{tail}")


# ── Staff ranking + live counter (manager and owner) ─────────────────────────
# One query per staffer per span — the ranking is a handful of rows, and each
# row reuses the same day-math as the cashier's own cards, so the figures a
# manager compares are the figures each cashier sees.

def _sellers_in_span(db: Session, start, end) -> list:
    """Everyone who rang in the span, plus every active staffer on the roster.

    Ex-staff appear only with past sales beside them (history keeps them);
    active staff appear even at zero so an empty day still reads as a roster.
    """
    from models import CheckoutSession, Sale, StaffUser
    rang = {row[0] for row in db.query(CheckoutSession.staff_user_id).join(
        Sale, CheckoutSession.sale_id == Sale.id).filter(
        CheckoutSession.staff_user_id.isnot(None),
        Sale.created_at >= start, Sale.created_at < end,
        Sale.payment_confirmed == True,  # noqa: E712
    ).distinct().all()}
    roster = {u.id for u in db.query(StaffUser).filter(
        StaffUser.is_active == True).all()}  # noqa: E712
    users = db.query(StaffUser).filter(
        StaffUser.id.in_(rang | roster)).all()
    return sorted(users, key=lambda u: (u.full_name or u.username))


def _staff_ranking(db: Session, *, week: bool = False) -> list[dict]:
    """Per-seller today (or trailing 7 days): sales, invoices, discounts."""
    if week:
        end = _midnight_span(0)[1]
        start = end - timedelta(days=7)
    else:
        start, end = _midnight_span(0)
    rows = []
    for user in _sellers_in_span(db, start, end):
        day = _staff_day_sales(db, user.id, start, end)
        rows.append({
            "name": user.full_name or user.username,
            "invoices": day["count"],
            "sales": _money(day["total"]),
            "discounts": _money(day["manual"] + day["loyalty"]),
            "_total": day["total"],
        })
    rows.sort(key=lambda r: r["_total"], reverse=True)
    for row in rows:
        del row["_total"]
    return rows


def _live_counter(db: Session) -> dict | None:
    """Open drawers and live baskets right now, as one compact panel."""
    from models import CheckoutSession
    from services.accounting import open_cash_session
    sessions = db.query(CheckoutSession).filter(
        CheckoutSession.state.in_(("draft", "reserved", "payment_pending")),
    ).all()
    open_session = open_cash_session(db)
    bits = []
    if open_session:
        bits.append("صندوق باز است")
    if sessions:
        bits.append(f"{len(sessions)} سبد باز")
    if not bits:
        return None
    return {"value": f"{len(sessions)} سبد" if sessions else "—",
            "sub": " · ".join(bits)}


def _shop_target(db: Session) -> dict | None:
    """Today's shop takings against the weekday goal — managers see it too."""
    from services.reporting import canonical_report
    start, end = _midnight_span(0)
    target = get_setting_int(db, f"sales_target_{start.weekday()}", 0)
    if target <= 0:
        return None
    shop = canonical_report(db, start, end)
    pct = min(100, int(round(shop["net_sales"] * 100 / target)))
    return {"value": f"٪{pct}",
            "sub": f"{_money(shop['net_sales'])} از {_money(target)}",
            "progress": pct}


def dashboard_overview(db: Session, *, role: str = "manager", user_id=None,
                       user_name: str | None = None, day_offset: int = 0,
                       rank_week: bool = False) -> dict:
    """Every card this viewer may see, already formatted, in reading order.

    Cards for other roles are not built at all, and a section with nothing left
    in it disappears — so a manager is never handed an empty heading implying
    there is something to do that they cannot reach.
    """
    if role == "cashier" or (user_id and not role_allows(role, "manager")):
        return _cashier_overview(db, user_id=user_id, user_name=user_name,
                                 day_offset=day_offset)
    numbers = _Numbers(db)
    sections = []
    for key, title, eyebrow, kind in SECTIONS:
        section = {"key": key, "title": title, "eyebrow": eyebrow, "kind": kind,
                   "cards": [], "rows": [], "show_profit": False}
        if kind == "reading":
            # Owner-only, and lazy about it: the strip runs three aggregates
            # and a day-walk, so a manager's page must never pay for it — the
            # same discipline the other owner-only cards keep.
            if not role_allows(role, "owner"):
                continue
            section["sentences"] = _month_reading(db) or []
        elif kind == "top":
            if not role_allows(role, "manager"):
                continue
            section["rows"] = _top_rows(numbers, role)
            section["show_profit"] = role_allows(role, "owner")
        else:
            for spec in CARDS:
                if spec["section"] != key or not role_allows(role, spec["min_role"]):
                    continue
                try:
                    card = spec["build"](numbers, role)
                except Exception:
                    # A failed query shows as a failed card, never a missing
                    # one: silence would read as a calm shop.
                    import logging as _logging
                    _logging.getLogger(__name__).exception(
                        "dashboard card %s failed", spec["key"])
                    card = {"value": "—", "sub": "خطا در محاسبه — بعداً ببینید"}
                if card is None:
                    continue
                section["cards"].append({
                    "key": spec["key"], "label": spec["label"],
                    "tone": spec["tone"], "href": spec["href"],
                    "delta": None, "clear": False, **card,
                })
        if section["cards"] or section["rows"] or section.get("sentences") or kind == "top":
            sections.append(section)
    if role_allows(role, "manager"):
        # The counter, watched: who rang what, what is live, whether the day
        # meets its goal. Names are a manager's working material here.
        staff_rows = _staff_ranking(db, week=rank_week)
        sections.append({
            "key": "staff", "title": "فروشندگان", "eyebrow": "امروز" if not rank_week else "۷ روز گذشته",
            "kind": "staff", "cards": [], "rows": staff_rows,
            "rank_week": rank_week,
            "targets_editable": True,
            "targets": {str(w): get_setting_int(db, f"sales_target_{w}", 0)
                        for w in range(7)},
            "weekdays": ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه",
                         "شنبه", "یکشنبه"],
        })
        live = _live_counter(db)
        if live is not None:
            sections.append({
                "key": "live", "title": "الان صندوق", "eyebrow": "", "kind": "stats",
                "cards": [{"key": "live_counter", "label": "صندوق و سبدهای باز",
                           "tone": None, "href": "/admin/cashbox",
                           "delta": None, "clear": False, **live}],
                "rows": [],
            })
        target = _shop_target(db)
        if target is not None:
            sections.append({
                "key": "target", "title": "هدف امروز", "eyebrow": "", "kind": "stats",
                "cards": [{"key": "shop_target", "label": "هدف امروز فروشگاه",
                           "tone": "sky", "href": None,
                           "delta": None, "clear": False, **target}],
                "rows": [],
            })
    # Every card speaks the same keys: goal cards carry a progress, the rest
    # carry none — so the template never reads a name a builder left out.
    for section in sections:
        for card in section.get("cards", []):
            card.setdefault("progress", None)
    return {
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "sections": sections,
        "greeting": _manager_greeting(db, role, user_name, sections),
        # The gateway strip reads this, not a card: two light queries, no
        # money figures, safe for every role that reaches the dashboard.
        "gateway": _gateway_status(db),
    }


def _manager_greeting(db: Session, role: str, user_name: str | None,
                      sections: list) -> dict | None:
    """Name, weekday, and the day in one line — for managers and owners too."""
    if not user_name:
        return None
    from services.reporting import canonical_report
    start, _ = _midnight_span(0)
    shop = canonical_report(db, start, start + timedelta(days=1))
    urgent = sum(len(s.get("cards", [])) for s in sections
                 if s["key"] == "urgent")
    salutation, date_part, time_part = _greeting_stamp()
    return {
        "name": user_name,
        "weekday": _FA_WEEKDAYS[start.weekday()],
        "salutation": salutation,
        "date": date_part,
        "time": time_part,
        "summary": (f"فروش امروز {_money(shop['net_sales'])} — "
                    f"{urgent} اقدام فوری" if urgent else
                    f"فروش امروز {_money(shop['net_sales'])} — روز آرام است."),
    }
