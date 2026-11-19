from decimal import Decimal

from django.db import transaction

from core.audit import record_business_event
from core.models import (
    CreditNote,
    Dispatch,
    Invoice,
    Item,
    StockBatch,
    StockMovement,
)
from core.services.stock import create_movement


@transaction.atomic
def issue_credit_note(*, invoice, issue_date, reason, created_by):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.POSTED:
        raise ValueError("Only posted invoices can be credited.")
    if invoice.payments.exists():
        raise ValueError("Refund recorded payments before crediting this invoice.")
    reason = reason.strip()
    if not reason:
        raise ValueError("Enter a reason for the credit note.")

    reversal_rows = []
    dispatch_ids = list(
        Dispatch.objects.filter(invoice=invoice).values_list("pk", flat=True)
    )
    if dispatch_ids:
        movements = StockMovement.objects.filter(
            source_doc_type="DISPATCH",
            source_doc_id__in=dispatch_ids,
        ).values("item_id", "batch_id", "qty_out", "unit_cost_base")
        movement_totals = {}
        for movement in movements:
            key = (movement["item_id"], movement["batch_id"])
            total = movement_totals.setdefault(
                key,
                {"quantity": Decimal("0"), "value": Decimal("0")},
            )
            total["quantity"] += movement["qty_out"]
            total["value"] += movement["qty_out"] * movement["unit_cost_base"]
        item_ids = sorted({item_id for item_id, _ in movement_totals})
        batch_ids = sorted({
            batch_id for _, batch_id in movement_totals if batch_id is not None
        })
        locked_items = {
            item.pk: item
            for item in Item.objects.select_for_update().filter(pk__in=item_ids)
        }
        locked_batches = {
            batch.pk: batch
            for batch in StockBatch.objects.select_for_update().filter(pk__in=batch_ids)
        }
        for (item_id, batch_id), totals in movement_totals.items():
            quantity = totals["quantity"]
            if quantity <= Decimal("0"):
                continue
            unit_cost_base = (
                totals["value"] / quantity
            ).quantize(Decimal("0.00000001"))
            reversal_rows.append({
                "item": locked_items[item_id],
                "batch": locked_batches.get(batch_id),
                "quantity": quantity,
                "unit_cost_base": unit_cost_base,
            })

    credit_note = CreditNote.objects.create(
        number=f"CN-{invoice.number.replace('/', '-')}",
        invoice=invoice,
        issue_date=issue_date,
        reason=reason,
        taxable_total=invoice.taxable_total,
        cgst_total=invoice.cgst_total,
        sgst_total=invoice.sgst_total,
        igst_total=invoice.igst_total,
        total=invoice.total,
        created_by=created_by,
    )
    for row in reversal_rows:
        create_movement(
            item=row["item"],
            batch=row["batch"],
            qty_in=row["quantity"],
            unit_cost_base=row["unit_cost_base"],
            movement_type=StockMovement.MovementType.REVERSAL,
            source_doc_type="CREDIT_NOTE",
            source_doc_id=credit_note.pk,
            user=created_by,
        )
    invoice.status = Invoice.Status.CREDITED
    invoice.save(update_fields=["status"])
    record_business_event(
        action="ISSUE",
        entity_type="CreditNote",
        entity_id=credit_note.pk,
        user=created_by,
        source_doc_type="INVOICE",
        source_doc_id=invoice.pk,
        details={"number": credit_note.number, "total": credit_note.total},
    )
    return credit_note
