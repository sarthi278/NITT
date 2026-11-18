from decimal import Decimal, InvalidOperation

from django.db import transaction

from core.models import Item, Purchase, PurchaseLine, StockMovement
from core.services.units import item_unit_multiplier, to_item_base
from core.services.stock import create_movement


@transaction.atomic
def post_purchase(*, supplier, item, display_quantity, unit, unit_cost, purchase_date,
                  number, user):
    item = item.__class__.objects.select_for_update().get(pk=item.pk)
    if not item.is_active or item.item_type == Item.ItemType.FINISHED:
        raise ValueError("Only active raw-material or packaging items can be purchased.")
    try:
        display_quantity = Decimal(str(display_quantity))
        unit_cost = Decimal(str(unit_cost))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid quantity and unit cost.")
    if not display_quantity.is_finite() or display_quantity <= Decimal("0"):
        raise ValueError("Quantity must be greater than zero.")
    if not unit_cost.is_finite() or unit_cost < Decimal("0"):
        raise ValueError("Unit cost cannot be negative")
    quantity = to_item_base(display_quantity, item, unit)
    multiplier = item_unit_multiplier(item, unit)
    unit_cost_base = (unit_cost / multiplier).quantize(Decimal("0.00000001"))
    purchase = Purchase.objects.create(
        number=number,
        supplier=supplier,
        purchase_date=purchase_date,
        posted=True,
    )
    PurchaseLine.objects.create(
        purchase=purchase,
        item=item,
        quantity=quantity,
        unit=item.base_unit,
        display_quantity=display_quantity,
        display_unit=unit,
        unit_cost=unit_cost,
    )
    create_movement(
        item=item,
        qty_in=quantity,
        movement_type=StockMovement.MovementType.PURCHASE,
        source_doc_type="PURCHASE",
        source_doc_id=purchase.id,
        user=user,
        unit_cost=unit_cost,
        unit_cost_base=unit_cost_base,
    )
    return purchase
