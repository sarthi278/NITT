from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from core.audit import record_business_event
from core.models import Item, StockBatch, StockInward, StockMovement
from core.services.stock import create_movement
from core.services.units import item_unit_multiplier, to_item_base


def _validate_batch_dates(*, batch_no, entry_date, manufacturing_date, expiry_date):
    if (manufacturing_date or expiry_date) and not batch_no:
        raise ValueError("Enter a batch number when supplying batch dates.")
    if manufacturing_date and manufacturing_date > entry_date:
        raise ValueError("Manufacturing date cannot be after the stock entry date.")
    if expiry_date and expiry_date < entry_date:
        raise ValueError("Expired stock cannot be received.")
    if manufacturing_date and expiry_date and expiry_date <= manufacturing_date:
        raise ValueError("Expiry date must be after manufacturing date.")


@transaction.atomic
def post_stock_inward(
    *,
    submission_key,
    item,
    display_quantity,
    unit,
    unit_cost,
    entry_date,
    user,
    batch_no="",
    manufacturing_date=None,
    expiry_date=None,
    supplier=None,
    remarks="",
):
    existing = StockInward.objects.filter(submission_key=submission_key).first()
    if existing:
        return existing, False

    item = Item.objects.select_for_update().get(pk=item.pk)
    if not item.is_active:
        raise ValueError("This item is inactive and cannot receive stock.")
    quantity = to_item_base(display_quantity, item, unit)
    try:
        cost = Decimal(str(unit_cost))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid unit cost")
    if not cost.is_finite() or cost < Decimal("0"):
        raise ValueError("Unit cost cannot be negative")
    unit_cost_base = (
        cost / item_unit_multiplier(item, unit)
    ).quantize(Decimal("0.00000001"))

    batch_no = batch_no.strip()
    _validate_batch_dates(
        batch_no=batch_no,
        entry_date=entry_date,
        manufacturing_date=manufacturing_date,
        expiry_date=expiry_date,
    )

    batch = None
    if batch_no:
        try:
            with transaction.atomic():
                batch, created = StockBatch.objects.get_or_create(
                    item=item,
                    batch_no=batch_no,
                    defaults={
                        "mfg_date": manufacturing_date,
                        "expiry_date": expiry_date,
                    },
                )
        except IntegrityError:
            batch = StockBatch.objects.get(item=item, batch_no=batch_no)
            created = False

        if not created:
            for field, supplied_date in (
                ("mfg_date", manufacturing_date),
                ("expiry_date", expiry_date),
            ):
                existing_date = getattr(batch, field)
                if supplied_date and existing_date and supplied_date != existing_date:
                    raise ValueError("Batch dates do not match the existing batch.")
                if supplied_date and not existing_date:
                    setattr(batch, field, supplied_date)
            if manufacturing_date or expiry_date:
                batch.save(update_fields=["mfg_date", "expiry_date"])

    inward = StockInward.objects.create(
        submission_key=submission_key,
        item=item,
        display_quantity=display_quantity,
        base_quantity=quantity,
        unit=unit,
        unit_cost=cost,
        batch=batch,
        supplier=supplier,
        entry_date=entry_date,
        remarks=remarks.strip(),
        created_by=user,
    )
    create_movement(
        item=item,
        batch=batch,
        qty_in=quantity,
        unit_cost=cost,
        unit_cost_base=unit_cost_base,
        movement_type=StockMovement.MovementType.ADJUSTMENT,
        source_doc_type="STOCK_INWARD",
        source_doc_id=inward.pk,
        user=user,
    )
    record_business_event(
        action="ADJUSTMENT",
        entity_type="StockInward",
        entity_id=inward.pk,
        user=user,
        source_doc_type="STOCK_INWARD",
        source_doc_id=inward.pk,
        details={"item_id": item.pk, "base_quantity": quantity},
    )
    return inward, True
