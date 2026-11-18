from decimal import Decimal, InvalidOperation

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
