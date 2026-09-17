"""Campaigns that reach real people and actually move money.

A campaign is three things at once, and this module is the only place that
decides all three:

* **A window.** Active, plus inside its start/end dates. ``campaign_status``
  names the state and every refusal is a sentence the counter can read.
* **An audience.** Who the SMS goes to. The audience is chosen per send (all
  consented customers, one tier, one tag, or the hand-picked list) and the
  preview counts real people — consented, not archived, not already invited —
  before anything is queued.
* **A discount.** The code works at the counter for anyone; a customer who
  holds the campaign gets it applied without typing anything. Either way the
  redemption is written to ``SaleCampaign`` and the customer's assignment
  records it, so the campaign page can show what it earned.

Assignments (``CampaignAssignment``) are the customer-visible half: the badge
in checkout, the column on the customers list, and the profile's campaign card
all read this one table.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Campaign, CampaignAssignment, Customer, Sale, SaleCampaign
from services._common import (
    fmt,
    get_setting_int,
    is_archived_customer,
    jalali_str,
    marketing_opt_in,
    share,
)
from services.tier import TIER_LABELS

# ── states ────────────────────────────────────────────────────────────────────

STATUS_LIVE = "live"
STATUS_SCHEDULED = "scheduled"
STATUS_EXPIRED = "expired"
STATUS_INACTIVE = "inactive"

STATUS_LABELS = {
    STATUS_LIVE: "در جریان",
    STATUS_SCHEDULED: "زمان‌بندی‌شده",
    STATUS_EXPIRED: "منقضی",
    STATUS_INACTIVE: "غیرفعال",
}

# How a customer came to hold a campaign.
SOURCE_LABELS = {"sms": "پیامک", "manual": "دستی", "checkout": "صندوق"}

ASSIGNMENT_STATUS_LABELS = {
    "invited": "دعوت‌شده",
    "used": "استفاده‌شده",
    "removed": "برداشته‌شده",
}

DEPOSIT_LIMIT_DEFAULT = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive; compare them in UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ── window ────────────────────────────────────────────────────────────────────

def campaign_is_live(campaign: Campaign, now: datetime | None = None) -> bool:
    """Active *and* inside its dates. Open-ended dates mean "no bound"."""
    return campaign_status(campaign, now) == STATUS_LIVE


def campaign_status(campaign: Campaign, now: datetime | None = None) -> str:
    now = now or _now()
    if not campaign.is_active:
        return STATUS_INACTIVE
    start = _aware(campaign.start_date)
    if start is not None and now < start:
        return STATUS_SCHEDULED
    end = _aware(campaign.end_date)
    if end is not None and now > end:
        return STATUS_EXPIRED
    return STATUS_LIVE


def campaign_window_label(campaign: Campaign) -> str:
    """One line describing the window, for the list and the detail page."""
    start = jalali_str(campaign.start_date, with_time=False) if campaign.start_date else None
    end = jalali_str(campaign.end_date, with_time=False) if campaign.end_date else None
    if start and end:
        return f"{start} تا {end}"
    if start:
        return f"از {start}"
    if end:
        return f"تا {end}"
    return "بدون محدودیت تاریخ"


def campaign_status_label(campaign: Campaign) -> str:
    return STATUS_LABELS[campaign_status(campaign)]


# ── the code ──────────────────────────────────────────────────────────────────

def normalise_code(value) -> str:
    return (str(value or "")).strip().upper()


def resolve_campaign_code(
    db: Session,
    code,
    *,
    total_amount: int | None = None,
    ignore_min_purchase: bool = False,
) -> tuple[Campaign | None, str | None]:
    """Turn a typed code into a campaign, or a sentence explaining the refusal.

    Returns ``(campaign, None)`` when the code is usable, ``(None, message)``
    when it is not, and ``(None, None)`` when nothing was typed at all — so the
    caller never has to invent wording and an empty field is never an error.
    """
    wanted = normalise_code(code)
    if not wanted:
        return None, None

    campaign = db.query(Campaign).filter(func.upper(Campaign.code) == wanted).first()
    if not campaign:
        return None, f"کد کمپین «{wanted}» پیدا نشد."

    status = campaign_status(campaign)
    if status != STATUS_LIVE:
        if status == STATUS_INACTIVE:
            return None, f"کمپین «{campaign.name}» غیرفعال است."
        if status == STATUS_SCHEDULED:
            return None, (f"کمپین «{campaign.name}» از "
                          f"{jalali_str(campaign.start_date, with_time=False)} شروع می‌شود.")
        return None, (f"کمپین «{campaign.name}» در "
                      f"{jalali_str(campaign.end_date, with_time=False)} تمام شده است.")

    if (not ignore_min_purchase and campaign.min_purchase
            and total_amount is not None and total_amount < campaign.min_purchase):
        return None, (f"کمپین «{campaign.name}» حداقل خرید "
                      f"{fmt(campaign.min_purchase)} تومان دارد.")

    return campaign, None


def campaign_discount_amount(campaign: Campaign, total_amount: int) -> int:
    """What this campaign takes off a basket of ``total_amount``."""
    percent = min(max(0, campaign.discount_percent or 0), 100)
    if percent <= 0:
        return 0
    return int(max(0, total_amount) * percent / 100)


def campaign_discount_line(campaign: Campaign, amount: int) -> str:
    return f"کمپین {campaign.name} ({campaign.discount_percent}٪): {fmt(amount)} تومان"


# ── who holds a campaign ──────────────────────────────────────────────────────

def _assignment(db: Session, campaign_id: int, customer_id: int) -> CampaignAssignment | None:
    return db.query(CampaignAssignment).filter(
        CampaignAssignment.campaign_id == campaign_id,
        CampaignAssignment.customer_id == customer_id,
    ).first()


def assign_campaign(
    db: Session,
    campaign: Campaign,
    customer: Customer,
    *,
    source: str = "manual",
) -> CampaignAssignment:
    """Give a customer this campaign. Idempotent — the unique pair holds."""
    existing = _assignment(db, campaign.id, customer.id)
    if existing:
        if existing.status == "removed":
            existing.status = "invited"
            existing.source = source
        return existing
    row = CampaignAssignment(
        campaign_id=campaign.id,
        customer_id=customer.id,
        status="invited",
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def unassign_campaign(db: Session, campaign: Campaign, customer: Customer) -> bool:
    """Take a customer off a campaign without losing the history."""
    existing = _assignment(db, campaign.id, customer.id)
    if not existing or existing.status == "removed":
        return False
    existing.status = "removed"
    return True


def mark_campaign_used(
    db: Session,
    campaign: Campaign,
    customer: Customer | None,
    *,
    sale_id: int | None = None,
    source: str = "checkout",
) -> CampaignAssignment | None:
    """Record that this customer redeemed the campaign on ``sale_id``."""
    if customer is None:
        return None
    existing = _assignment(db, campaign.id, customer.id)
    if not existing:
        existing = CampaignAssignment(
            campaign_id=campaign.id,
            customer_id=customer.id,
            status="invited",
            source=source,
        )
        db.add(existing)
    if existing.status == "removed":
        # A code typed at the counter is proof they came back for it.
        existing.status = "invited"
        existing.source = source
    existing.used_count = (existing.used_count or 0) + 1
    existing.used_at = _now()
    existing.sale_id = sale_id
    # A reusable campaign is a standing promo: it stays on offer. Everything
    # else burns here, which is why the counter stops showing it afterwards.
    existing.status = "invited" if campaign.is_reusable else "used"
    db.flush()
    return existing


def restore_campaign_after_refund(db: Session, sale_id: int) -> None:
    """A refunded invoice must not burn the customer's campaign."""
    rows = db.query(SaleCampaign).filter(SaleCampaign.sale_id == sale_id).all()
    if not rows:
        return
    from models import Sale  # local import to keep the module import-light

    for row in rows:
        campaign = db.query(Campaign).filter(Campaign.id == row.campaign_id).first()
        sale = db.query(Sale).filter(Sale.id == sale_id).first()
        customer_id = sale.customer_id if sale else None
        if campaign is None or not customer_id:
            continue
        if campaign.is_reusable:
            continue
        existing = _assignment(db, campaign.id, customer_id)
        if not existing:
            continue
        existing.status = "invited"
        existing.used_count = max(0, (existing.used_count or 0) - 1)
        if existing.used_count == 0:
            existing.used_at = None
            existing.sale_id = None


def campaign_for_customer(
    db: Session,
    customer: Customer | None,
    total_amount: int | None = None,
) -> Campaign | None:
    """The campaign a customer holds and can use right now, if any.

    Only what they hold counts — this never invents an assignment — and when
    several qualify the biggest saving wins, because stacking a campaign on
    itself makes no sense.
    """
    if customer is None:
        return None
    rows = db.query(Campaign, CampaignAssignment).join(
        CampaignAssignment, CampaignAssignment.campaign_id == Campaign.id,
    ).filter(
        CampaignAssignment.customer_id == customer.id,
        CampaignAssignment.status.in_(("invited", "used")),
    ).all()

    best: Campaign | None = None
    best_amount = 0
    for campaign, assignment in rows:
        if not campaign_is_live(campaign):
            continue
        # 'used' only stays eligible when the campaign is reusable.
        if assignment.status == "used" and not campaign.is_reusable:
            continue
        if campaign.min_purchase and total_amount is not None and total_amount < campaign.min_purchase:
            continue
        amount = campaign_discount_amount(campaign, total_amount or 0)
        if amount > best_amount or (best is None and amount == 0):
            best, best_amount = campaign, amount
    return best


def customer_campaign_map(db: Session, customer_ids: list[int]) -> dict[int, dict]:
    """Bulk badge data for the customers list — one query, no N+1.

    For each customer: the live campaign they hold (what the counter should
    honour) plus a count of campaigns they have already used, so the list can
    stay honest without a row-by-row lookup.
    """
    ids = [int(cid) for cid in customer_ids if cid]
    if not ids:
        return {}
    rows = db.query(CampaignAssignment, Campaign).join(
        Campaign, Campaign.id == CampaignAssignment.campaign_id,
    ).filter(
        CampaignAssignment.customer_id.in_(ids),
        CampaignAssignment.status != "removed",
    ).all()

    out: dict[int, dict] = {}
    for assignment, campaign in rows:
        entry = out.setdefault(assignment.customer_id, {"offered": None, "used_count": 0})
        live = campaign_is_live(campaign)
        if assignment.status == "used":
            entry["used_count"] += assignment.used_count or 1
        if live and (assignment.status == "invited" or campaign.is_reusable):
            current = entry["offered"]
            if current is None or campaign.discount_percent > current["campaign"].discount_percent:
                entry["offered"] = {
                    "campaign": campaign,
                    "assignment": assignment,
                    "percent": campaign.discount_percent,
                    "reusable": bool(campaign.is_reusable),
                }
    return out


def customer_campaign_history(db: Session, customer: Customer) -> list[dict]:
    """Every campaign this customer touched, newest first, for the profile."""
    rows = db.query(CampaignAssignment, Campaign).join(
        Campaign, Campaign.id == CampaignAssignment.campaign_id,
    ).filter(CampaignAssignment.customer_id == customer.id).all()

    history = [
        {
            "campaign": campaign,
            "assignment": assignment,
            "status": assignment.status,
            "status_label": ASSIGNMENT_STATUS_LABELS.get(assignment.status, assignment.status),
            "source_label": SOURCE_LABELS.get(assignment.source, assignment.source),
            "live": campaign_is_live(campaign),
            "campaign_status_label": STATUS_LABELS[campaign_status(campaign)],
            "invited_at": assignment.invited_at,
            "invite_sent_at": assignment.invite_sent_at,
            "used_at": assignment.used_at,
            "used_count": assignment.used_count or 0,
        }
        for assignment, campaign in rows
    ]
    history.sort(key=lambda row: (row["used_at"] or row["invited_at"] or _now()), reverse=True)
    return history


# ── the audience ──────────────────────────────────────────────────────────────

def parse_audience(value: str) -> tuple[str, str]:
    """``tier:gold`` / ``tag:vip`` / ``assigned`` / ``all`` → (kind, argument)."""
    raw = (value or "all").strip()
    if ":" in raw:
        kind, _, argument = raw.partition(":")
        if kind in {"tier", "tag"} and argument:
            return kind, argument
        return "all", ""
    if raw == "assigned":
        return "assigned", ""
    return "all", ""


def _pool(db: Session, campaign: Campaign) -> tuple[list[Customer], dict]:
    """Consented, reachable customers plus the counts a preview needs.

    Everyone who cannot be messaged is excluded: archived customers, anyone who
    opted out of marketing, anyone already invited by a previous send of this
    campaign (the re-send guard), and anyone deliberately taken off it.
    """
    customers = db.query(Customer).all()
    assignments = db.query(CampaignAssignment).filter(
        CampaignAssignment.campaign_id == campaign.id,
    ).all()
    sent = {a.customer_id for a in assignments if a.invite_sent_at is not None}
    removed = {a.customer_id for a in assignments if a.status == "removed"}
    assigned = {a.customer_id for a in assignments if a.status != "removed"}

    pool: list[Customer] = []
    skipped = {"archived": 0, "opted_out": 0, "already_sent": 0, "removed": 0}
    for customer in customers:
        if is_archived_customer(customer):
            skipped["archived"] += 1
            continue
        if not marketing_opt_in(customer):
            skipped["opted_out"] += 1
            continue
        if customer.id in removed:
            skipped["removed"] += 1
            continue
        if customer.id in sent:
            skipped["already_sent"] += 1
            continue
        pool.append(customer)

    context = {"assigned_ids": assigned, "skipped": skipped}
    return pool, context


def _matches(pool: list[Customer], kind: str, argument: str, assigned_ids: set[int]) -> list[Customer]:
    if kind == "tier":
        return [c for c in pool if c.tier == argument]
    if kind == "tag":
        from services.customers import parse_tags

        return [c for c in pool if argument in parse_tags(c.tags)]
    if kind == "assigned":
        return [c for c in pool if c.id in assigned_ids]
    return list(pool)


def audience_options(db: Session, campaign: Campaign) -> list[dict]:
    """Every audience choice with the number of people a send would reach.

    The number is the honest one: reachable people, minus anyone this campaign
    already messaged, and cut at the blast cap the store configured.
    """
    limit = max(1, get_setting_int(db, "campaign_sms_limit", DEPOSIT_LIMIT_DEFAULT))
    pool, context = _pool(db, campaign)
    assigned_ids = context["assigned_ids"]

    options = [{
        "key": "all",
        "label": "همه مشتریان رضایت‌دار",
        "count": min(len(pool), limit),
        "total": len(pool),
    }]
    for key, label in TIER_LABELS.items():
        matched = _matches(pool, "tier", key, assigned_ids)
        options.append({
            "key": f"tier:{key}",
            "label": f"سطح {label}",
            "count": min(len(matched), limit),
            "total": len(matched),
        })
    try:
        from services.customers import TAG_PALETTE
    except Exception:  # pragma: no cover - the palette is part of the customer service
        TAG_PALETTE = ()
    for key, label in TAG_PALETTE:
        matched = _matches(pool, "tag", key, assigned_ids)
        options.append({
            "key": f"tag:{key}",
            "label": f"برچسب {label}",
            "count": min(len(matched), limit),
            "total": len(matched),
        })
    handpicked = _matches(pool, "assigned", "", assigned_ids)
    options.append({
        "key": "assigned",
        "label": "مشتریان دست‌چین‌شده",
        "count": min(len(handpicked), limit),
        "total": len(handpicked),
    })
    return options


def campaign_recipients(
    db: Session,
    campaign: Campaign,
    audience: str = "all",
) -> dict:
    """The people one send would reach, plus the reasons others were skipped."""
    limit = max(1, get_setting_int(db, "campaign_sms_limit", DEPOSIT_LIMIT_DEFAULT))
    kind, argument = parse_audience(audience)
    pool, context = _pool(db, campaign)
    matched = _matches(pool, kind, argument, context["assigned_ids"])
    recipients = matched[:limit]
    return {
        "recipients": recipients,
        "count": len(recipients),
        "matched": len(matched),
        "capped": len(matched) > len(recipients),
        "limit": limit,
        "skipped": context["skipped"],
        "audience": audience or "all",
        "audience_label": audience_label(audience, db=db),
    }


def audience_label(audience: str, db: Session | None = None) -> str:
    kind, argument = parse_audience(audience)
    if kind == "tier":
        return f"سطح {TIER_LABELS.get(argument, argument)}"
    if kind == "tag":
        try:
            from services.customers import TAG_LABELS

            return f"برچسب {TAG_LABELS.get(argument, argument)}"
        except Exception:  # pragma: no cover
            return f"برچسب {argument}"
    if kind == "assigned":
        return "مشتریان دست‌چین‌شده"
    return "همه مشتریان رضایت‌دار"


# ── numbers for the pages ─────────────────────────────────────────────────────

def campaign_stats(db: Session, campaign: Campaign) -> dict:
    """What this campaign did: invited, used, redemptions, revenue."""
    rows = db.query(CampaignAssignment).filter(
        CampaignAssignment.campaign_id == campaign.id,
    ).all()
    invited = sum(1 for r in rows if r.status == "invited")
    used = sum(1 for r in rows if r.status == "used")
    messaged = [r for r in rows if r.invite_sent_at is not None]

    sale_row = db.query(
        func.coalesce(func.sum(SaleCampaign.discount_amount), 0),
        func.count(SaleCampaign.id),
        func.coalesce(func.sum(Sale.final_amount), 0),
    ).join(Sale, Sale.id == SaleCampaign.sale_id).filter(
        SaleCampaign.campaign_id == campaign.id,
        Sale.is_refunded == False,  # noqa: E712
    ).one()

    reached = invited + used
    return {
        "messaged": len(messaged),
        "invited": invited,
        "used": used,
        "reached": reached,
        "used_money": int(sale_row[0] or 0),
        "redemptions": int(sale_row[1] or 0),
        "revenue": int(sale_row[2] or 0),
        "response_rate": share(used, reached, 0),
        "status": campaign_status(campaign),
        "status_label": STATUS_LABELS[campaign_status(campaign)],
        "window_label": campaign_window_label(campaign),
    }


def campaign_overview(db: Session) -> dict:
    """The list page's KPI row: what campaigns are doing, in four numbers."""
    campaigns = db.query(Campaign).all()
    live = sum(1 for c in campaigns if campaign_is_live(c))
    scheduled = sum(1 for c in campaigns if campaign_status(c) == STATUS_SCHEDULED)

    messaged = db.query(func.count(CampaignAssignment.id)).filter(
        CampaignAssignment.invite_sent_at.isnot(None),
    ).scalar() or 0
    used = db.query(func.count(CampaignAssignment.id)).filter(
        CampaignAssignment.status == "used",
    ).scalar() or 0
    revenue = db.query(func.coalesce(func.sum(SaleCampaign.discount_amount), 0)).join(
        Sale, Sale.id == SaleCampaign.sale_id,
    ).filter(Sale.is_refunded == False).scalar() or 0  # noqa: E712

    return {
        "total": len(campaigns),
        "live": live,
        "scheduled": scheduled,
        "messaged": int(messaged),
        "used": int(used),
        "discount_given": int(revenue),
    }


def _used_counts(db: Session, campaign_ids: list[int]) -> dict[int, int]:
    """Redemptions per campaign in one query, for the list's ordering."""
    ids = [cid for cid in campaign_ids if cid]
    if not ids:
        return {}
    rows = db.query(
        SaleCampaign.campaign_id, func.count(SaleCampaign.id),
    ).filter(SaleCampaign.campaign_id.in_(ids)).group_by(SaleCampaign.campaign_id).all()
    return {row[0]: int(row[1]) for row in rows}


def campaign_filtered(
    db: Session,
    *,
    search: str = "",
    status: str = "all",
    order: str = "newest",
    page: int = 1,
    per_page: int = 25,
) -> dict:
    """One page of campaigns with the counters the table shows per row."""
    page = max(1, int(page or 1))
    rows = db.query(Campaign).all()

    if search:
        needle = search.strip().lower()
        rows = [c for c in rows
                if needle in (c.name or "").lower() or needle in (c.code or "").lower()]
    if status in {"live", "scheduled", "expired", "inactive"}:
        rows = [c for c in rows if campaign_status(c) == status]

    if order == "name":
        rows.sort(key=lambda c: c.name or "")
    elif order == "discount":
        rows.sort(key=lambda c: c.discount_percent or 0, reverse=True)
    elif order == "used":
        counts = _used_counts(db, [c.id for c in rows])
        rows.sort(key=lambda c: counts.get(c.id, 0), reverse=True)
    else:
        rows.sort(key=lambda c: c.created_at or _now(), reverse=True)

    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    window = rows[(page - 1) * per_page: page * per_page]

    return {
        "campaigns": window,
        "rows": [{"campaign": c,
                  "status": campaign_status(c),
                  "status_label": STATUS_LABELS[campaign_status(c)],
                  "window_label": campaign_window_label(c),
                  "stats": campaign_stats(db, c)} for c in window],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "status": status,
        "order": order,
    }
