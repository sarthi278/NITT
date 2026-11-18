from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import (
    Customer,
    CustomerPrice,
    Invoice,
    Item,
    Order,
    PriceList,
    PriceListItem,
    Product,
    StockBatch,
    StockMovement,
    Supplier,
)
from core.services.credit_notes import issue_credit_note
from core.services.dispatch import dispatch_invoice
from core.services.invoicing import create_invoice
from core.services.orders import approve_order, create_order
from core.services.payments import invoice_outstanding, record_payment
from core.services.production import post_production_run
from core.services.purchasing import post_purchase
from core.services.stock import balance, create_movement


class OrderInvoiceDispatchTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin")
        self.sales_a = User.objects.get(username="sales-a")
        self.sales_b = User.objects.get(username="sales-b")
        self.retailer = Customer.objects.get(name="Retailer A")
        self.distributor = Customer.objects.get(name="Distributor B")
        self.product = Product.objects.get(sku="MUR-200")

    def build_benchmark_stock(self):
        supplier = Supplier.objects.first()
        post_purchase(
            supplier=supplier,
            item=Item.objects.get(sku="RM-FLOUR"),
            display_quantity=Decimal("60"),
            unit="kg",
            unit_cost=Decimal("45"),
            purchase_date=date(2026, 4, 1),
            number="PUR-SALES",
            user=self.admin,
        )
        line_ids = {
            line.item.sku: line.pk
            for line in self.product.bom.lines.select_related("item")
        }
        post_production_run(
            product=self.product,
            batch_count=Decimal("1"),
            actual_good_output=188,
            actual_quantities={
                line_ids["RM-FLOUR"]: Decimal("40"),
                line_ids["RM-OIL"]: Decimal("10.5"),
                line_ids["RM-SPICE"]: Decimal("1.2"),
                line_ids["PK-POUCH"]: Decimal("194"),
                line_ids["PK-CARTON"]: Decimal("9"),
            },
            wastage_quantities={line_ids["PK-POUCH"]: Decimal("6")},
            run_date=date(2026, 4, 1),
            number="RUN-SALES",
            user=self.admin,
        )

    def submit_order(self, customer, salesman, quantity, client_uuid=None):
        return create_order(
            customer=customer,
            created_by=salesman,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            client_uuid=client_uuid or uuid4(),
        )

    def test_salesman_order_is_scoped_and_submission_is_idempotent(self):
        submission_id = uuid4()
        order, created = self.submit_order(
            self.retailer,
            self.sales_a,
            "150",
            client_uuid=submission_id,
        )
        duplicate, duplicate_created = self.submit_order(
            self.retailer,
            self.sales_a,
            "150",
            client_uuid=submission_id,
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(order.pk, duplicate.pk)
        self.assertEqual(order.lines.count(), 1)

        with self.assertRaisesMessage(ValueError, "assigned customers"):
            self.submit_order(self.distributor, self.sales_a, "120")

    def test_customer_price_overrides_price_list_and_default_price(self):
        price_list = PriceList.objects.create(name="Distributor list")
        PriceListItem.objects.create(
            price_list=price_list,
            product=self.product,
            unit_price=Decimal("45"),
        )
        self.distributor.price_list = price_list
        self.distributor.save(update_fields=["price_list"])

        listed_order, _ = self.submit_order(self.distributor, self.sales_b, "10")
        self.assertEqual(listed_order.lines.get().unit_price, Decimal("45.00"))

        CustomerPrice.objects.create(
            customer=self.distributor,
            product=self.product,
            unit_price=Decimal("42"),
        )
        customer_order, _ = self.submit_order(self.distributor, self.sales_b, "10")
        self.assertEqual(customer_order.lines.get().unit_price, Decimal("42.00"))

    def test_sales_order_form_only_lists_assigned_customers(self):
        self.client.force_login(self.sales_a)
        response = self.client.get("/orders/new/")
        self.assertContains(response, "Retailer A")
        self.assertNotContains(response, "Distributor B")
        self.assertContains(response, "order-sync.js")

        response = self.client.post("/orders/new/", {
            "customer": self.retailer.pk,
            "client_uuid": str(uuid4()),
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.pk,
            "lines-0-quantity": "12",
            "lines-1-product": "",
            "lines-1-quantity": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Order.objects.filter(created_by=self.sales_a, customer=self.retailer).count(),
            1,
        )

    def test_tamil_nadu_invoice_tax_and_dispatch(self):
        self.build_benchmark_stock()
        order, _ = self.submit_order(self.retailer, self.sales_a, "150")
        line = order.lines.get()
        approve_order(
            order=order,
            approved_quantities={line.pk: Decimal("150")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        self.assertEqual(invoice.number, "2026-27/0001")
        self.assertEqual(invoice.taxable_total, Decimal("7500.00"))
        self.assertEqual(invoice.cgst_total, Decimal("187.50"))
        self.assertEqual(invoice.sgst_total, Decimal("187.50"))
        self.assertEqual(invoice.igst_total, Decimal("0.00"))
        self.assertEqual(invoice.total, Decimal("7875.00"))

        dispatch, created = dispatch_invoice(
            invoice=invoice,
            dispatched_by=self.admin,
            dispatch_date=date(2026, 6, 1),
        )
        self.assertTrue(created)
        self.assertEqual(
            balance(Item.objects.get(sku="FG-MURUKKU-200")),
            Decimal("88.000"),
        )
        duplicate, duplicate_created = dispatch_invoice(
            invoice=invoice,
            dispatched_by=self.admin,
            dispatch_date=date(2026, 6, 1),
        )
        self.assertFalse(duplicate_created)
        self.assertEqual(dispatch.pk, duplicate.pk)

    def test_interstate_shortage_can_be_partially_approved_and_taxed(self):
        self.build_benchmark_stock()
        first_order, _ = self.submit_order(self.retailer, self.sales_a, "150")
        approve_order(
            order=first_order,
            approved_quantities={first_order.lines.get().pk: Decimal("150")},
            approved_by=self.admin,
        )
        first_invoice = create_invoice(
            order=first_order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        dispatch_invoice(
            invoice=first_invoice,
            dispatched_by=self.admin,
            dispatch_date=date(2026, 6, 1),
        )

        second_order, _ = self.submit_order(self.distributor, self.sales_b, "120")
        second_line = second_order.lines.get()
        with self.assertRaisesMessage(ValueError, "short by 32 packets"):
            approve_order(
                order=second_order,
                approved_quantities={second_line.pk: Decimal("120")},
                approved_by=self.admin,
            )
        self.assertEqual(second_order.status, Order.Status.SUBMITTED)

        approve_order(
            order=second_order,
            approved_quantities={second_line.pk: Decimal("88")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=second_order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 2),
        )
        self.assertEqual(invoice.taxable_total, Decimal("4400.00"))
        self.assertEqual(invoice.igst_total, Decimal("220.00"))
        self.assertEqual(invoice.cgst_total, Decimal("0.00"))
        self.assertEqual(invoice.sgst_total, Decimal("0.00"))

    def test_approval_counts_multiple_lines_for_the_same_finished_item(self):
        self.build_benchmark_stock()
        order, _ = create_order(
            customer=self.retailer,
            created_by=self.sales_a,
            lines=[
                {"product": self.product, "quantity": Decimal("150")},
                {"product": self.product, "quantity": Decimal("100")},
            ],
            client_uuid=uuid4(),
        )
        approved_quantities = {
            line.pk: line.quantity for line in order.lines.all()
        }

        with self.assertRaisesMessage(ValueError, "short by 12"):
            approve_order(
                order=order,
                approved_quantities=approved_quantities,
                approved_by=self.admin,
            )
        self.assertEqual(order.status, Order.Status.SUBMITTED)

    def test_selling_salesman_cannot_view_another_users_order(self):
        order, _ = self.submit_order(self.retailer, self.sales_a, "10")
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
        self.client.force_login(self.sales_b)
        response = self.client.get(f"/invoices/{invoice.pk}/")
        self.assertEqual(response.status_code, 403)

    def test_payment_reduces_invoice_outstanding(self):
        self.build_benchmark_stock()
        order, _ = self.submit_order(self.retailer, self.sales_a, "150")
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("150")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        payment = record_payment(
            invoice=invoice,
            amount=Decimal("4000"),
            payment_date=date(2026, 6, 2),
            method="UPI",
            reference="UPI-TEST",
            received_by=self.admin,
        )
        self.assertEqual(payment.amount, Decimal("4000.00"))
        self.assertEqual(invoice_outstanding(invoice), Decimal("3875.00"))
        with self.assertRaisesMessage(ValueError, "exceed"):
            record_payment(
                invoice=invoice,
                amount=Decimal("4000"),
                payment_date=date(2026, 6, 2),
                method="UPI",
                received_by=self.admin,
            )

    def test_full_credit_note_reverses_dispatched_stock(self):
        self.build_benchmark_stock()
        order, _ = self.submit_order(self.retailer, self.sales_a, "150")
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("150")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=date(2026, 6, 1),
        )
        dispatch_invoice(
            invoice=invoice,
            dispatched_by=self.admin,
            dispatch_date=date(2026, 6, 1),
        )
        credit_note = issue_credit_note(
            invoice=invoice,
            issue_date=date(2026, 6, 2),
            reason="Order returned",
            created_by=self.admin,
        )
        invoice.refresh_from_db()
        finished_item = Item.objects.get(sku="FG-MURUKKU-200")
        self.assertEqual(balance(finished_item), Decimal("238"))
        self.assertEqual(invoice_outstanding(invoice), Decimal("0.00"))
        self.assertEqual(invoice.status, Invoice.Status.CREDITED)
        reversal = StockMovement.objects.get(
            source_doc_type="CREDIT_NOTE",
            source_doc_id=credit_note.pk,
        )
        self.assertEqual(reversal.qty_in, Decimal("150"))

    def test_dispatch_uses_first_expiring_unexpired_batch(self):
        finished_item = Item.objects.get(sku="FG-MURUKKU-200")
        today = date.today()
        expired = StockBatch.objects.create(
            item=finished_item,
            batch_no="EXPIRED-LOT",
            expiry_date=date.fromordinal(today.toordinal() - 1),
        )
        soonest = StockBatch.objects.create(
            item=finished_item,
            batch_no="SOONEST-LOT",
            expiry_date=date.fromordinal(today.toordinal() + 15),
        )
        for batch in (expired, soonest):
            create_movement(
                item=finished_item,
                batch=batch,
                qty_in=Decimal("10"),
                unit_cost_base=Decimal("50"),
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                source_doc_type="TEST_BATCH",
                source_doc_id=batch.pk,
                user=self.admin,
            )

        order, _ = self.submit_order(self.retailer, self.sales_a, "10")
        approve_order(
            order=order,
            approved_quantities={order.lines.get().pk: Decimal("10")},
            approved_by=self.admin,
        )
        invoice = create_invoice(
            order=order,
            posted_by=self.admin,
            invoice_date=today,
        )
        dispatch, _ = dispatch_invoice(
            invoice=invoice,
            dispatched_by=self.admin,
            dispatch_date=today,
        )
        movement = StockMovement.objects.get(
            source_doc_type="DISPATCH",
            source_doc_id=dispatch.pk,
        )
        self.assertEqual(movement.batch, soonest)
        self.assertNotEqual(movement.batch, expired)
