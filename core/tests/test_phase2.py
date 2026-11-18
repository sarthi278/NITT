from datetime import date
from decimal import Decimal
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase
from core.models import Item, Product, Purchase, ProductionRun, StockMovement, Supplier
from core.services.purchasing import post_purchase
from core.services.production import post_production_run
from core.services.inventory_valuation import weighted_average_rate
from core.services.stock import balance


class PurchaseAndProductionTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin")
        self.supplier = Supplier.objects.first()

    def test_seed_is_idempotent_for_opening_stock(self):
        movements_before = StockMovement.objects.count()
        call_command("seed_benchmark")
        self.assertEqual(StockMovement.objects.count(), movements_before)
        self.assertEqual(
            balance(Item.objects.get(sku="RM-FLOUR")),
            Decimal("100000"),
        )

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
        self.assertEqual(weighted_average_rate(flour), Decimal("0.04312500"))

    def test_benchmark_production_run(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        post_purchase(
            supplier=self.supplier, item=flour, display_quantity=Decimal("60"),
            unit="kg", unit_cost=Decimal("45.00"), purchase_date=date.today(),
            number="PUR-0001", user=self.admin,
        )

        product = Product.objects.get(sku="MUR-200")
        line_ids = {
            line.item.sku: line.pk
            for line in product.bom.lines.select_related("item")
        }
        run, consumptions, yield_variance, yield_flagged = post_production_run(
            product=product,
            batch_count=Decimal("1"),
            actual_good_output=188,
            damaged_pouches=6,
            labour_cost=Decimal("1200"),
            power_gas_cost=Decimal("600"),
            actual_quantities={
                line_ids["RM-FLOUR"]: Decimal("40"),
                line_ids["RM-OIL"]: Decimal("10.500"),
                line_ids["RM-SPICE"]: Decimal("1.200"),
                line_ids["PK-POUCH"]: Decimal("194"),
                line_ids["PK-CARTON"]: Decimal("9"),
            },
            wastage_quantities={
                line_ids["PK-POUCH"]: Decimal("6"),
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
        self.assertEqual(run.material_cost, Decimal("4055.00"))
        self.assertEqual(run.total_cost, Decimal("5855.00"))
        self.assertEqual(run.cost_per_packet, Decimal("31.14"))
        self.assertEqual(run.margin_per_packet, Decimal("18.86"))
        pouch = Item.objects.get(sku="PK-POUCH")
        self.assertEqual(
            StockMovement.objects.get(
                item=pouch,
                movement_type=StockMovement.MovementType.WASTE,
            ).qty_out,
            Decimal("6"),
        )
        oil = next(row for row in consumptions if row["item"].sku == "RM-OIL")
        self.assertEqual(oil["variance_pct"], Decimal("5.000"))
        self.assertTrue(oil["flagged"])
        self.assertEqual(yield_variance, Decimal("-6.000"))
        self.assertTrue(yield_flagged)

    def test_production_rolls_back_if_stock_is_insufficient(self):
        flour = Item.objects.get(sku="RM-FLOUR")
        product = Product.objects.get(sku="MUR-200")
        line_ids = {
            line.item.sku: line.pk
            for line in product.bom.lines.select_related("item")
        }
        try:
            post_production_run(
                product=product,
                batch_count=Decimal("1"),
                actual_good_output=188,
                damaged_pouches=6,
                labour_cost=Decimal("0"),
                power_gas_cost=Decimal("0"),
                actual_quantities={
                    line_ids["RM-FLOUR"]: Decimal("150"),
                    line_ids["RM-OIL"]: Decimal("10"),
                    line_ids["RM-SPICE"]: Decimal("1.2"),
                    line_ids["PK-POUCH"]: Decimal("194"),
                    line_ids["PK-CARTON"]: Decimal("9"),
                },
                wastage_quantities={
                    line_ids["PK-POUCH"]: Decimal("6"),
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
