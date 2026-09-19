"""What each chart on سود و زیان shows, said in one sentence.

The page is read by an owner who may not be able to tell the chart's accents
apart, in a shop whose palette changes with the season, and printed in one colour
when a figure has to be discussed away from the counter. A chart that only means
something through its colours means nothing to any of those readers, so every
chart carries a sentence stating what its marks show — built here from the same
figures the chart draws, so the sentence and the picture cannot disagree.

Every sentence is one line, always says something (a period with nothing in it is
stated rather than left blank), and is keyed by the canvas it belongs to. The
test that pins these keys to the canvases the page actually draws is what keeps a
new chart from arriving without a sentence under it.

The same sentences are read somewhere no canvas is drawn at all — the dashboard's
month strip and the owner's monthly SMS summary. The builders therefore take the
period as a word (``span``) instead of hard-coding «این بازه»: the page says
«این بازه», the dashboard says «این ماه», and the two are guarded to stay the
same words apart from that one substitution — a sentence edited only on the page
cannot quietly stop being true of the phone.
"""

from __future__ import annotations

from services._common import fmt
from services.tier import TIER_LABELS

# Said instead of a fabricated zero: a range with no sales in it is a fact about
# the range, and a chart of it shows nothing at all.
NO_DATA = "در این بازه فروشی ثبت نشده."

# The word the page's sentences use for their period. A consumer reading about a
# named month passes its own word (the dashboard says «این ماه») — nothing else
# about the sentences may differ, which the parity guard pins.
DEFAULT_SPAN = "این بازه"

# Every chart on the analytics page, by the canvas it is drawn on. The page's own
# markup is what this list is checked against.
CHART_IDS = (
    "pricingChart",
    "colorChart",
    "sizeChart",
    "trendChart",
    "dailyChart",
    "categoryChart",
    "tierChart",
    "weekdayChart",
    "hourChart",
    "priceDistChart",
    "marginCatChart",
    "customerSegChart",
)


def _money(amount) -> str:
    return f"{fmt(int(round(float(amount or 0))))} ت"


def _share(part, total) -> int:
    total = float(total or 0)
    if total <= 0:
        return 0
    return int(round(float(part or 0) / total * 100))


def _rows(values) -> list[dict]:
    return [row for row in (values or []) if isinstance(row, dict)]


def _pricing(price_stats) -> str:
    """The bars are cost, price and profit; the sentence is the same three in words."""
    stats = price_stats if isinstance(price_stats, dict) else {}
    price = float(stats.get("avg_price") or 0)
    cost = float(stats.get("avg_cost") or 0)
    profit = float(stats.get("avg_profit") or 0)
    if not price and not cost:
        return NO_DATA
    margin = _share(profit, price)
    if profit < 0:
        # Worth saying plainly: a negative bar is the one thing on this page a
        # reader must not have to work out from a colour.
        return (f"هر واحد فروش بهطور میانگین {_money(abs(profit))} زیان میدهد: "
                f"{_money(price)} فروش در برابر {_money(cost)} هزینه کالا.")
    return (f"هر واحد فروش بهطور میانگین {_money(profit)} سود دارد — "
            f"{margin}٪ از قیمت؛ از {_money(price)} فروش، {_money(cost)} هزینه کالا است.")


def _variant(rows, noun: str, total: int) -> str:
    """Which colour or size carries the sales, and how much of them."""
    rows = [row for row in _rows(rows) if float(row.get("quantity") or 0) > 0]
    if not rows:
        return NO_DATA
    top = rows[0]
    units = int(float(top.get("quantity") or 0))
    return (f"پرفروشترین {noun} «{top.get('label') or '—'}» با {units} واحد است — "
            f"{_share(units, total)}٪ از واحدهای فروختهشده.")


def _trend(months) -> str:
    """The growth line: its peak, and where the last finished month landed."""
    months = [row for row in _rows(months) if row.get("month")]
    if not months or not any(float(row.get("revenue") or 0) for row in months):
        return NO_DATA
    peak = max(months, key=lambda row: float(row.get("revenue") or 0))
    sentence = f"بالاترین درآمد در {peak['month']} با {_money(peak.get('revenue'))} بود."
    # The line ends on the current, unfinished month, so the comparison is made
    # between the two finished months before it rather than against a partial one.
    finished = months[:-1]
    if len(finished) >= 2:
        last, previous = finished[-1], finished[-2]
        before = float(previous.get("revenue") or 0)
        if before > 0:
            change = int(round((float(last.get("revenue") or 0) - before) / before * 100))
            if change == 0:
                sentence += f" {last['month']} بدون تغییر نسبت به {previous['month']} ماند."
            else:
                sentence += (f" {last['month']} نسبت به {previous['month']} "
                             f"{abs(change)}٪ {'بیشتر' if change > 0 else 'کمتر'} بود.")
    return sentence


def _daily(days, span: str = DEFAULT_SPAN) -> str:
    days = [row for row in _rows(days) if row.get("date")]
    selling = [row for row in days if float(row.get("revenue") or 0)]
    if not selling:
        return NO_DATA
    best = max(selling, key=lambda row: float(row.get("revenue") or 0))
    revenue = sum(float(row.get("revenue") or 0) for row in days)
    profit = sum(float(row.get("profit") or 0) for row in days)
    return (f"در {len(selling)} روز {span} فروش ثبت شده: بیشترین {_money(best.get('revenue'))} "
            f"در {best['date']}، جمع {_money(revenue)} و سود {_money(profit)}.")


def _category(categories, span: str = DEFAULT_SPAN) -> str:
    categories = [row for row in _rows(categories) if float(row.get("revenue") or 0)]
    if not categories:
        return NO_DATA
    total = sum(float(row.get("revenue") or 0) for row in categories)
    top = max(categories, key=lambda row: float(row.get("revenue") or 0))
    share = _share(top.get("revenue"), total)
    sentence = (f"«{top.get('category') or 'بدون دسته'}» با {share}٪ بیشترین سهم فروش را دارد "
                f"({_money(top.get('revenue'))} از {_money(total)})؛ "
                f"{len(categories)} دسته در {span} فروش داشتهاند.")
    if share >= 50:
        sentence += " بیش از نیمی از فروش به یک دسته وابسته است."
    return sentence


def _tier(rows, span: str = DEFAULT_SPAN) -> str:
    rows = [row for row in _rows(rows) if float(row.get("revenue") or 0)]
    if not rows:
        return NO_DATA
    total = sum(float(row.get("revenue") or 0) for row in rows)
    top = max(rows, key=lambda row: float(row.get("revenue") or 0))
    label = top.get("label") or TIER_LABELS.get(top.get("tier"), top.get("tier") or "—")
    return (f"مشتریان «{label}» با {_share(top.get('revenue'), total)}٪ بیشترین سهم فروش را "
            f"دارند ({_money(top.get('revenue'))})؛ {len(rows)} سطح در {span} خرید کردهاند.")


def _weekday(pattern) -> str:
    days = [row for row in _rows((pattern or {}).get("weekdays")) if row.get("day")]
    if not days or not any(float(row.get("revenue") or 0) for row in days):
        return NO_DATA
    best = max(days, key=lambda row: float(row.get("revenue") or 0))
    worst = min(days, key=lambda row: float(row.get("revenue") or 0))
    return (f"شلوغترین روز «{best['day']}» با {_money(best.get('revenue'))} و کمرونقترین "
            f"«{worst['day']}» با {_money(worst.get('revenue'))} است.")


def _hour(pattern) -> str:
    hours = [row for row in _rows((pattern or {}).get("hours")) if row.get("label")]
    if not hours or not any(float(row.get("revenue") or 0) for row in hours):
        return NO_DATA
    total = sum(float(row.get("revenue") or 0) for row in hours)
    best = max(hours, key=lambda row: float(row.get("revenue") or 0))
    return (f"اوج فروش ساعت {best['label']} با {_money(best.get('revenue'))} است — "
            f"{_share(best.get('revenue'), total)}٪ از فروش ساعتبندیشده.")


def _price_dist(buckets, span: str = DEFAULT_SPAN) -> str:
    buckets = [row for row in _rows(buckets) if float(row.get("quantity") or 0)]
    if not buckets:
        return NO_DATA
    units = sum(float(row.get("quantity") or 0) for row in buckets)
    top = max(buckets, key=lambda row: float(row.get("quantity") or 0))
    return (f"بیشترین فروش در بازه قیمتی {_money(top.get('price'))} بوده: "
            f"{int(float(top.get('quantity') or 0))} واحد، "
            f"{_share(top.get('quantity'), units)}٪ از اقلام {span}.")


def _margin(margin_by_cat, span: str = DEFAULT_SPAN) -> str:
    # A category with no revenue has no margin — the figure is `None`, not nought —
    # and a sentence about it would be the page stating a margin nobody has.
    rows = [row for row in _rows(margin_by_cat)
            if row.get("category") and row.get("margin") is not None]
    if not rows:
        return NO_DATA
    ordered = sorted(rows, key=lambda row: int(row.get("margin") or 0))
    worst, best = ordered[0], ordered[-1]
    losing = [row for row in ordered if int(row.get("margin") or 0) < 0]
    if len(ordered) == 1:
        margin = int(best.get("margin") or 0)
        verdict = "زیانده" if margin < 0 else "سودده"
        return f"این دسته در {span} {verdict} است: حاشیه سود {margin}٪."
    sentence = (f"بالاترین حاشیه سود «{best['category']}» با {int(best.get('margin') or 0)}٪ و "
                f"پایینترین «{worst['category']}» با {int(worst.get('margin') or 0)}٪ است.")
    if losing:
        sentence += f" {len(losing)} دسته از {len(ordered)} دسته زیاندهاند."
    else:
        sentence += " هیچ دستهای زیانده نیست."
    return sentence


def _segments(customer_health, span: str = DEFAULT_SPAN) -> str:
    health = customer_health if isinstance(customer_health, dict) else {}
    segments = [row for row in _rows(health.get("segments")) if float(row.get("count") or 0)]
    repeat = int(health.get("repeat_rate") or 0)
    one_timer = int(health.get("one_timer_pct") or 0)
    if not segments and not repeat and not one_timer:
        return NO_DATA
    sentence = (f"{repeat}٪ مشتریان {span} بیش از یکبار خرید کردهاند و {one_timer}٪ فقط یکبار؛ "
                f"میانگین {health.get('avg_orders') or 0} سفارش برای هر مشتری.")
    if segments:
        biggest = max(segments, key=lambda row: float(row.get("count") or 0))
        sentence += (f" پرحجمترین گروه «{biggest.get('label')}» با "
                     f"{int(float(biggest.get('count') or 0))} مشتری است.")
    return sentence


def chart_notes(*, price_stats=None, color_stats=None, size_stats=None, daily=None,
                categories=None, tier_revenue=None, revenue_trend=None,
                sales_pattern=None, price_dist=None, margin_by_cat=None,
                customer_health=None, span: str = DEFAULT_SPAN) -> dict[str, str]:
    """One sentence per chart, keyed by the canvas it belongs to.

    Every argument is optional and any of them may be empty: an owner looking at a
    day with no sales gets a sentence saying so rather than a chart with a caption
    that claims something. ``span`` is the word the sentences use for their period
    — the page's default, or a named month for a reader with no charts in front
    of them.
    """
    colour_units = sum(int(float(row.get("quantity") or 0)) for row in _rows(color_stats))
    size_units = sum(int(float(row.get("quantity") or 0)) for row in _rows(size_stats))
    notes = {
        "pricingChart": _pricing(price_stats),
        "colorChart": _variant(color_stats, "رنگ", colour_units),
        "sizeChart": _variant(size_stats, "سایز", size_units),
        "trendChart": _trend(revenue_trend),
        "dailyChart": _daily(daily, span),
        "categoryChart": _category(categories, span),
        "tierChart": _tier(tier_revenue, span),
        "weekdayChart": _weekday(sales_pattern),
        "hourChart": _hour(sales_pattern),
        "priceDistChart": _price_dist(price_dist, span),
        "marginCatChart": _margin(margin_by_cat, span),
        "customerSegChart": _segments(customer_health, span),
    }
    if span != DEFAULT_SPAN:
        # A reader with no charts in front of them is not reading about «این
        # بازه»: the empty sentence is rewritten to name their period too.
        empty = NO_DATA.replace(DEFAULT_SPAN, span)
        notes = {key: (empty if value == NO_DATA else value)
                 for key, value in notes.items()}
    return notes
