from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import RequestFactory, TestCase
from django.test import override_settings
from django.urls import reverse

from core.admin import admin_site
from core.models import (
    AuditLog,
    Customer,
    Item,
    Purchase,
    StockMovement,
    UnitConversion,
)
from core.services.stock import balance
from core.validators import validate_gstin


class AdminAndImmutableLedgerTests(TestCase):
    def make_user(self, username, role, *, is_staff=True):
        group, _ = Group.objects.get_or_create(name=role)
        user = User.objects.create_user(
            username=username,
            password="test-password",
            is_staff=is_staff,
        )
        user.groups.add(group)
        return user

    def test_only_admin_and_owner_groups_can_access_admin(self):
        production = self.make_user("floor-admin", "Production")
        production.is_staff = True
        production.save(update_fields=["is_staff"])
        self.client.force_login(production)
        response = self.client.get(reverse("admin:index"))
        self.assertNotEqual(response.status_code, 200)

        owner = self.make_user("business-owner", "Owner")
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_stock_movement_and_audit_log_are_read_only_in_admin(self):
        owner = self.make_user("read-only-owner", "Owner")
        item = Item.objects.create(
            name="Flour",
            sku="ADMIN-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        movement = StockMovement.objects.create(
            item=item,
            qty_in=Decimal("100"),
            movement_type=StockMovement.MovementType.OPENING,
            source_doc_type="TEST",
            source_doc_id=1,
        )
        audit = AuditLog.objects.create(
            user=owner,
            action="CREATE",
            entity_type="Item",
            entity_id=item.pk,
        )
        self.client.force_login(owner)
        movement_admin = admin_site._registry[StockMovement]
        audit_admin = admin_site._registry[AuditLog]
        request = RequestFactory().get("/admin/")
        request.user = owner

        for model_admin, obj in ((movement_admin, movement), (audit_admin, audit)):
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request, obj))
            self.assertFalse(model_admin.has_delete_permission(request, obj))
        movement_page = self.client.get(
            reverse("admin:core_stockmovement_change", args=[movement.pk])
        )
        audit_page = self.client.get(
            reverse("admin:core_auditlog_change", args=[audit.pk])
        )
        self.assertEqual(movement_page.status_code, 200)
        self.assertEqual(audit_page.status_code, 200)
        self.assertNotContains(movement_page, 'name="_save"')
        self.assertNotContains(audit_page, 'name="_save"')

    def test_stock_movements_and_audit_logs_reject_mutation(self):
        owner = self.make_user("immutable-owner", "Owner")
        item = Item.objects.create(
            name="Oil",
            sku="IMMUTABLE-OIL",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.MILLILITRE,
        )
        movement = StockMovement.objects.create(
            item=item,
            qty_in=Decimal("10"),
            movement_type=StockMovement.MovementType.OPENING,
            source_doc_type="TEST",
            source_doc_id=1,
        )
        log = AuditLog.objects.create(
            user=owner,
            action="CREATE",
            entity_type="Item",
            entity_id=item.pk,
        )
        with self.assertRaisesMessage(ValueError, "immutable"):
            movement.save()
        with self.assertRaisesMessage(ValueError, "deleted"):
            movement.delete()
        with self.assertRaisesMessage(ValueError, "updated"):
            StockMovement.objects.filter(pk=movement.pk).update(qty_in=0)

        log.action = "UPDATE"
        with self.assertRaisesMessage(ValueError, "immutable"):
            log.save()
        with self.assertRaisesMessage(ValueError, "deleted"):
            log.delete()
        with self.assertRaisesMessage(ValueError, "updated"):
            AuditLog.objects.filter(pk=log.pk).update(action="UPDATE")


class MasterDataPermissionTests(TestCase):
    def setUp(self):
        self.admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user(
            username="management-admin",
            password="test-password",
            is_staff=True,
        )
        self.admin.groups.add(self.admin_group)

    def test_management_screens_require_admin_role(self):
        salesman_group, _ = Group.objects.get_or_create(name="Salesman")
        salesman = User.objects.create_user(username="regular-sales")
        salesman.groups.add(salesman_group)
        self.client.force_login(salesman)
        for page in ("products", "customers", "suppliers", "price_lists"):
            with self.subTest(page=page):
                self.assertEqual(
                    self.client.get(reverse(page)).status_code,
                    403,
                )

        for route in (
            ("purchase", []),
            ("production", []),
            ("stock_inward", []),
            ("stock", []),
            ("ledger", []),
            ("payments", []),
            ("reports", []),
            ("item_create", []),
            ("order_review", [999]),
            ("order_invoice", [999]),
            ("invoice_dispatch", [999]),
            ("invoice_credit_note", [999]),
        ):
            with self.subTest(route=route[0]):
                self.assertEqual(
                    self.client.get(reverse(route[0], args=route[1])).status_code,
                    403,
                )

        self.client.force_login(self.admin)
        for page in ("products", "customers", "suppliers", "price_lists"):
            with self.subTest(page=page):
                self.assertEqual(
                    self.client.get(reverse(page)).status_code,
                    200,
                )
        for page in (
            "product_create",
            "customer_create",
            "supplier_create",
            "price_list_create",
        ):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="viewport"')
            self.assertContains(response, "col-12")

    def test_gstin_format_is_validated(self):
        validate_gstin("33AAAAA0000A1Z5")
        with self.assertRaises(ValidationError):
            validate_gstin("not-a-gstin")

    def test_product_form_applies_a_reusable_bom_template(self):
        from core.models import BOMTemplate, BOMTemplateLine, Product

        finished = Item.objects.create(
            name="Murukku 500 g",
            sku="FORM-FINISHED",
            item_type=Item.ItemType.FINISHED,
            base_unit=Item.BaseUnit.PIECE,
        )
        flour = Item.objects.create(
            name="Flour",
            sku="FORM-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        UnitConversion.objects.create(
            item=flour,
            unit="kg",
            to_base_multiplier=Decimal("1000"),
        )
        template = BOMTemplate.objects.create(
            name="500 g template",
            batch_yield_packets=80,
        )
        BOMTemplateLine.objects.create(
            template=template,
            item=flour,
            qty_per_batch=Decimal("40"),
            unit="kg",
        )

        self.client.force_login(self.admin)
        product_payload = {
            "name": "Murukku 500 g",
            "sku": "FORM-MUR-500",
            "finished_item": finished.pk,
            "hsn_code": "19059030",
            "gst_rate": "5",
            "selling_price": "125",
            "is_active": "on",
            "bom_template": template.pk,
            "batch_yield_packets": "80",
            "tolerance_pct": "3",
            "bom_lines-TOTAL_FORMS": "1",
            "bom_lines-INITIAL_FORMS": "0",
            "bom_lines-MIN_NUM_FORMS": "0",
            "bom_lines-MAX_NUM_FORMS": "30",
            "bom_lines-0-item": "",
            "bom_lines-0-qty_per_batch": "",
            "bom_lines-0-unit": "",
            "pack_sizes-TOTAL_FORMS": "1",
            "pack_sizes-INITIAL_FORMS": "0",
            "pack_sizes-MIN_NUM_FORMS": "0",
            "pack_sizes-MAX_NUM_FORMS": "10",
            "pack_sizes-0-name": "500 g",
            "pack_sizes-0-packet_count": "1",
        }
        response = self.client.post(reverse("product_create"), product_payload)
        self.assertRedirects(response, reverse("products"))
        product = Product.objects.get(sku="FORM-MUR-500")
        self.assertEqual(product.bom.batch_yield_packets, 80)
        self.assertEqual(product.bom.lines.get().item, flour)
        self.assertEqual(product.pack_sizes.get().name, "500 g")

        invalid_item = Item.objects.create(
            name="Unconfigured flour",
            sku="FORM-UNCONFIGURED",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        invalid_template = BOMTemplate.objects.create(name="Invalid units")
        BOMTemplateLine.objects.create(
            template=invalid_template,
            item=invalid_item,
            qty_per_batch=Decimal("40"),
            unit="kg",
        )
        invalid_finished = Item.objects.create(
            name="Murukku test",
            sku="FORM-FINISHED-BAD",
            item_type=Item.ItemType.FINISHED,
            base_unit=Item.BaseUnit.PIECE,
        )
        product_payload.update({
            "name": "Murukku invalid template",
            "sku": "FORM-MUR-BAD",
            "finished_item": invalid_finished.pk,
            "bom_template": invalid_template.pk,
        })
        response = self.client.post(reverse("product_create"), product_payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a unit configured for this item")
        self.assertFalse(Product.objects.filter(sku="FORM-MUR-BAD").exists())

    def test_customer_and_supplier_creation_validate_gstin(self):
        sales_group, _ = Group.objects.get_or_create(name="Salesman")
        sales_user = User.objects.create_user(username="assigned-sales")
        sales_user.groups.add(sales_group)
        self.client.force_login(self.admin)
        customer_data = {
            "name": "New Retailer",
            "customer_type": "RETAIL",
            "gstin": "33AAAAA0000A1Z5",
            "state": "Tamil Nadu",
            "address": "Trichy",
            "phone": "9000000000",
            "email": "buyer@example.test",
            "assigned_salesman": sales_user.pk,
            "price_list": "",
            "is_active": "on",
            "prices-TOTAL_FORMS": "1",
            "prices-INITIAL_FORMS": "0",
            "prices-MIN_NUM_FORMS": "0",
            "prices-MAX_NUM_FORMS": "30",
            "prices-0-product": "",
            "prices-0-unit_price": "",
        }
        response = self.client.post(reverse("customer_create"), customer_data)
        self.assertRedirects(response, reverse("customers"))
        customer = Customer.objects.get(name="New Retailer")
        self.assertEqual(customer.assigned_salesman, sales_user)
        self.assertEqual(customer.gstin, "33AAAAA0000A1Z5")

        customer_data["name"] = "Bad GST Customer"
        customer_data["gstin"] = "invalid"
        response = self.client.post(reverse("customer_create"), customer_data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Customer.objects.filter(name="Bad GST Customer").exists())

        response = self.client.post(reverse("supplier_create"), {
            "name": "New Supplier",
            "gstin": "29AAAAA0000A1Z5",
            "state": "Karnataka",
            "address": "Bengaluru",
            "phone": "9000000001",
            "email": "supplier@example.test",
            "is_active": "on",
        })
        self.assertRedirects(response, reverse("suppliers"))

    def test_reset_benchmark_requires_explicit_development_database(self):
        from django.db import connection

        with override_settings(
            DEBUG=True,
            BENCHMARK_RESET_ENABLED=True,
            BENCHMARK_DEMO_DATABASE=str(connection.settings_dict["NAME"]),
        ):
            call_command("reset_benchmark", confirm=True, verbosity=0)
        self.assertEqual(balance(Item.objects.get(sku="RM-FLOUR")), Decimal("100000"))
        self.assertEqual(balance(Item.objects.get(sku="FG-MURUKKU-200")), Decimal("50"))
        self.assertEqual(Purchase.objects.count(), 0)

        with override_settings(
            DEBUG=False,
            BENCHMARK_RESET_ENABLED=True,
            BENCHMARK_DEMO_DATABASE=str(connection.settings_dict["NAME"]),
        ):
            with self.assertRaises(CommandError):
                call_command("reset_benchmark", confirm=True, verbosity=0)
