from decimal import Decimal
from django.db import transaction
from core.models import Purchase, PurchaseLine, StockMovement
from core.services.units import to_base
from core.services.stock import create_movement


@transaction.atomic
def post_purchase(*, supplier, item, display_quantity, unit, unit_cost, purchase_date,
                  number, user):
    item = item.__class__.objects.select_for_update().get(pk=item.pk)
    quantity = to_base(display_quantity, unit)
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
    )
    return purchase
