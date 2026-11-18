from decimal import Decimal
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from core.models import Item, StockMovement
from core.services.stock import balance, create_movement


class SeedAndLedgerTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")

    def test_benchmark_opening_balances(self):
        self.assertEqual(balance(Item.objects.get(sku="RM-FLOUR")), Decimal("100000"))
        self.assertEqual(balance(Item.objects.get(sku="RM-OIL")), Decimal("30000"))
        self.assertEqual(balance(Item.objects.get(sku="RM-SPICE")), Decimal("5000"))
        self.assertEqual(balance(Item.objects.get(sku="PK-POUCH")), Decimal("500"))
        self.assertEqual(balance(Item.objects.get(sku="PK-CARTON")), Decimal("30"))
        self.assertEqual(balance(Item.objects.get(sku="FG-MURUKKU-200")), Decimal("50"))

    def test_roles_and_users_exist(self):
        self.assertTrue(User.objects.filter(username="sales-a").exists())
        self.assertTrue(User.objects.filter(username="accountant").exists())

    def test_movement_changes_ledger_without_stored_balance(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        before = balance(flour)
        admin = User.objects.get(username="admin")
        create_movement(
            item=flour,
            qty_out=Decimal("40000"),
            movement_type=StockMovement.MovementType.ISSUE,
            source_doc_type="PRODUCTION_RUN",
            source_doc_id=1,
            user=admin,
        )
        self.assertEqual(balance(flour), before - Decimal("40000"))
        self.assertEqual(flour.stock_movements.count() if hasattr(flour, "stock_movements") else StockMovement.objects.filter(item=flour).count(), 2)
