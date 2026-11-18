from datetime import date
from decimal import Decimal
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase
from core.models import Item, Product, Purchase, ProductionRun, Supplier
from core.services.purchasing import post_purchase
from core.services.production import post_production_run
from core.services.stock import balance


class PurchaseAndProductionTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin")
        self.supplier = Supplier.objects.first()

    def test_purchase_adds_sixty_kg_flour(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        purchase = post_purchase(
            supplier=self.supplier,
            item=flour,
            display_quantity=Decimal("60"),
            unit="kg",
            unit_cost=Decimal("45.00"),
            purchase_date=date.today(),
            number="PUR-0001",
            user=self.admin,
        )
        self.assertEqual(Purchase.objects.get(pk=purchase.pk).lines.count(), 1)
        self.assertEqual(balance(flour), Decimal("160000"))

    def test_benchmark_production_run(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        post_purchase(
            supplier=self.supplier, item=flour, display_quantity=Decimal("60"),
            unit="kg", unit_cost=Decimal("45.00"), purchase_date=date.today(),
            number="PUR-0001", user=self.admin,
        )

        run, consumptions, yield_variance, yield_flagged = post_production_run(
            product=Product.objects.get(sku="MUR-200"),
            batch_count=Decimal("1"),
            actual_good_output=188,
            damaged_pouches=6,
            labour_cost=Decimal("1200"),
            power_gas_cost=Decimal("600"),
            actual_quantities={
                "RM-FLOUR": Decimal("40"),
                "RM-OIL": Decimal("10.500"),
                "RM-SPICE": Decimal("1.200"),
                "PK-POUCH": Decimal("194"),
                "PK-CARTON": Decimal("9"),
            },
            run_date=date.today(),
            number="RUN-0001",
            user=self.admin,
        )

        self.assertEqual(balance(flour), Decimal("120000"))
        self.assertEqual(balance(Item.objects.get(sku="RM-OIL")), Decimal("19500"))
        self.assertEqual(balance(Item.objects.get(sku="RM-SPICE")), Decimal("3800"))
        self.assertEqual(balance(Item.objects.get(sku="PK-POUCH")), Decimal("306"))
        self.assertEqual(balance(Item.objects.get(sku="PK-CARTON")), Decimal("21"))
        self.assertEqual(balance(Item.objects.get(sku="FG-MURUKKU-200")), Decimal("238"))
        oil = next(row for row in consumptions if row["item"].sku == "RM-OIL")
        self.assertEqual(oil["variance_pct"], Decimal("5.000"))
        self.assertTrue(oil["flagged"])
        self.assertEqual(yield_variance, Decimal("-6.000"))
        self.assertTrue(yield_flagged)

    def test_production_rolls_back_if_stock_is_insufficient(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        try:
            post_production_run(
                product=Product.objects.get(sku="MUR-200"),
                batch_count=Decimal("1"),
                actual_good_output=188,
                damaged_pouches=6,
                labour_cost=Decimal("0"),
                power_gas_cost=Decimal("0"),
                actual_quantities={
                    "RM-FLOUR": Decimal("150"),
                    "RM-OIL": Decimal("10"),
                    "RM-SPICE": Decimal("1.2"),
                    "PK-POUCH": Decimal("194"),
                    "PK-CARTON": Decimal("9"),
                },
                run_date=date.today(),
                number="RUN-FAILED",
                user=self.admin,
            )
            self.fail("Insufficient stock should fail")
        except ValueError:
            pass
        self.assertFalse(ProductionRun.objects.filter(number="RUN-FAILED").exists())
        self.assertEqual(balance(flour), Decimal("100000"))
