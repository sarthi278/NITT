from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum

from core.models import Invoice, Payment

ZERO = Decimal("0")
CENT = Decimal("0.01")


def invoice_outstanding(invoice):
    if invoice.status == Invoice.Status.CREDITED:
        return ZERO.quantize(CENT)
    paid = invoice.payments.aggregate(total=Sum("amount"))["total"] or ZERO
    return max(invoice.total - paid, ZERO).quantize(CENT)


@transaction.atomic
def record_payment(
    *,
    invoice,
    amount,
    payment_date,
    method,
    reference="",
    received_by,
):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.POSTED:
        raise ValueError("Payments can only be recorded against posted invoices.")
    try:
        payment_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid payment amount.")
    if (
        not payment_amount.is_finite()
        or payment_amount <= ZERO
        or payment_amount != payment_amount.quantize(CENT)
    ):
        raise ValueError("Payment amount must be positive and have at most 2 decimals.")
    if payment_amount > invoice_outstanding(invoice):
        raise ValueError("Payment cannot exceed the invoice's outstanding amount.")
    if method not in Payment.Method.values:
        raise ValueError("Select a valid payment method.")
    return Payment.objects.create(
        invoice=invoice,
        payment_date=payment_date,
        amount=payment_amount,
        method=method,
        reference=reference.strip(),
        received_by=received_by,
    )
