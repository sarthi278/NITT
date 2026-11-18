from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum

from core.models import Dispatch, Item, Order, StockBatch, StockMovement
from core.services.inventory_valuation import weighted_average_rate
from core.services.stock import balance, create_movement

ZERO = Decimal("0")


@transaction.atomic
def dispatch_invoice(*, invoice, dispatched_by, dispatch_date):
    invoice = invoice.__class__.objects.select_for_update().get(pk=invoice.pk)
    existing = Dispatch.objects.filter(invoice=invoice).first()
    if existing:
        return existing, False

    order_lines = list(
        invoice.lines.select_related("product__finished_item").order_by("pk")
    )
    quantities_by_item = {}
    items = {}
    for line in order_lines:
        item_id = line.product.finished_item_id
        if not item_id:
            raise ValueError(f"Finished-stock item is missing for {line.product_name}.")
        quantities_by_item[item_id] = quantities_by_item.get(item_id, ZERO) + line.quantity

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
    )
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

    order = invoice.order
    order.status = Order.Status.DISPATCHED
    order.save(update_fields=["status", "updated_at"])
    return dispatch, True


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
    return unbatched_balance + eligible_batch_balance


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
