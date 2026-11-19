from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import Customer, Payment, Product
from core.services.invoicing import create_invoice
from core.services.orders import approve_order, create_order
from core.services.payments import record_payment
from core.services.pdf import amount_in_words
from core.services.reports import build_report


class ReportAndPdfTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.accountant = User.objects.get(username="accountant")
        self.owner = User.objects.get(username="owner")
        self.admin = User.objects.get(username="admin")
        self.salesman = User.objects.get(username="sales-a")

    def test_csv_and_excel_stock_exports(self):
        self.client.force_login(self.accountant)
        csv_response = self.client.get("/reports/?report=stock&export=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(b"Flour", csv_response.content)

        excel_response = self.client.get("/reports/?report=stock&export=xlsx")
        self.assertEqual(excel_response.status_code, 200)
        self.assertTrue(excel_response.content.startswith(b"PK"))

    def test_report_pdf_is_a_valid_pdf_response(self):
        self.client.force_login(self.accountant)
        response = self.client.get("/reports/?report=stock&export=pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_invoice_pdf_is_scoped_to_the_salesman(self):
        customer = Customer.objects.get(name="Retailer A")
        product = Product.objects.get(sku="MUR-200")
        order, _ = create_order(
            customer=customer,
            created_by=self.salesman,
            lines=[{"product": product, "quantity": Decimal("10")}],
            client_uuid="ef8df645-2606-4f21-a85e-bd837b6dc110",
        )
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("10")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        self.client.force_login(self.salesman)
        response = self.client.get(f"/invoices/{invoice.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_owner_landing_page_shows_inventory_metrics(self):
        self.client.force_login(self.owner)
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Owner dashboard")
        self.assertContains(response, "Outstanding dues")
        self.assertContains(response, "Taxable sales")
        self.assertContains(response, "Good production")

    def test_owner_dashboard_metrics_use_live_sales_data(self):
        customer = Customer.objects.get(name="Retailer A")
        product = Product.objects.get(sku="MUR-200")
        order, _ = create_order(
            customer=customer,
            created_by=self.salesman,
            lines=[{"product": product, "quantity": Decimal("2")}],
            client_uuid="7e43f87d-4c64-4e78-b3d6-a9a7ef361d42",
        )
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("2")},
            approved_by=self.admin,
        )
        create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=timezone.localdate(),
        )

        self.client.force_login(self.owner)
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["today_sales"], Decimal("100.00"))
        self.assertEqual(response.context["monthly_revenue"], Decimal("100.00"))

    def test_invoice_amount_in_words_uses_indian_numbering_and_paise(self):
        self.assertEqual(
            amount_in_words(Decimal("7875.00")),
            "Rupees Seven Thousand Eight Hundred Seventy Five Only",
        )
        self.assertEqual(
            amount_in_words(Decimal("123456.78")),
            "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six "
            "and Seventy Eight Paise Only",
        )

    def test_salesman_cannot_access_finance_reports(self):
        self.client.force_login(self.salesman)
        self.assertEqual(self.client.get("/reports/").status_code, 403)

    def test_outstanding_ageing_and_hsn_report_values(self):
        order, _ = create_order(
            customer=Customer.objects.get(name="Retailer A"),
            created_by=self.salesman,
            lines=[{
                "product": Product.objects.get(sku="MUR-200"),
                "quantity": Decimal("10"),
            }],
            client_uuid="49ee236a-dad8-4a43-8733-834a28f47fd7",
        )
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("10")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        record_payment(
            invoice=invoice,
            amount=Decimal("100"),
            payment_date=date(2026, 6, 10),
            method=Payment.Method.BANK,
            received_by=self.admin,
        )

        _, _, outstanding_rows = build_report(
            "outstanding",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 15),
        )
        self.assertEqual(outstanding_rows[0][4], "0-30")
        self.assertEqual(outstanding_rows[0][7], Decimal("425.00"))

        _, _, hsn_rows = build_report(
            "hsn",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )
        self.assertEqual(hsn_rows[0][0], "19059030")
        self.assertEqual(hsn_rows[0][1], Decimal("10.000"))

        _, _, tally_rows = build_report(
            "tally",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )
        self.assertEqual(
            sum((row[4] for row in tally_rows), Decimal("0")),
            sum((row[5] for row in tally_rows), Decimal("0")),
        )
