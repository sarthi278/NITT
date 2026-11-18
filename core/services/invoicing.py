from collections import defaultdict
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.db import transaction

from core.models import (
    CompanySettings,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    Order,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def financial_year(invoice_date, start_month):
    start_year = invoice_date.year if invoice_date.month >= start_month else invoice_date.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _round_money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _allocate_tax(total_tax, lines):
    taxable_total = sum((line["taxable"] for line in lines), ZERO)
    allocations = {line["order_line"].pk: ZERO for line in lines}
    if taxable_total == ZERO or total_tax == ZERO:
        return allocations

    shares = []
    allocated = ZERO
    for line in lines:
        exact = total_tax * line["taxable"] / taxable_total
        rounded_down = exact.quantize(CENT, rounding=ROUND_DOWN)
        allocated += rounded_down
        shares.append((
            exact - rounded_down,
            line["order_line"].pk,
            rounded_down,
        ))

    remaining_cents = int(((total_tax - allocated) / CENT).to_integral_value())
    shares.sort(key=lambda row: (-row[0], row[1]))
    for index, (_, line_id, amount) in enumerate(shares):
        allocations[line_id] = amount + (CENT if index < remaining_cents else ZERO)
    return allocations


@transaction.atomic
def create_invoice(*, order, posted_by, invoice_date):
    order = Order.objects.select_for_update().select_related("customer").get(pk=order.pk)
    if order.status != Order.Status.APPROVED:
        raise ValueError("Only approved orders can be invoiced.")
    if hasattr(order, "invoice"):
        raise ValueError("This order already has an invoice.")

    settings, _ = CompanySettings.objects.get_or_create(
        pk=1,
        defaults={"name": "NIT Demo Snacks", "state": "Tamil Nadu", "fy_start_month": 4},
    )
    settings = CompanySettings.objects.select_for_update().get(pk=settings.pk)
    fy = financial_year(invoice_date, settings.fy_start_month)
    sequence, _ = InvoiceSequence.objects.get_or_create(financial_year=fy)
    sequence = InvoiceSequence.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    number = f"{fy}/{sequence.last_number:04d}"

    order_lines = list(
        order.lines.select_related("product").filter(approved_quantity__gt=ZERO).order_by("pk")
    )
    if not order_lines:
        raise ValueError("The order has no approved quantities to invoice.")

    same_state = order.customer.state.strip().casefold() == settings.state.strip().casefold()
    line_data = []
    grouped = defaultdict(list)
    for order_line in order_lines:
        taxable = _round_money(order_line.approved_quantity * order_line.unit_price)
        row = {
            "order_line": order_line,
            "product": order_line.product,
            "quantity": order_line.approved_quantity,
            "unit_price": order_line.unit_price,
            "taxable": taxable,
            "gst_rate": order_line.product.gst_rate,
            "cgst": ZERO,
            "sgst": ZERO,
            "igst": ZERO,
        }
        grouped[order_line.product.gst_rate].append(row)
        line_data.append(row)

    cgst_total = ZERO
    sgst_total = ZERO
    igst_total = ZERO
    for rate, rows in grouped.items():
        taxable_for_rate = sum((row["taxable"] for row in rows), ZERO)
        invoice_tax = _round_money(taxable_for_rate * rate / Decimal("100"))
        if same_state:
            cgst_for_rate = _round_money(invoice_tax / Decimal("2"))
            sgst_for_rate = invoice_tax - cgst_for_rate
            cgst_allocations = _allocate_tax(cgst_for_rate, rows)
            sgst_allocations = _allocate_tax(sgst_for_rate, rows)
            for row in rows:
                row["cgst"] = cgst_allocations[row["order_line"].pk]
                row["sgst"] = sgst_allocations[row["order_line"].pk]
            cgst_total += cgst_for_rate
            sgst_total += sgst_for_rate
        else:
            allocations = _allocate_tax(invoice_tax, rows)
            for row in rows:
                row["igst"] = allocations[row["order_line"].pk]
            igst_total += invoice_tax

    taxable_total = sum((row["taxable"] for row in line_data), ZERO)
    invoice = Invoice.objects.create(
        number=number,
        financial_year=fy,
        order=order,
        customer=order.customer,
        invoice_date=invoice_date,
        customer_gstin=order.customer.gstin,
        company_gstin=settings.gstin,
        place_of_supply=order.customer.state,
        taxable_total=taxable_total,
        cgst_total=cgst_total,
        sgst_total=sgst_total,
        igst_total=igst_total,
        total=taxable_total + cgst_total + sgst_total + igst_total,
        posted_by=posted_by,
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            order_line=row["order_line"],
            product=row["product"],
            product_name=row["product"].name,
            hsn_code=row["product"].hsn_code,
            quantity=row["quantity"],
            unit_price=row["unit_price"],
            taxable_value=row["taxable"],
            gst_rate=row["gst_rate"],
            cgst=row["cgst"],
            sgst=row["sgst"],
            igst=row["igst"],
            total=row["taxable"] + row["cgst"] + row["sgst"] + row["igst"],
        )
        for row in line_data
    ])
    order.status = Order.Status.INVOICED
    order.save(update_fields=["status", "updated_at"])
    return invoice
