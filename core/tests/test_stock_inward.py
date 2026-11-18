from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Item, StockInward, StockMovement
from core.services.stock import balance


class StockInwardTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin")
        self.client.login(username="admin", password="demo123")
        self.flour = Item.objects.get(sku="RM-FLOUR")
        self.entry_date = timezone.localdate()
        self.payload = {
            "submission_key": str(uuid.uuid4()),
            "item": str(self.flour.pk),
            "quantity": "2.000",
            "unit": "kg",
            "unit_cost": "45.00",
            "entry_date": self.entry_date.isoformat(),
            "batch_no": "FLOUR-LOT-1",
            "manufacturing_date": (self.entry_date - timedelta(days=1)).isoformat(),
            "expiry_date": (self.entry_date + timedelta(days=365)).isoformat(),
            "supplier": "",
            "remarks": "Receiving test",
        }

    def test_inward_converts_to_base_unit_and_posts_traceable_movement(self):
        response = self.client.post(reverse("stock_inward"), self.payload)

        self.assertRedirects(response, reverse("stock"))
        inward = StockInward.objects.get()
        movement = StockMovement.objects.get(source_doc_type="STOCK_INWARD")
        self.assertEqual(inward.base_quantity, Decimal("2000.000"))
        self.assertEqual(movement.qty_in, Decimal("2000.000"))
        self.assertEqual(movement.source_doc_id, inward.pk)
        self.assertEqual(movement.batch, inward.batch)
        self.assertEqual(balance(self.flour), Decimal("102000.000"))

    def test_form_shows_item_metadata_and_configured_units(self):
        response = self.client.get(reverse("stock_inward"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Item SKU")
        self.assertContains(response, "Item type")
        self.assertContains(response, '"kg"')
        self.assertContains(response, "Raw Material")

    def test_repeated_submission_token_does_not_add_stock_twice(self):
        self.client.post(reverse("stock_inward"), self.payload)
        response = self.client.post(reverse("stock_inward"), self.payload)

        self.assertRedirects(response, reverse("stock"))
        self.assertEqual(StockInward.objects.count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(source_doc_type="STOCK_INWARD").count(),
            1,
        )
        self.assertEqual(balance(self.flour), Decimal("102000.000"))

    def test_invalid_quantity_cost_or_unit_does_not_post(self):
        invalid_payloads = [
            {**self.payload, "quantity": "0"},
            {**self.payload, "unit_cost": "-1.00"},
            {**self.payload, "unit": "L"},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                payload["submission_key"] = str(uuid.uuid4())
                response = self.client.post(reverse("stock_inward"), payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(StockInward.objects.count(), 0)
                self.assertEqual(
                    StockMovement.objects.filter(source_doc_type="STOCK_INWARD").count(),
                    0,
                )

    def test_batch_dates_require_batch_and_cannot_be_expired(self):
        without_batch = {**self.payload, "batch_no": "", "submission_key": str(uuid.uuid4())}
        response = self.client.post(reverse("stock_inward"), without_batch)
        self.assertEqual(response.status_code, 200)

        expired = {
            **self.payload,
            "submission_key": str(uuid.uuid4()),
            "expiry_date": (self.entry_date - timedelta(days=1)).isoformat(),
        }
        response = self.client.post(reverse("stock_inward"), expired)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockInward.objects.count(), 0)

    def test_batch_dates_must_be_in_chronological_order(self):
        invalid_dates = {
            **self.payload,
            "submission_key": str(uuid.uuid4()),
            "manufacturing_date": (self.entry_date + timedelta(days=1)).isoformat(),
        }
        response = self.client.post(reverse("stock_inward"), invalid_dates)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockInward.objects.count(), 0)

    def test_accountant_can_view_stock_but_cannot_add_it(self):
        accountant = User.objects.get(username="accountant")
        self.client.force_login(accountant)

        response = self.client.get(reverse("stock"))
        self.assertNotContains(response, reverse("stock_inward"))
        self.assertEqual(self.client.get(reverse("stock_inward")).status_code, 403)
