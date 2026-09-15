from __future__ import annotations

import re
from datetime import datetime, timezone

from models import Expense, SalaryPayment, StaffUser, to_english_digits
from services.accounting import open_cash_session
from services.events import append_event

_PERIOD_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def current_period_key(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    return value.strftime("%Y-%m")


def normalize_period_key(value: str) -> str:
    cleaned = to_english_digits((value or "").strip())
    if not _PERIOD_PATTERN.fullmatch(cleaned):
        raise ValueError("ماه پرداخت باید به شکل YYYY-MM باشد.")
    return cleaned


def create_salary_payment(
    db,
    staff_user: StaffUser,
    operator_user: StaffUser,
    period_key: str,
    deductions: int = 0,
    payment_method: str = "cash",
    note: str = "",
    request_id: str | None = None,
) -> SalaryPayment:
    """Create one immutable monthly wage record and its linked expense."""
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
    if deductions < 0 or deductions >= staff_user.salary_amount:
        raise ValueError("کسورات باید کمتر از حقوق ناخالص باشد.")
    if db.query(SalaryPayment).filter(
        SalaryPayment.staff_user_id == staff_user.id,
        SalaryPayment.period_key == period_key,
    ).first():
        raise ValueError("حقوق این کارمند برای این ماه قبلاً ثبت شده است.")

    net_amount = staff_user.salary_amount - deductions
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
        gross_amount=staff_user.salary_amount,
        deductions=deductions,
        net_amount=net_amount,
        payment_method=payment_method,
        operator_user_id=operator_user.id,
        expense_id=expense.id,
        cash_session_id=expense.cash_session_id,
        note=note.strip()[:1000] or None,
    )
    db.add(payment)
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
