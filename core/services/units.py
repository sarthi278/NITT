from decimal import Decimal, InvalidOperation

from core.models import UnitConversion

DISPLAY_UNITS = {
    "g": Decimal("1"),
    "kg": Decimal("1000"),
    "ml": Decimal("1"),
    "L": Decimal("1000"),
    "litre": Decimal("1000"),
    "piece": Decimal("1"),
    "packet": Decimal("1"),
    "carton": Decimal("20"),
}


def to_base(quantity, unit):
    try:
        amount = Decimal(str(quantity))
        multiplier = DISPLAY_UNITS[unit]
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError("Unsupported unit or invalid quantity")
    return (amount * multiplier).quantize(Decimal("0.001"))


def from_base(quantity, unit):
    if unit not in DISPLAY_UNITS:
        raise ValueError("Unsupported unit")
    return (Decimal(str(quantity)) / DISPLAY_UNITS[unit]).quantize(Decimal("0.001"))


def item_unit_multiplier(item, unit):
    if unit == item.base_unit:
        return Decimal("1")

    conversion = UnitConversion.objects.filter(item=item, unit=unit).first()
    if conversion is None or conversion.to_base_multiplier <= Decimal("0"):
        raise ValueError("Select a unit configured for this item")
    return conversion.to_base_multiplier


def to_item_base(quantity, item, unit):
    try:
        amount = Decimal(str(quantity))
        multiplier = item_unit_multiplier(item, unit)
        if not amount.is_finite() or amount <= Decimal("0"):
            raise ValueError("Quantity must be greater than zero")
        base_quantity = (amount * multiplier).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid quantity")

    if base_quantity <= Decimal("0"):
        raise ValueError("Converted quantity must be greater than zero")
    return base_quantity
