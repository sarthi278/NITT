from decimal import Decimal

from core.models import StockMovement

ZERO = Decimal("0")
RATE_QUANTUM = Decimal("0.00000001")


def weighted_average_rate(item):
    quantity = ZERO
    value = ZERO
    movements = StockMovement.objects.filter(item=item).order_by("timestamp", "id")

    for movement in movements:
        if movement.qty_in:
            quantity += movement.qty_in
            value += movement.qty_in * movement.unit_cost_base
        if movement.qty_out:
            if movement.qty_out > quantity:
                raise ValueError(f"Ledger stock is negative for {item.name}.")
            value -= movement.qty_out * movement.unit_cost_base
            quantity -= movement.qty_out
            if quantity == ZERO:
                value = ZERO

    if quantity <= ZERO:
        return ZERO
    return (value / quantity).quantize(RATE_QUANTUM)
