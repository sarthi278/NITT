from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import (
    AuditLog,
    CompanySettings,
    Customer,
    Item,
    Order,
    Product,
    StockMovement,
    Supplier,
)
from core.services.dispatch import dispatch_invoice
from core.services.invoicing import create_invoice
from core.services.orders import approve_order, create_order
from core.services.payments import invoice_outstanding, record_payment
from core.services.production import post_production_run
from core.services.pdf import build_invoice_pdf
from core.services.purchasing import post_purchase
from core.services.reports import build_report
from core.services.stock import balance


class BenchmarkWorkflowTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin")
        self.sales_a = User.objects.get(username="sales-a")
        self.sales_b = User.objects.get(username="sales-b")
        self.retailer = Customer.objects.get(name="Retailer A")
        self.distributor = Customer.objects.get(name="Distributor B")
        self.product = Product.objects.get(sku="MUR-200")

    def test_internship_benchmark_from_purchase_to_reports(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        oil = Item.objects.get(sku="RM-OIL")
        spice = Item.objects.get(sku="RM-SPICE")
        pouch = Item.objects.get(sku="PK-POUCH")
        carton = Item.objects.get(sku="PK-CARTON")
        finished = Item.objects.get(sku="FG-MURUKKU-200")
        supplier = Supplier.objects.get(name="Benchmark Flour Supplier")
        product_500 = Product.objects.get(sku="MUR-500")
        self.assertEqual(product_500.bom.batch_yield_packets, 80)
        self.assertEqual(
            product_500.bom.lines.get(item__sku="PK-POUCH-500").qty_per_batch,
            Decimal("80.000"),
        )
        self.assertEqual(
            product_500.bom.lines.get(item__sku="PK-CARTON").qty_per_batch,
            Decimal("4.000"),
        )

        purchase = post_purchase(
            supplier=supplier,
            item=flour,
            display_quantity=Decimal("60"),
            unit="kg",
            unit_cost=Decimal("45"),
            purchase_date=date(2026, 4, 1),
            number="PUR-BENCHMARK-E2E",
            user=self.admin,
        )
        self.assertEqual(purchase.lines.get().display_quantity, Decimal("60"))
        self.assertEqual(balance(flour), Decimal("160000"))
        CompanySettings.objects.update_or_create(
            pk=1,
            defaults={
                "name": "NITT Foods",
                "address": "Tiruchirappalli",
                "gstin": "33AAAAA0000A1Z5",
                "state": "Tamil Nadu",
                "phone": "0431-2500000",
                "email": "accounts@example.test",
            },
        )

        bom_lines = {
            line.item.sku: line.pk
            for line in self.product.bom.lines.select_related("item")
        }
        run, consumptions, yield_variance, yield_flagged = post_production_run(
            product=self.product,
            batch_count=Decimal("1"),
            actual_good_output=188,
            actual_quantities={
                bom_lines["RM-FLOUR"]: Decimal("40"),
                bom_lines["RM-OIL"]: Decimal("10.5"),
                bom_lines["RM-SPICE"]: Decimal("1.2"),
                bom_lines["PK-POUCH"]: Decimal("194"),
                bom_lines["PK-CARTON"]: Decimal("9"),
            },
            wastage_quantities={bom_lines["PK-POUCH"]: Decimal("6")},
            run_date=date(2026, 4, 1),
            number="RUN-BENCHMARK-E2E",
            user=self.admin,
            labour_cost=Decimal("1200"),
            power_gas_cost=Decimal("600"),
        )
        self.assertEqual(run.expected_output, 200)
        self.assertEqual(run.actual_good_output, 188)
        self.assertEqual(yield_variance, Decimal("-6.000"))
        self.assertTrue(yield_flagged)
        self.assertEqual(run.material_cost, Decimal("4055.00"))
        self.assertEqual(run.total_cost, Decimal("5855.00"))
        self.assertEqual(run.cost_per_packet, Decimal("31.14"))
        self.assertEqual(run.margin_per_packet, Decimal("18.86"))
        self.assertTrue(
            next(row for row in consumptions if row["item"] == oil)["flagged"]
        )
        self.assertEqual(balance(flour), Decimal("120000"))
        self.assertEqual(balance(oil), Decimal("19500"))
        self.assertEqual(balance(spice), Decimal("3800"))
        self.assertEqual(balance(pouch), Decimal("306"))
        self.assertEqual(balance(carton), Decimal("21"))
        self.assertEqual(balance(finished), Decimal("238"))

        retailer_order, created = create_order(
            customer=self.retailer,
            created_by=self.sales_a,
            lines=[{"product": self.product, "quantity": Decimal("150")}],
            client_uuid="28e9b9e6-9a65-47c6-8f39-e2c026ab8610",
        )
        self.assertTrue(created)
        approve_order(
            order=retailer_order,
            approved_quantities={retailer_order.lines.get().pk: Decimal("150")},
            approved_by=self.admin,
        )
        retailer_invoice = create_invoice(
            order=retailer_order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        self.assertEqual(retailer_invoice.number, "2026-27/0001")
        self.assertEqual(retailer_invoice.taxable_total, Decimal("7500.00"))
        self.assertEqual(retailer_invoice.cgst_total, Decimal("187.50"))
        self.assertEqual(retailer_invoice.sgst_total, Decimal("187.50"))
        self.assertEqual(retailer_invoice.total, Decimal("7875.00"))
        self.assertEqual(retailer_invoice.company_name, "NITT Foods")
        self.assertEqual(retailer_invoice.company_address, "Tiruchirappalli")
        self.assertEqual(retailer_invoice.company_gstin, "33AAAAA0000A1Z5")
        self.assertTrue(build_invoice_pdf(retailer_invoice).startswith(b"%PDF-"))
        dispatch_invoice(
            invoice=retailer_invoice,
            dispatched_by=self.admin,
            dispatch_date=date(2026, 6, 1),
        )
        self.assertEqual(balance(finished), Decimal("88"))
        self.assertTrue(StockMovement.objects.filter(
            source_doc_type="DISPATCH",
            qty_out=Decimal("150"),
        ).exists())

        distributor_order, _ = create_order(
            customer=self.distributor,
            created_by=self.sales_b,
            lines=[{"product": self.product, "quantity": Decimal("120")}],
            client_uuid="5f7748e8-4851-46df-9d99-a166084ff92b",
        )
        with self.assertRaisesMessage(ValueError, "short by 32"):
            approve_order(
                order=distributor_order,
                approved_quantities={
                    distributor_order.lines.get().pk: Decimal("120")
                },
                approved_by=self.admin,
            )
        self.assertEqual(distributor_order.status, Order.Status.SUBMITTED)
        approve_order(
            order=distributor_order,
            approved_quantities={
                distributor_order.lines.get().pk: Decimal("88")
            },
            approved_by=self.admin,
        )
        distributor_invoice = create_invoice(
            order=distributor_order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 2),
        )
        self.assertEqual(distributor_invoice.taxable_total, Decimal("4400.00"))
        self.assertEqual(distributor_invoice.igst_total, Decimal("220.00"))
        self.assertEqual(distributor_invoice.cgst_total, Decimal("0.00"))
        self.assertEqual(distributor_invoice.sgst_total, Decimal("0.00"))

        record_payment(
            invoice=retailer_invoice,
            amount=Decimal("4000"),
            payment_date=date(2026, 6, 3),
            method="UPI",
            reference="BENCHMARK",
            received_by=self.admin,
        )
        self.assertEqual(invoice_outstanding(retailer_invoice), Decimal("3875.00"))

        _, _, sales_rows = build_report(
            "sales", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(len(sales_rows), 2)
        _, _, purchase_rows = build_report(
            "purchases", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(purchase_rows[0][4], Decimal("60.000"))
        _, _, production_rows = build_report(
            "production", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(len(production_rows), 5)
        self.assertEqual(
            production_rows[0][-4:],
            (
                Decimal("5855.00"),
                Decimal("31.14"),
                Decimal("18.86"),
                Decimal("37.72"),
            ),
        )
        _, _, outstanding_rows = build_report(
            "outstanding", date(2026, 4, 1), date(2026, 6, 30)
        )
        outstanding_by_customer = {row[1]: row[-1] for row in outstanding_rows}
        self.assertEqual(outstanding_by_customer["Retailer A"], Decimal("3875.00"))
        self.assertEqual(outstanding_by_customer["Distributor B"], Decimal("4620.00"))
        _, _, gst_rows = build_report(
            "gst", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(len(gst_rows), 2)
        self.assertEqual(
            {row[3]: row[8] for row in gst_rows},
            {
                "Retailer A": Decimal("0.00"),
                "Distributor B": Decimal("220.00"),
            },
        )
        _, _, stock_rows = build_report("stock")
        stock_by_sku = {row[1]: row[4] for row in stock_rows}
        self.assertEqual(stock_by_sku["RM-FLOUR"], Decimal("120000"))
        self.assertEqual(stock_by_sku["FG-MURUKKU-200"], Decimal("88"))
        _, _, payment_rows = build_report(
            "payments", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(payment_rows[0][5], Decimal("4000.00"))
        _, _, hsn_rows = build_report(
            "hsn", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual(hsn_rows[0][1], Decimal("238.000"))
        self.assertEqual(hsn_rows[0][2], Decimal("11900.00"))
        self.assertEqual(hsn_rows[0][-1], Decimal("595.00"))
        self.assertTrue(AuditLog.objects.filter(
            entity_type="Order",
            entity_id=retailer_order.pk,
            action="LINES_CREATED",
            source_doc_type="ORDER",
            source_doc_id=retailer_order.pk,
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity_type="ProductionRun",
            entity_id=run.pk,
            action="POST",
            user=self.admin,
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity_type="Invoice",
            entity_id=retailer_invoice.pk,
            action="POST",
            source_doc_type="ORDER",
            source_doc_id=retailer_order.pk,
        ).exists())
