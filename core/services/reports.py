from collections import defaultdict
from decimal import Decimal
from decimal import ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

from core.models import (
    CreditNote,
    Invoice,
    InvoiceLine,
    Item,
    Payment,
    ProductionRun,
    PurchaseLine,
    StockMovement,
)
from core.services.stock import balance

ZERO = Decimal("0")

REPORTS = (
    ("sales", "Sales register"),
    ("customers", "Customer-wise sales"),
    ("products", "Product-wise sales"),
    ("gst", "GST summary"),
    ("hsn", "HSN summary"),
    ("purchases", "Purchase register"),
    ("stock", "Stock summary"),
    ("ledger", "Stock ledger"),
    ("production", "Production variance"),
    ("payments", "Payments and collections"),
    ("outstanding", "Outstanding and ageing"),
    ("tally", "Tally-style vouchers"),
)


def _invoice_lines(start_date, end_date):
    lines = InvoiceLine.objects.select_related(
        "invoice__customer",
        "product",
    ).exclude(invoice__status=Invoice.Status.CREDITED)
    if start_date:
        lines = lines.filter(invoice__invoice_date__gte=start_date)
    if end_date:
        lines = lines.filter(invoice__invoice_date__lte=end_date)
    return lines.order_by("invoice__invoice_date", "invoice__number", "pk")


def build_report(report_type, start_date=None, end_date=None):
    if report_type == "sales":
        rows = [
            (
                line.invoice.number,
                line.invoice.invoice_date,
                line.invoice.customer.name,
                line.invoice.place_of_supply,
                line.product_name,
                line.hsn_code,
                line.quantity,
                line.taxable_value,
                line.cgst,
                line.sgst,
                line.igst,
                line.total,
            )
            for line in _invoice_lines(start_date, end_date)
        ]
        return "Sales register", (
            "Invoice", "Date", "Customer", "State", "Product", "HSN", "Qty",
            "Taxable", "CGST", "SGST", "IGST", "Total",
        ), rows

    if report_type in {"customers", "products"}:
        grouped = defaultdict(lambda: [ZERO, ZERO])
        for line in _invoice_lines(start_date, end_date):
            name = (
                line.invoice.customer.name
                if report_type == "customers"
                else line.product_name
            )
            grouped[name][0] += line.quantity
            grouped[name][1] += line.taxable_value
        rows = [
            (name, totals[0], totals[1])
            for name, totals in sorted(grouped.items())
        ]
        title = "Customer-wise sales" if report_type == "customers" else "Product-wise sales"
        return title, ("Name", "Quantity", "Taxable sales"), rows

    if report_type == "gst":
        invoices = Invoice.objects.exclude(status=Invoice.Status.CREDITED)
        if start_date:
            invoices = invoices.filter(invoice_date__gte=start_date)
        if end_date:
            invoices = invoices.filter(invoice_date__lte=end_date)
        rows = [
            (
                "B2B" if invoice.customer_gstin else "B2C",
                invoice.number,
                invoice.invoice_date,
                invoice.customer.name,
                invoice.customer_gstin,
                invoice.taxable_total,
                invoice.cgst_total,
                invoice.sgst_total,
                invoice.igst_total,
                invoice.total,
            )
            for invoice in invoices.select_related("customer").order_by("invoice_date", "number")
        ]
        return "GST summary", (
            "Type", "Invoice", "Date", "Customer", "GSTIN", "Taxable",
            "CGST", "SGST", "IGST", "Total",
        ), rows

    if report_type == "hsn":
        grouped = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO, ZERO])
        for line in _invoice_lines(start_date, end_date):
            totals = grouped[line.hsn_code]
            totals[0] += line.quantity
            totals[1] += line.taxable_value
            totals[2] += line.cgst
            totals[3] += line.sgst
            totals[4] += line.igst
        rows = [
            (hsn, *totals, sum(totals[2:], ZERO))
            for hsn, totals in sorted(grouped.items())
        ]
        return "HSN summary", (
            "HSN", "Quantity", "Taxable", "CGST", "SGST", "IGST", "Tax total",
        ), rows

    if report_type == "purchases":
        lines = PurchaseLine.objects.select_related(
            "purchase__supplier",
            "item",
        ).filter(purchase__posted=True)
        if start_date:
            lines = lines.filter(purchase__purchase_date__gte=start_date)
        if end_date:
            lines = lines.filter(purchase__purchase_date__lte=end_date)
        rows = [
            (
                line.purchase.number,
                line.purchase.purchase_date,
                line.purchase.supplier.name,
                line.item.name,
                line.display_quantity or line.quantity,
                line.display_unit or line.unit,
                line.unit_cost,
                line.quantity,
            )
            for line in lines.order_by("purchase__purchase_date", "purchase__number", "pk")
        ]
        return "Purchase register", (
            "Purchase", "Date", "Supplier", "Item", "Quantity", "Unit",
            "Unit cost", "Base quantity",
        ), rows

    if report_type == "stock":
        items = Item.objects.filter(is_active=True).order_by("item_type", "name")
        rows = []
        for item in items:
            current_balance = balance(item)
            rows.append((
                item.name,
                item.sku,
                item.get_item_type_display(),
                item.base_unit,
                current_balance,
                item.reorder_level,
                "Low" if current_balance < item.reorder_level else "OK",
            ))
        return "Stock summary", (
            "Item", "SKU", "Type", "Base unit", "Current stock", "Reorder level", "Status",
        ), rows

    if report_type == "ledger":
        movements = StockMovement.objects.select_related(
            "item",
            "user",
        )
        if start_date:
            movements = movements.filter(timestamp__date__gte=start_date)
        if end_date:
            movements = movements.filter(timestamp__date__lte=end_date)
        rows = [
            (
                movement.timestamp,
                movement.item.name,
                movement.movement_type,
                movement.qty_in,
                movement.qty_out,
                movement.item.base_unit,
                movement.source_doc_type,
                movement.source_doc_id,
                movement.user.username if movement.user else "",
            )
            for movement in movements.order_by("timestamp", "pk")
        ]
        return "Stock ledger", (
            "Timestamp", "Item", "Movement", "In", "Out", "Unit",
            "Source type", "Source ID", "User",
        ), rows

    if report_type == "production":
        runs = ProductionRun.objects.select_related("product").prefetch_related(
            "consumptions__item",
        )
        if start_date:
            runs = runs.filter(run_date__gte=start_date)
        if end_date:
            runs = runs.filter(run_date__lte=end_date)
        rows = []
        for run in runs.order_by("run_date", "number"):
            yield_variance = (
                (Decimal(run.actual_good_output - run.expected_output)
                 / Decimal(run.expected_output) * Decimal("100"))
                if run.expected_output
                else ZERO
            )
            for consumption in run.consumptions.all():
                rows.append((
                    run.number,
                    run.run_date,
                    run.product.name,
                    run.expected_output,
                    run.actual_good_output,
                    yield_variance,
                    consumption.item.name,
                    consumption.planned_qty,
                    consumption.actual_qty,
                    consumption.wasted_qty,
                    consumption.variance_pct,
                    consumption.flagged,
                    consumption.material_cost,
                ))
        return "Production variance", (
            "Run", "Date", "Product", "Expected output", "Good output",
            "Yield variance %", "Material", "Planned", "Actual", "Wastage",
            "Material variance %", "Flagged", "Material cost",
        ), rows

    if report_type == "payments":
        payments = Payment.objects.select_related("invoice__customer", "received_by")
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
        rows = [
            (
                payment.payment_date,
                payment.invoice.number,
                payment.invoice.customer.name,
                payment.get_method_display(),
                payment.reference,
                payment.amount,
                payment.received_by.username,
            )
            for payment in payments.order_by("payment_date", "pk")
        ]
        return "Payments and collections", (
            "Date", "Invoice", "Customer", "Method", "Reference", "Amount", "Received by",
        ), rows

    if report_type == "outstanding":
        invoices = Invoice.objects.select_related("customer").prefetch_related("payments")
        if start_date:
            invoices = invoices.filter(invoice_date__gte=start_date)
        if end_date:
            invoices = invoices.filter(invoice_date__lte=end_date)
        rows = []
        as_of = end_date or timezone.localdate()
        for invoice in invoices.order_by("invoice_date", "number"):
            if (
                invoice.status == Invoice.Status.CREDITED
                and CreditNote.objects.filter(
                    invoice=invoice,
                    issue_date__lte=as_of,
                ).exists()
            ):
                continue
            paid = invoice.payments.filter(payment_date__lte=as_of).aggregate(
                total=Sum("amount"),
            )["total"] or ZERO
            outstanding = max(invoice.total - paid, ZERO)
            if outstanding == ZERO:
                continue
            age_days = max((as_of - invoice.invoice_date).days, 0)
            if age_days <= 30:
                bucket = "0-30"
            elif age_days <= 60:
                bucket = "31-60"
            elif age_days <= 90:
                bucket = "61-90"
            else:
                bucket = "90+"
            rows.append((
                invoice.number,
                invoice.customer.name,
                invoice.invoice_date,
                age_days,
                bucket,
                invoice.total,
                paid,
                outstanding,
            ))
        return "Outstanding and ageing", (
            "Invoice", "Customer", "Date", "Age days", "Bucket",
            "Invoice total", "Paid", "Outstanding",
        ), rows

    if report_type == "tally":
        rows = []

        def append_voucher(voucher_type, voucher_no, voucher_date, entries):
            for ledger, debit, credit, narration in entries:
                rows.append((
                    voucher_type,
                    voucher_date,
                    voucher_no,
                    ledger,
                    debit,
                    credit,
                    narration,
                ))

        invoices = Invoice.objects.exclude(status=Invoice.Status.CREDITED)
        if start_date:
            invoices = invoices.filter(invoice_date__gte=start_date)
        if end_date:
            invoices = invoices.filter(invoice_date__lte=end_date)
        for invoice in invoices.select_related("customer").order_by("invoice_date", "number"):
            entries = [
                (invoice.customer.name, invoice.total, ZERO, invoice.number),
                ("Sales", ZERO, invoice.taxable_total, invoice.number),
            ]
            for ledger, amount in (
                ("Output CGST", invoice.cgst_total),
                ("Output SGST", invoice.sgst_total),
                ("Output IGST", invoice.igst_total),
            ):
                if amount:
                    entries.append((ledger, ZERO, amount, invoice.number))
            append_voucher("Sales", invoice.number, invoice.invoice_date, entries)

        purchases = PurchaseLine.objects.select_related(
            "purchase__supplier",
            "item",
        ).filter(purchase__posted=True)
        if start_date:
            purchases = purchases.filter(purchase__purchase_date__gte=start_date)
        if end_date:
            purchases = purchases.filter(purchase__purchase_date__lte=end_date)
        for line in purchases.order_by("purchase__purchase_date", "purchase__number", "pk"):
            quantity = line.display_quantity or line.quantity
            amount = (quantity * line.unit_cost).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            append_voucher(
                "Purchase",
                line.purchase.number,
                line.purchase.purchase_date,
                [
                    ("Purchases", amount, ZERO, line.item.name),
                    (line.purchase.supplier.name, ZERO, amount, line.item.name),
                ],
            )

        credit_notes = CreditNote.objects.select_related("invoice__customer")
        if start_date:
            credit_notes = credit_notes.filter(issue_date__gte=start_date)
        if end_date:
            credit_notes = credit_notes.filter(issue_date__lte=end_date)
        for note in credit_notes.order_by("issue_date", "number"):
            entries = [
                ("Sales Returns", note.taxable_total, ZERO, note.reason),
                (note.invoice.customer.name, ZERO, note.total, note.reason),
            ]
            for ledger, amount in (
                ("Output CGST", note.cgst_total),
                ("Output SGST", note.sgst_total),
                ("Output IGST", note.igst_total),
            ):
                if amount:
                    entries.append((ledger, amount, ZERO, note.reason))
            append_voucher("Credit Note", note.number, note.issue_date, entries)

        payments = Payment.objects.select_related("invoice__customer")
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
        for payment in payments.order_by("payment_date", "pk"):
            bank_ledger = (
                "Cash"
                if payment.method == Payment.Method.CASH
                else "Bank"
            )
            append_voucher(
                "Receipt",
                f"PMT-{payment.pk:06d}",
                payment.payment_date,
                [
                    (bank_ledger, payment.amount, ZERO, payment.invoice.number),
                    (payment.invoice.customer.name, ZERO, payment.amount, payment.invoice.number),
                ],
            )
        return "Tally-style vouchers", (
            "Voucher type", "Date", "Voucher no.", "Ledger", "Debit", "Credit", "Narration",
        ), rows

    raise ValueError("Select a supported report.")
