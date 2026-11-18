from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from core.models import (
    BOM,
    Item,
    ProductionConsumption,
    ProductionRun,
    StockMovement,
)
from core.services.inventory_valuation import weighted_average_rate
from core.services.stock import balance, create_movement
from core.services.units import item_quantity_to_base

ZERO = Decimal("0")
CENT = Decimal("0.01")


def variance_percent(actual, planned):
    if planned == ZERO:
        return ZERO
    return (
        (Decimal(str(actual)) - Decimal(str(planned)))
        / Decimal(str(planned))
        * Decimal("100")
    ).quantize(Decimal("0.001"))


def _decimal_amount(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"Enter a valid {label}.")
    if not amount.is_finite() or amount < ZERO:
        raise ValueError(f"{label.capitalize()} cannot be negative.")
    return amount


def _form_value(values, key, label):
    value = values.get(key, values.get(str(key)))
    if value is None:
        raise ValueError(f"Enter the {label} for every BOM line.")
    return value


@transaction.atomic
def post_production_run(
    *,
    product,
    batch_count,
    actual_good_output,
    actual_quantities,
    run_date,
    number,
    user,
    damaged_pouches=None,
    wastage_quantities=None,
    labour_cost=ZERO,
    power_gas_cost=ZERO,
):
    product = product.__class__.objects.select_for_update().select_related(
        "finished_item"
    ).get(pk=product.pk)
    if not product.finished_item_id:
        raise ValueError(f"Finished-stock item is not configured for {product.name}.")
    if not product.finished_item.is_active:
        raise ValueError(f"Finished-stock item for {product.name} is inactive.")
    try:
        bom = product.bom
    except BOM.DoesNotExist:
        raise ValueError(f"No BOM is configured for {product.name}.")
    bom_lines = list(bom.lines.select_related("item").order_by("pk"))
    if not bom_lines:
        raise ValueError(f"The BOM for {product.name} has no material lines.")

    try:
        batches = Decimal(str(batch_count))
        good_output_decimal = Decimal(str(actual_good_output))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid batch count and good output.")
    if not batches.is_finite() or batches <= ZERO:
        raise ValueError("Batch count must be greater than zero.")
    normalized_batches = batches.quantize(Decimal("0.001"))
    if batches != normalized_batches or normalized_batches <= ZERO:
        raise ValueError("Batch count must be a positive number with up to 3 decimals.")
    if not number.strip():
        raise ValueError("Production run number is required.")
    if (
        not good_output_decimal.is_finite()
        or good_output_decimal != good_output_decimal.to_integral_value()
    ):
        raise ValueError("Good output must be a whole number of packets.")
    good_output = int(good_output_decimal)
    if good_output <= 0:
        raise ValueError("Good output must be greater than zero.")

    labour = _decimal_amount(labour_cost, "labour cost")
    power_gas = _decimal_amount(power_gas_cost, "gas and power cost")
    expected_output = int(
        (Decimal(bom.batch_yield_packets) * batches).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    if expected_output <= 0:
        raise ValueError("Expected output must be greater than zero.")

    item_ids = {line.item_id for line in bom_lines}
    item_ids.add(product.finished_item_id)
    locked_items = {
        item.pk: item
        for item in Item.objects.select_for_update().filter(pk__in=sorted(item_ids))
    }

    consumptions = []
    required_by_item = {}
    for line in bom_lines:
        item = locked_items[line.item_id]
        if not item.is_active:
            raise ValueError(f"BOM item {item.name} is inactive.")
        planned_display = line.qty_per_batch * batches
        planned = item_quantity_to_base(planned_display, item, line.unit)
        actual_display = _decimal_amount(
            _form_value(actual_quantities, line.pk, f"actual quantity for {item.name}"),
            f"actual quantity for {item.name}",
        )
        wasted_display = _decimal_amount(
            (wastage_quantities or {}).get(
                line.pk,
                (wastage_quantities or {}).get(str(line.pk), ZERO),
            ),
            f"wastage for {item.name}",
        )
        actual = item_quantity_to_base(actual_display, item, line.unit, allow_zero=True)
        wasted = item_quantity_to_base(wasted_display, item, line.unit, allow_zero=True)
        if wasted > actual:
            raise ValueError(f"Wastage for {item.name} cannot exceed actual consumption.")

        rate = weighted_average_rate(item)
        line_cost = (actual * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        variance = variance_percent(actual, planned)
        consumptions.append({
            "line": line,
            "item": item,
            "planned": planned,
            "actual": actual,
            "wasted": wasted,
            "rate": rate,
            "cost": line_cost,
            "variance_pct": variance,
            "flagged": abs(variance) > bom.tolerance_pct,
        })
        required_by_item[item.pk] = required_by_item.get(item.pk, ZERO) + actual

    for item_id, required in required_by_item.items():
        item = locked_items[item_id]
        if balance(item) < required:
            raise ValueError(
                f"Insufficient stock for {item.name}: need {required}, have {balance(item)}."
            )

    material_cost = sum((row["cost"] for row in consumptions), ZERO)
    overhead_cost = (labour + power_gas).quantize(CENT, rounding=ROUND_HALF_UP)
    total_cost = (material_cost + overhead_cost).quantize(CENT, rounding=ROUND_HALF_UP)
    cost_per_packet = (total_cost / Decimal(good_output)).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )
    margin = (product.selling_price - cost_per_packet).quantize(CENT)
    margin_pct = (
        (margin / product.selling_price * Decimal("100")).quantize(CENT)
        if product.selling_price > ZERO
        else ZERO
    )
    packaging_wastage = sum(
        (row["wasted"] for row in consumptions if row["item"].item_type == Item.ItemType.PACKAGING),
        ZERO,
    )
    if damaged_pouches is not None:
        declared_wastage = _decimal_amount(damaged_pouches, "damaged packaging total")
        if declared_wastage != packaging_wastage:
            raise ValueError(
                "Damaged packaging total must match the BOM-line wastage entries."
            )

    run = ProductionRun.objects.create(
        number=number,
        product=product,
        bom=bom,
        batch_count=normalized_batches,
        expected_output=expected_output,
        actual_good_output=good_output,
        damaged_pouches=int(packaging_wastage),
        labour_cost=labour,
        power_gas_cost=power_gas,
        material_cost=material_cost,
        overhead_cost=overhead_cost,
        total_cost=total_cost,
        cost_per_packet=cost_per_packet,
        margin_per_packet=margin,
        margin_pct=margin_pct,
        run_date=run_date,
        created_by=user,
    )

    consumption_results = []
    for row in consumptions:
        ProductionConsumption.objects.create(
            run=run,
            item=row["item"],
            planned_qty=row["planned"],
            actual_qty=row["actual"],
            wasted_qty=row["wasted"],
            variance_pct=row["variance_pct"],
            material_cost=row["cost"],
            flagged=row["flagged"],
        )
        consumption_results.append({
            "item": row["item"],
            "planned": row["planned"],
            "actual": row["actual"],
            "wasted": row["wasted"],
            "variance_pct": row["variance_pct"],
            "flagged": row["flagged"],
        })
        issued = row["actual"] - row["wasted"]
        if issued:
            create_movement(
                item=row["item"],
                qty_out=issued,
                unit_cost_base=row["rate"],
                movement_type=StockMovement.MovementType.ISSUE,
                source_doc_type="PRODUCTION_RUN",
                source_doc_id=run.pk,
                user=user,
            )
        if row["wasted"]:
            create_movement(
                item=row["item"],
                qty_out=row["wasted"],
                unit_cost_base=row["rate"],
                movement_type=StockMovement.MovementType.WASTE,
                source_doc_type="PRODUCTION_RUN",
                source_doc_id=run.pk,
                user=user,
            )

    finished_item = locked_items[product.finished_item_id]
    create_movement(
        item=finished_item,
        qty_in=Decimal(good_output),
        unit_cost=cost_per_packet,
        unit_cost_base=cost_per_packet,
        movement_type=StockMovement.MovementType.OUTPUT,
        source_doc_type="PRODUCTION_RUN",
        source_doc_id=run.pk,
        user=user,
    )

    yield_variance = variance_percent(good_output, expected_output)
    yield_flagged = abs(yield_variance) > bom.tolerance_pct
    return run, consumption_results, yield_variance, yield_flagged
