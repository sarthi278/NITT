from decimal import Decimal
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import BOM, BOMLine, CompanySettings, Customer, Item, Product, ProductPackSize, Supplier, UnitConversion, StockMovement
from core.services.stock import create_movement


class Command(BaseCommand):
    help = "Create or refresh benchmark master data and test users without duplicating opening stock"

    @transaction.atomic
    def handle(self, *args, **options):
        for role in ["Salesman", "Admin", "Production", "Accountant", "Owner"]:
            Group.objects.get_or_create(name=role)

        users = [
            ("sales-a", "Salesman"),
            ("sales-b", "Salesman"),
            ("admin", "Admin"),
            ("production", "Production"),
            ("accountant", "Accountant"),
            ("owner", "Owner"),
        ]
        for username, role in users:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password("demo123")
            user.is_staff = role in ("Admin", "Production", "Accountant", "Owner")
            user.save()
            user.groups.clear()
            user.groups.add(Group.objects.get(name=role))

        CompanySettings.objects.update_or_create(
            pk=1,
            defaults={"name": "Demo Snacks", "state": "Tamil Nadu", "fy_start_month": 4},
        )

        sales_a = User.objects.get(username="sales-a")
        sales_b = User.objects.get(username="sales-b")
        Customer.objects.update_or_create(
            name="Retailer A",
            defaults={
                "state": "Tamil Nadu",
                "customer_type": "RETAIL",
                "gstin": "33AAAAA0000A1Z5",
                "assigned_salesman": sales_a,
            },
        )
        Customer.objects.update_or_create(
            name="Distributor B",
            defaults={
                "state": "Karnataka",
                "customer_type": "DISTRIBUTOR",
                "gstin": "29AAAAA0000A1Z5",
                "assigned_salesman": sales_b,
            },
        )
        Supplier.objects.get_or_create(name="Benchmark Flour Supplier")

        flour, _ = Item.objects.update_or_create(
            sku="RM-FLOUR",
            defaults={"name": "Flour", "item_type": "RAW", "base_unit": "g", "reorder_level": Decimal("10000"), "is_active": True},
        )
        oil, _ = Item.objects.update_or_create(
            sku="RM-OIL",
            defaults={"name": "Cooking oil", "item_type": "RAW", "base_unit": "ml", "reorder_level": Decimal("5000"), "is_active": True},
        )
        spice, _ = Item.objects.update_or_create(
            sku="RM-SPICE",
            defaults={"name": "Salt and spice mix", "item_type": "RAW", "base_unit": "g", "reorder_level": Decimal("1000"), "is_active": True},
        )
        pouch, _ = Item.objects.update_or_create(
            sku="PK-POUCH",
            defaults={"name": "200 g pouch", "item_type": "PACKAGING", "base_unit": "piece", "is_active": True},
        )
        carton, _ = Item.objects.update_or_create(
            sku="PK-CARTON",
            defaults={"name": "Carton", "item_type": "PACKAGING", "base_unit": "piece", "is_active": True},
        )
        finished, _ = Item.objects.update_or_create(
            sku="FG-MURUKKU-200",
            defaults={"name": "Murukku 200 g", "item_type": "FINISHED", "base_unit": "piece", "hsn_code": "19059030", "is_active": True},
        )

        conversions = [
            (flour, "kg", "1000"),
            (flour, "g", "1"),
            (oil, "L", "1000"),
            (oil, "litre", "1000"),
            (oil, "ml", "1"),
            (spice, "kg", "1000"),
            (spice, "g", "1"),
            (pouch, "piece", "1"),
            (carton, "piece", "1"),
            (finished, "packet", "1"),
            (finished, "carton", "20"),
        ]
        for item, unit, multiplier in conversions:
            UnitConversion.objects.update_or_create(
                item=item,
                unit=unit,
                defaults={"to_base_multiplier": Decimal(multiplier)},
            )

        product, _ = Product.objects.update_or_create(
            sku="MUR-200",
            defaults={"name": "Murukku 200 g", "finished_item": finished, "hsn_code": "19059030", "gst_rate": Decimal("5"), "selling_price": Decimal("50.00")},
        )
        ProductPackSize.objects.update_or_create(product=product, name="200 g", defaults={"packet_count": 1})

        bom, _ = BOM.objects.update_or_create(product=product, defaults={"batch_yield_packets": 200, "tolerance_pct": Decimal("3")})
        bom_lines = [
            (flour, "40", "kg"),
            (oil, "10", "L"),
            (spice, "1.200", "kg"),
            (pouch, "200", "piece"),
            (carton, "10", "piece"),
        ]
        for item, quantity, unit in bom_lines:
            BOMLine.objects.update_or_create(
                bom=bom,
                item=item,
                defaults={"qty_per_batch": Decimal(quantity), "unit": unit},
            )

        system = User.objects.get(username="admin")
        openings = [
            (flour, Decimal("100000"), Decimal("42.00"), Decimal("0.042")),
            (oil, Decimal("30000"), Decimal("140.00"), Decimal("0.14")),
            (spice, Decimal("5000"), Decimal("200.00"), Decimal("0.20")),
            (pouch, Decimal("500"), Decimal("2.50"), Decimal("2.50")),
            (carton, Decimal("30"), Decimal("15.00"), Decimal("15.00")),
            (finished, Decimal("50"), Decimal("50.00"), Decimal("50.00")),
        ]
        for item, quantity, unit_cost, unit_cost_base in openings:
            if StockMovement.objects.filter(
                item=item,
                source_doc_type="OPENING",
                source_doc_id=1,
            ).exists():
                continue
            create_movement(
                item=item,
                qty_in=quantity,
                movement_type="OPENING",
                source_doc_type="OPENING",
                source_doc_id=1,
                user=system,
                unit_cost=unit_cost,
                unit_cost_base=unit_cost_base,
            )

        self.stdout.write(self.style.SUCCESS("Seeded benchmark users, masters, BOM and opening stock"))
