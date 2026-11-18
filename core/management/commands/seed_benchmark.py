from decimal import Decimal
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import BOM, BOMLine, CompanySettings, Customer, Item, Product, ProductPackSize, Supplier, UnitConversion, StockMovement
from core.services.stock import create_movement


class Command(BaseCommand):
    help = "Reset benchmark master data and test users"

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

        CompanySettings.objects.all().delete()
        CompanySettings.objects.create(name="Demo Snacks", state="Tamil Nadu", fy_start_month=4)

        Customer.objects.update_or_create(
            name="Retailer A",
            defaults={"state": "Tamil Nadu", "customer_type": "RETAIL", "gstin": "33AAAAA0000A1Z5"},
        )
        Customer.objects.update_or_create(
            name="Distributor B",
            defaults={"state": "Karnataka", "customer_type": "DISTRIBUTOR", "gstin": "29AAAAA0000A1Z5"},
        )
        Supplier.objects.get_or_create(name="Benchmark Flour Supplier")

        StockMovement.objects.all().delete()
        Item.objects.all().delete()
        flour = Item.objects.create(name="Flour", sku="RM-FLOUR", item_type="RAW", base_unit="g", reorder_level=Decimal("10000"))
        oil = Item.objects.create(name="Cooking oil", sku="RM-OIL", item_type="RAW", base_unit="ml", reorder_level=Decimal("5000"))
        spice = Item.objects.create(name="Salt and spice mix", sku="RM-SPICE", item_type="RAW", base_unit="g", reorder_level=Decimal("1000"))
        pouch = Item.objects.create(name="200 g pouch", sku="PK-POUCH", item_type="PACKAGING", base_unit="piece")
        carton = Item.objects.create(name="Carton", sku="PK-CARTON", item_type="PACKAGING", base_unit="piece")
        finished = Item.objects.create(name="Murukku 200 g", sku="FG-MURUKKU-200", item_type="FINISHED", base_unit="piece", hsn_code="19059030")

        UnitConversion.objects.bulk_create([
            UnitConversion(item=flour, unit="kg", to_base_multiplier=Decimal("1000")),
            UnitConversion(item=flour, unit="g", to_base_multiplier=Decimal("1")),
            UnitConversion(item=oil, unit="L", to_base_multiplier=Decimal("1000")),
            UnitConversion(item=oil, unit="litre", to_base_multiplier=Decimal("1000")),
            UnitConversion(item=oil, unit="ml", to_base_multiplier=Decimal("1")),
            UnitConversion(item=spice, unit="kg", to_base_multiplier=Decimal("1000")),
            UnitConversion(item=spice, unit="g", to_base_multiplier=Decimal("1")),
            UnitConversion(item=pouch, unit="piece", to_base_multiplier=Decimal("1")),
            UnitConversion(item=carton, unit="piece", to_base_multiplier=Decimal("1")),
            UnitConversion(item=finished, unit="packet", to_base_multiplier=Decimal("1")),
            UnitConversion(item=finished, unit="carton", to_base_multiplier=Decimal("20")),
        ])

        product, _ = Product.objects.update_or_create(
            sku="MUR-200",
            defaults={"name": "Murukku 200 g", "hsn_code": "19059030", "gst_rate": Decimal("5"), "selling_price": Decimal("50.00")},
        )
        ProductPackSize.objects.update_or_create(product=product, name="200 g", defaults={"packet_count": 1})

        bom, _ = BOM.objects.update_or_create(product=product, defaults={"batch_yield_packets": 200, "tolerance_pct": Decimal("3")})
        BOMLine.objects.filter(bom=bom).delete()
        BOMLine.objects.bulk_create([
            BOMLine(bom=bom, item=flour, qty_per_batch=Decimal("40"), unit="kg"),
            BOMLine(bom=bom, item=oil, qty_per_batch=Decimal("10"), unit="L"),
            BOMLine(bom=bom, item=spice, qty_per_batch=Decimal("1.200"), unit="kg"),
            BOMLine(bom=bom, item=pouch, qty_per_batch=Decimal("200"), unit="piece"),
            BOMLine(bom=bom, item=carton, qty_per_batch=Decimal("10"), unit="piece"),
        ])

        system = User.objects.get(username="admin")
        openings = [
            (flour, Decimal("100000"), Decimal("42.00")),
            (oil, Decimal("30000"), Decimal("140.00")),
            (spice, Decimal("5000"), Decimal("200.00")),
            (pouch, Decimal("500"), Decimal("2.50")),
            (carton, Decimal("30"), Decimal("15.00")),
            (finished, Decimal("50"), Decimal("50.00")),
        ]
        for item, quantity, unit_cost in openings:
            create_movement(
                item=item,
                qty_in=quantity,
                movement_type="OPENING",
                source_doc_type="OPENING",
                source_doc_id=1,
                user=system,
                unit_cost=unit_cost,
            )

        self.stdout.write(self.style.SUCCESS("Seeded benchmark users, masters, BOM and opening stock"))
