from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from core.models import ProductionRun, ProductionConsumption, StockMovement
from core.services.units import to_base
from core.services.stock import balance, create_movement

ZERO = Decimal("0")


def variance_percent(actual, planned):
    if planned == ZERO:
        return ZERO
    return ((Decimal(str(actual)) - Decimal(str(planned))) / Decimal(str(planned)) * Decimal("100")).quantize(Decimal("0.001"))


@transaction.atomic
def post_production_run(*, product, batch_count, actual_good_output,
                        damaged_pouches, labour_cost, power_gas_cost,
                        actual_quantities, run_date, number, user):
    if actual_good_output < 0 or damaged_pouches < 0:
        raise ValueError("Actual output and damaged pouches cannot be negative")

    product = type(product).objects.select_for_update().get(pk=product.pk)
    bom = product.bom
    batch_count = Decimal(str(batch_count))
    expected_output = int((bom.batch_yield_packets * batch_count).to_integral_value())

    run = ProductionRun.objects.create(
        number=number,
        product=product,
        batch_count=batch_count,
        expected_output=expected_output,
        actual_good_output=actual_good_output,
        damaged_pouches=damaged_pouches,
        labour_cost=labour_cost,
        power_gas_cost=power_gas_cost,
        run_date=run_date,
        created_by=user,
    )

    consumption_results = []
    bom_lines = list(bom.lines.select_related("item").all())
    for bom_line in bom_lines:
        type(bom_line.item).objects.select_for_update().get(pk=bom_line.item.pk)

    planned_by_sku = {}
    for bom_line in bom.lines.select_related("item").all():
        planned = to_base(bom_line.qty_per_batch * batch_count, bom_line.unit)
        key = bom_line.item.sku
        if key == "PK-POUCH":
            actual_display = Decimal(str(actual_quantities.get(key, Decimal(actual_good_output) + Decimal(damaged_pouches))))
        else:
            actual_display = Decimal(str(actual_quantities.get(key, planned)))
        actual = to_base(actual_display, bom_line.unit)

        if balance(bom_line.item) < actual:
            raise ValueError(f"Insufficient stock for {bom_line.item.name}")

        variance = variance_percent(actual, planned)
        flagged = abs(variance) > bom.tolerance_pct
        ProductionConsumption.objects.create(
            run=run,
            item=bom_line.item,
            planned_qty=planned,
            actual_qty=actual,
            variance_pct=variance,
            flagged=flagged,
        )
        consumption_results.append({
            "item": bom_line.item,
            "planned": planned,
            "actual": actual,
            "variance_pct": variance,
            "flagged": flagged,
        })
        create_movement(
            item=bom_line.item,
            qty_out=actual,
            movement_type=StockMovement.MovementType.ISSUE,
            source_doc_type="PRODUCTION_RUN",
            source_doc_id=run.id,
            user=user,
        )

    finished_item = bom.product.selling_item if hasattr(bom.product, "selling_item") else None
    try:
        from core.models import Item
        finished_item = Item.objects.get(sku="FG-MURUKKU-200")
    except Exception:
        pass
    if finished_item:
        create_movement(
            item=finished_item,
            qty_in=Decimal(actual_good_output),
            movement_type=StockMovement.MovementType.OUTPUT,
            source_doc_type="PRODUCTION_RUN",
            source_doc_id=run.id,
            user=user,
        )

    yield_variance = variance_percent(actual_good_output, expected_output)
    return run, consumption_results, yield_variance, abs(yield_variance) > bom.tolerance_pct
