from decimal import Decimal
from django.db.models import Sum
from core.models import StockMovement

ZERO = Decimal("0")


def balance(item):
    result = StockMovement.objects.filter(item=item).aggregate(
        qty_in=Sum("qty_in"),
        qty_out=Sum("qty_out"),
    )
    return (result["qty_in"] or ZERO) - (result["qty_out"] or ZERO)


def create_movement(*, item, qty_in=ZERO, qty_out=ZERO, movement_type,
                    source_doc_type, source_doc_id, user=None, unit_cost=ZERO,
                    batch=None):
    if qty_in != ZERO and qty_out != ZERO:
        raise ValueError("A movement cannot have qty_in and qty_out together")
    if qty_in < ZERO or qty_out < ZERO:
        raise ValueError("Movement quantities cannot be negative")
    return StockMovement.objects.create(
        item=item,
        batch=batch,
        qty_in=qty_in,
        qty_out=qty_out,
        unit_cost=unit_cost,
        movement_type=movement_type,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        user=user,
    )
