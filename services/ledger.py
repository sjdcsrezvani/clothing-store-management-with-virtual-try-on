from datetime import datetime, timezone

from models import CashSession, FinancialEntry, PaymentReversal, Refund, RefundLine, Expense, SupplierPayment


def now():
    return datetime.now(timezone.utc)


def create_refund(db, sale, operator_id, reason, lines, payment_reference=None, pos_reference=None):
    refund = Refund(
        sale_id=sale.id,
        operator_user_id=operator_id,
        reason=reason or "Refund",
        original_payment_reference=payment_reference,
        pos_reversal_reference=pos_reference,
        total_amount=sum(line["amount"] for line in lines),
    )
    db.add(refund)
    db.flush()
    for line in lines:
        db.add(RefundLine(
            refund_id=refund.id,
            sale_item_id=line["sale_item_id"],
            quantity=line["quantity"],
            amount=line["amount"],
        ))
    db.add(FinancialEntry(
        entry_type="refund",
        amount=-refund.total_amount,
        refund_id=refund.id,
        operator_user_id=operator_id,
        reason=refund.reason,
        reference=payment_reference or pos_reference,
    ))
    return refund


def reverse_payment_immutably(db, payment, operator_id, reason):
    if payment.reversed_at:
        return None
    reversal = PaymentReversal(
        payment_id=payment.id,
        amount=payment.amount,
        reason=reason or "Payment reversal",
        operator_user_id=operator_id,
    )
    db.add(reversal)
    db.flush()
    db.add(FinancialEntry(
        entry_type="payment_reversal",
        amount=-payment.amount,
        payment_reversal_id=reversal.id,
        operator_user_id=operator_id,
        reason=reversal.reason,
    ))
    payment.reversed_at = now()
    payment.reversal_id = reversal.id
    return reversal


def reverse_expense_immutably(db, expense, operator_id, reason):
    if expense.reversed_at:
        return None
    expense.reversed_at = now()
    entry = FinancialEntry(
        entry_type="expense_reversal",
        amount=-expense.amount,
        expense_id=expense.id,
        operator_user_id=operator_id,
        reason=reason or "Expense reversal",
        reference=str(expense.id),
    )
    db.add(entry)
    db.flush()
    expense.reversal_id = entry.id
    return entry
