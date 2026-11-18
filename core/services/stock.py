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
                    unit_cost_base=ZERO, batch=None):
    if qty_in != ZERO and qty_out != ZERO:
        raise ValueError("A movement cannot have qty_in and qty_out together")
    if qty_in < ZERO or qty_out < ZERO:
        raise ValueError("Movement quantities cannot be negative")
    if unit_cost < ZERO or unit_cost_base < ZERO:
        raise ValueError("Movement costs cannot be negative")
    return StockMovement.objects.create(
        item=item,
        batch=batch,
        qty_in=qty_in,
        qty_out=qty_out,
        unit_cost=unit_cost,
        unit_cost_base=unit_cost_base,
        movement_type=movement_type,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        user=user,
    )


def require_stock(item, required_qty):
    available = balance(item)
    if available < required_qty:
        raise ValueError(
            f"Insufficient stock for {item.name}: need {required_qty}, have {available}"
        )
