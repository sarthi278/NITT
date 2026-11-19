from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.db.models import F, Q, Sum

from core.audit import record_business_event
from core.models import (
    Dispatch,
    DispatchLine,
    Invoice,
    Item,
    Order,
    OrderLine,
    StockBatch,
    StockMovement,
)
from core.services.inventory_valuation import weighted_average_rate
from core.services.stock import balance, create_movement

ZERO = Decimal("0")
QUANTITY_QUANTUM = Decimal("0.001")


@transaction.atomic
def dispatch_invoice(
    *,
    invoice,
    dispatched_by,
    dispatch_date,
    dispatch_quantities=None,
    submission_key=None,
):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if submission_key is not None:
        try:
            submission_id = UUID(str(submission_key))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A valid dispatch submission ID is required.")
        existing = Dispatch.objects.filter(submission_key=submission_id).first()
        if existing:
            if existing.invoice_id != invoice.pk:
                raise ValueError("This dispatch submission ID belongs to another invoice.")
            return existing, False

    if invoice.status != Invoice.Status.POSTED:
        raise ValueError("Only posted invoices can be dispatched.")
    if dispatch_quantities is None:
        existing = Dispatch.objects.filter(invoice=invoice).first()
        if existing:
            return existing, False
    elif submission_key is None:
        raise ValueError("A dispatch submission ID is required.")

    order = Order.objects.select_for_update().get(pk=invoice.order_id)
    invoice_lines = list(
        invoice.lines.select_related("product__finished_item").order_by("pk")
    )
    remaining = remaining_dispatch_quantities(invoice)
    valid_line_ids = {line.pk for line in invoice_lines}
    if dispatch_quantities is not None:
        for key in dispatch_quantities:
            try:
                line_id = int(key)
            except (TypeError, ValueError):
                raise ValueError("Select valid invoice lines to dispatch.")
            if line_id not in valid_line_ids:
                raise ValueError("Select valid invoice lines to dispatch.")

    selected_lines = []
    quantities_by_item = {}
    items = {}
    for line in invoice_lines:
        value = (
            remaining[line.pk]
            if dispatch_quantities is None
            else dispatch_quantities.get(
                line.pk,
                dispatch_quantities.get(str(line.pk), ZERO),
            )
        )
        try:
            quantity = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"Enter a valid dispatch quantity for {line.product_name}.")
        if not quantity.is_finite() or quantity < ZERO:
            raise ValueError("Dispatch quantities cannot be negative.")
        normalized = quantity.quantize(QUANTITY_QUANTUM)
        if quantity != normalized:
            raise ValueError("Dispatch quantities must use at most 3 decimal places.")
        if normalized > remaining[line.pk]:
            raise ValueError(
                f"Dispatch quantity for {line.product_name} exceeds the remaining "
                f"quantity of {remaining[line.pk]}."
            )
        if normalized == ZERO:
            continue

        item_id = line.product.finished_item_id
        if not item_id:
            raise ValueError(f"Finished-stock item is missing for {line.product_name}.")
        selected_lines.append((line, normalized))
        quantities_by_item[item_id] = quantities_by_item.get(item_id, ZERO) + normalized

    if not selected_lines:
        raise ValueError("Enter a positive quantity for at least one invoice line.")
    locked_items = {
        item.pk: item
        for item in Item.objects.select_for_update().filter(
            pk__in=sorted(quantities_by_item)
        )
    }
    allocations_by_item = {}
    for item_id, required in quantities_by_item.items():
        item = locked_items[item_id]
        allocations = _fefo_allocations(
            item=item,
            required=required,
            dispatch_date=dispatch_date,
        )
        allocations_by_item[item_id] = allocations
        items[item_id] = item

    dispatch = Dispatch.objects.create(
        invoice=invoice,
        dispatch_date=dispatch_date,
        dispatched_by=dispatched_by,
        **({"submission_key": submission_id} if submission_key is not None else {}),
    )
    DispatchLine.objects.bulk_create([
        DispatchLine(
            dispatch=dispatch,
            invoice_line=line,
            quantity=quantity,
        )
        for line, quantity in selected_lines
    ])
    rates = {
        item_id: weighted_average_rate(item)
        for item_id, item in items.items()
    }
    for item_id, allocations in allocations_by_item.items():
        item = items[item_id]
        for batch, quantity in allocations:
            create_movement(
                item=item,
                batch=batch,
                qty_out=quantity,
                unit_cost_base=rates[item_id],
                movement_type=StockMovement.MovementType.DISPATCH,
                source_doc_type="DISPATCH",
                source_doc_id=dispatch.pk,
                user=dispatched_by,
            )

    remaining_after_dispatch = remaining_dispatch_quantities(invoice)
    if not any(quantity > ZERO for quantity in remaining_after_dispatch.values()):
        order.status = Order.Status.DISPATCHED
        order.save(update_fields=["status", "updated_at"])
    record_business_event(
        action="DISPATCH",
        entity_type="Dispatch",
        entity_id=dispatch.pk,
        user=dispatched_by,
        source_doc_type="INVOICE",
        source_doc_id=invoice.pk,
        details={
            "quantities": {
                str(line.pk): quantity for line, quantity in selected_lines
            },
        },
    )
    return dispatch, True


def remaining_dispatch_quantities(invoice):
    invoice_lines = list(invoice.lines.order_by("pk"))
    dispatched = {
        row["invoice_line_id"]: row["total"]
        for row in DispatchLine.objects.filter(
            invoice_line__invoice_id=invoice.pk,
        ).values("invoice_line_id").annotate(total=Sum("quantity"))
    }
    return {
        line.pk: max(line.quantity - dispatched.get(line.pk, ZERO), ZERO)
        for line in invoice_lines
    }


def _fefo_allocations(*, item, required, dispatch_date):
    batch_balances, batches = _batch_balances(item, lock=True)
    allocations = []
    allocated = ZERO
    total_batch_balance = sum(batch_balances.values(), ZERO)
    for batch in batches:
        batch_balance = batch_balances[batch.pk]
        if batch_balance < ZERO:
            raise ValueError(f"Ledger stock is negative for batch {batch.batch_no}.")
        if (
            batch_balance == ZERO
            or (batch.expiry_date and batch.expiry_date < dispatch_date)
        ):
            continue
        quantity = min(batch_balance, required - allocated)
        if quantity > ZERO:
            allocations.append((batch, quantity))
            allocated += quantity
        if allocated == required:
            return allocations

    unbatched_balance = balance(item) - total_batch_balance
    if unbatched_balance < ZERO:
        raise ValueError(f"Ledger batch balances exceed total stock for {item.name}.")
    remaining = required - allocated
    if remaining > ZERO and unbatched_balance > ZERO:
        quantity = min(unbatched_balance, remaining)
        allocations.append((None, quantity))
        allocated += quantity
    if allocated < required:
        shortage = required - allocated
        raise ValueError(
            f"Insufficient non-expired stock for {item.name}: short by {shortage}."
        )
    return allocations


def available_for_dispatch(*, item, dispatch_date):
    batch_balances, batches = _batch_balances(item)
    for batch in batches:
        if batch_balances[batch.pk] < ZERO:
            raise ValueError(f"Ledger stock is negative for batch {batch.batch_no}.")
    total_batch_balance = sum(batch_balances.values(), ZERO)
    unbatched_balance = balance(item) - total_batch_balance
    if unbatched_balance < ZERO:
        raise ValueError(f"Ledger batch balances exceed total stock for {item.name}.")
    eligible_batch_balance = sum(
        (
            batch_balances[batch.pk]
            for batch in batches
            if not batch.expiry_date or batch.expiry_date >= dispatch_date
        ),
        ZERO,
    )
    physical_balance = unbatched_balance + eligible_batch_balance
    reserved = _reserved_quantity(item)
    return max(physical_balance - reserved, ZERO)


def _reserved_quantity(item):
    order_lines = list(
        OrderLine.objects.filter(
            product__finished_item=item,
            approved_quantity__gt=ZERO,
            order__status__in=(Order.Status.APPROVED, Order.Status.INVOICED),
        ).filter(
            Q(order__invoice__isnull=True)
            | Q(order__invoice__status=Invoice.Status.POSTED),
        ).values("pk", "approved_quantity")
    )
    if not order_lines:
        return ZERO

    line_ids = [line["pk"] for line in order_lines]
    dispatched = {
        row["invoice_line__order_line_id"]: row["total"]
        for row in DispatchLine.objects.filter(
            invoice_line__order_line_id__in=line_ids,
        ).values("invoice_line__order_line_id").annotate(total=Sum("quantity"))
    }
    return sum(
        (
            max(
                line["approved_quantity"] - dispatched.get(line["pk"], ZERO),
                ZERO,
            )
            for line in order_lines
        ),
        ZERO,
    )


def _batch_balances(item, *, lock=False):
    batch_totals = StockMovement.objects.filter(
        item=item,
        batch__isnull=False,
    ).values("batch_id").annotate(
        qty_in=Sum("qty_in"),
        qty_out=Sum("qty_out"),
    )
    totals_by_batch = {
        row["batch_id"]: (row["qty_in"] or ZERO) - (row["qty_out"] or ZERO)
        for row in batch_totals
    }
    batches = StockBatch.objects.filter(
        pk__in=totals_by_batch,
    )
    if lock:
        batches = batches.select_for_update()
    batches = batches.order_by(
        F("expiry_date").asc(nulls_last=True),
        F("mfg_date").asc(nulls_last=True),
        "pk",
    )
    return totals_by_batch, list(batches)
