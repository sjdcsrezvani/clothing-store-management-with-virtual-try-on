from __future__ import annotations

import re
from datetime import datetime, timezone

from models import Expense, SalaryPayment, SalaryPaymentItem, StaffUser, to_english_digits
from services.accounting import open_cash_session
from services.events import append_event

# A Jalali payroll month: the shop pays by the Persian calendar, so the key is
# a Jalali year and month. Pre-existing Gregorian keys stay valid history;
# only new writes are validated here.
_PERIOD_PATTERN = re.compile(r"^((13|14)\d{2})-(0[1-9]|1[0-2])$")


def current_period_key(value: datetime | None = None) -> str:
    import jdatetime
    day = (value or datetime.now(timezone.utc)).date()
    jd = jdatetime.date.fromgregorian(date=day)
    return f"{jd.year:04d}-{jd.month:02d}"


def normalize_period_key(value: str) -> str:
    cleaned = to_english_digits((value or "").strip())
    if not _PERIOD_PATTERN.fullmatch(cleaned):
        raise ValueError("ماه پرداخت باید به شکل YYYY-MM شمسی باشد (مثلاً ۱۴۰۵-۰۶).")
    return cleaned


# Which line kinds add to the wage and which take away. Advances behave as
# deductions: money the employee already holds, settled against this month.
ADD_KINDS = {"base", "overtime", "bonus"}
SUB_KINDS = {"advance", "deduction"}
ITEM_KINDS = ADD_KINDS | SUB_KINDS


def _coerce_item(raw: dict, salary_amount: int) -> dict:
    kind = str(raw.get("kind", "") or "").strip()
    if kind not in ITEM_KINDS:
        raise ValueError("نوع قلم حقوق نامعتبر است.")
    if kind == "base":
        raise ValueError("قلم حقوق پایه خودکار ثبت می‌شود.")
    try:
        amount = int(to_english_digits(
            str(raw.get("amount", "") or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        raise ValueError("مبلغ قلم حقوق نامعتبر است.")
    if amount <= 0:
        raise ValueError("مبلغ قلم حقوق باید بیشتر از صفر باشد.")
    label = str(raw.get("label", "") or "").strip()[:200]
    # A deduction without a reason is a number without an explanation — the
    # one line the owner will be asked about and cannot answer.
    if kind in SUB_KINDS and not label:
        raise ValueError("برای هر کسورات و پیش‌پرداخت، دلیل الزامی است.")
    return {"kind": kind, "label": label or None, "amount": amount}


def create_salary_payment(
    db,
    staff_user: StaffUser,
    operator_user: StaffUser,
    period_key: str,
    deductions: int = 0,
    payment_method: str = "cash",
    note: str = "",
    request_id: str | None = None,
    items: list[dict] | None = None,
) -> SalaryPayment:
    """Create one monthly wage record and its linked expense.

    The base line is always the current monthly salary; ``items`` carries the
    extra lines (advance/overtime/bonus/deduction with reasons). The legacy
    ``deductions`` total still works — it lands as one «کسورات» line — so
    older callers keep paying without change.
    """
    period_key = normalize_period_key(period_key)
    if not staff_user or not staff_user.is_active:
        raise ValueError("کارمند فعال یافت نشد.")
    if staff_user.salary_amount <= 0:
        raise ValueError("برای این کارمند حقوق ماهانه ثبت نشده است.")
    if payment_method not in {"cash", "card"}:
        raise ValueError("روش پرداخت حقوق نامعتبر است.")
    try:
        deductions = int(deductions)
    except (TypeError, ValueError):
        raise ValueError("کسورات حقوق نامعتبر است.")
    if deductions < 0:
        raise ValueError("کسورات حقوق نامعتبر است.")
    lines = [_coerce_item(raw, staff_user.salary_amount) for raw in (items or [])]
    if deductions:
        lines.append({"kind": "deduction", "label": "کسورات", "amount": deductions})
    gross = staff_user.salary_amount + sum(
        line["amount"] for line in lines if line["kind"] in ADD_KINDS)
    total_deductions = sum(line["amount"] for line in lines if line["kind"] in SUB_KINDS)
    if total_deductions >= gross:
        raise ValueError("جمع کسورات باید کمتر از حقوق ناخالص باشد.")
    # A voided month frees its slot: only a live payment blocks re-posting.
    if db.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == staff_user.id,
        SalaryPayment.period_key == period_key,
        SalaryPayment.is_voided == False,  # noqa: E712
    ).first():
        raise ValueError("حقوق این کارمند برای این ماه قبلاً ثبت شده است.")

    net_amount = gross - total_deductions
    open_session = open_cash_session(db)
    expense = Expense(
        amount=net_amount,
        category="حقوق کارکنان",
        expense_type="monthly",
        payment_method=payment_method,
        cash_session_id=open_session.id if open_session and payment_method == "cash" else None,
        note=(note.strip() or f"حقوق {staff_user.full_name or staff_user.username} - {period_key}")[:1000],
    )
    db.add(expense)
    db.flush()

    payment = SalaryPayment(
        staff_user_id=staff_user.id,
        period_key=period_key,
        gross_amount=gross,
        deductions=total_deductions,
        net_amount=net_amount,
        payment_method=payment_method,
        operator_user_id=operator_user.id,
        expense_id=expense.id,
        cash_session_id=expense.cash_session_id,
        note=note.strip()[:1000] or None,
    )
    db.add(payment)
    db.flush()
    db.add(SalaryPaymentItem(
        salary_payment_id=payment.id, kind="base",
        label="حقوق پایه", amount=staff_user.salary_amount))
    for line in lines:
        db.add(SalaryPaymentItem(
            salary_payment_id=payment.id, kind=line["kind"],
            label=line["label"], amount=line["amount"]))
    db.flush()
    append_event(
        db,
        "SalaryPaid",
        "salary_payment",
        payment.id,
        idempotency_key=f"salary-payment:{payment.id}:paid",
        actor_user_id=operator_user.id,
        request_id=request_id,
        payload={
            "staff_user_id": payment.staff_user_id,
            "period_key": payment.period_key,
            "gross_amount": payment.gross_amount,
            "deductions": payment.deductions,
            "net_amount": payment.net_amount,
            "payment_method": payment.payment_method,
            "expense_id": payment.expense_id,
            "cash_session_id": payment.cash_session_id,
        },
        occurred_at=payment.paid_at,
    )
    return payment


def void_salary_payment(db, payment: SalaryPayment, operator_user: StaffUser,
                        reason: str, request_id: str | None = None) -> SalaryPayment:
    """Void a salary payment with a reason: the linked salary expense is
    reversed through the ledger's own reversal (the same path a hand void
    takes, which is why salary expenses refuse direct deletion), and the
    payment row stays on the books as voided — history is never deleted,
    and the freed month may be re-paid fresh."""
    from services.ledger import reverse_expense_immutably
    if payment is None:
        raise ValueError("پرداخت حقوق یافت نشد.")
    if payment.is_voided:
        raise ValueError("این پرداخت قبلاً باطل شده است.")
    reason = str(reason or "").strip()[:500]
    if not reason:
        raise ValueError("دلیل ابطال الزامی است.")
    expense = db.query(Expense).filter(Expense.id == payment.expense_id).first()
    entry = reverse_expense_immutably(
        db, expense, operator_user.id, f"ابطال حقوق: {reason}",
        request_id=request_id) if expense is not None else None
    if expense is not None and entry is None:
        raise ValueError("سند هزینه این پرداخت قبلاً برگشت داده شده است.")
    payment.is_voided = True
    payment.void_reason = reason
    payment.voided_at = datetime.now(timezone.utc)
    payment.voided_by_user_id = operator_user.id
    db.flush()
    append_event(
        db,
        "SalaryVoided",
        "salary_payment",
        payment.id,
        idempotency_key=f"salary-payment:{payment.id}:voided",
        actor_user_id=operator_user.id,
        request_id=request_id,
        payload={"staff_user_id": payment.staff_user_id,
                 "period_key": payment.period_key,
                 "net_amount": payment.net_amount,
                 "reason": reason},
        occurred_at=payment.voided_at,
    )
    return payment


def run_monthly_payday(db, operator_user: StaffUser, period_key: str,
                       payment_method: str = "cash") -> dict:
    """Pay every payable staff member for ``period_key``: active, salaried,
    and without a live payment for the month (voided months re-pay).
    One transaction per person — a failure for one never strands the rest —
    and the report names who got paid, who was already paid, and who failed.
    """
    period_key = normalize_period_key(period_key)
    if payment_method not in {"cash", "card"}:
        raise ValueError("روش پرداخت حقوق نامعتبر است.")
    payable = db.query(StaffUser).filter(
        StaffUser.is_active == True,  # noqa: E712
        StaffUser.salary_amount > 0).order_by(StaffUser.id).all()
    report: dict = {"period": period_key, "created": [], "skipped": [], "failed": []}
    for person in payable:
        try:
            payment = create_salary_payment(
                db, person, operator_user, period_key,
                payment_method=payment_method)
            db.commit()
            report["created"].append(person.full_name or person.username)
        except ValueError as error:
            db.rollback()
            message = str(error)
            if "قبلاً ثبت شده" in message:
                report["skipped"].append(person.full_name or person.username)
            else:
                report["failed"].append({
                    "name": person.full_name or person.username, "error": message})
        except Exception as error:  # noqa: BLE001 — one stranger must not stop payday
            db.rollback()
            report["failed"].append({
                "name": person.full_name or person.username, "error": str(error)})
    return report
