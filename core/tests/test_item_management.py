from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from core.models import Item, StockMovement, UnitConversion
from core.services.stock import balance, create_movement


class ItemManagementTests(TestCase):
    def setUp(self):
        self.admin = self.create_user("admin", "Admin")
        self.production = self.create_user("production", "Production")
        self.owner = self.create_user("owner", "Owner")
        self.accountant = self.create_user("accountant", "Accountant")
        self.salesman = self.create_user("salesman", "Salesman")
        self.client.force_login(self.admin)

    def create_user(self, username, role):
        group, _ = Group.objects.get_or_create(name=role)
        user = User.objects.create_user(username=username, password="demo123")
        user.groups.add(group)
        return user

    def item_payload(self, **overrides):
        data = {
            "name": "New flour",
            "sku": "RM-NEW-FLOUR",
            "item_type": Item.ItemType.RAW,
            "base_unit": Item.BaseUnit.GRAM,
            "hsn_code": "",
            "gst_rate": "5.00",
            "reorder_level": "1000.000",
            "conversions-TOTAL_FORMS": "2",
            "conversions-INITIAL_FORMS": "0",
            "conversions-0-unit": "kg",
            "conversions-0-to_base_multiplier": "1000.000",
            "conversions-1-unit": "g",
            "conversions-1-to_base_multiplier": "1.000",
        }
        data.update(overrides)
        return data

    def test_create_item_saves_conversions_without_creating_stock(self):
        response = self.client.post(reverse("item_create"), self.item_payload())

        self.assertRedirects(response, reverse("stock"))
        item = Item.objects.get(sku="RM-NEW-FLOUR")
        self.assertEqual(item.conversions.count(), 2)
        self.assertEqual(balance(item), Decimal("0"))
        self.assertFalse(StockMovement.objects.filter(item=item).exists())

    def test_invalid_values_do_not_create_partial_records(self):
        payload = self.item_payload(
            gst_rate="-1.00",
            reorder_level="-1.000",
            **{
                "conversions-0-to_base_multiplier": "0",
                "conversions-1-unit": "",
                "conversions-1-to_base_multiplier": "",
            },
        )
        response = self.client.post(reverse("item_create"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(UnitConversion.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_duplicate_sku_is_rejected(self):
        Item.objects.create(
            name="Existing item",
            sku="RM-NEW-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )

        response = self.client.post(reverse("item_create"), self.item_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Item.objects.filter(sku="RM-NEW-FLOUR").count(), 1)

    def test_edit_updates_item_and_conversions(self):
        item = Item.objects.create(
            name="Old flour",
            sku="RM-OLD-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        conversion = UnitConversion.objects.create(
            item=item,
            unit="kg",
            to_base_multiplier=Decimal("1000"),
        )
        payload = self.item_payload(
            name="Updated flour",
            sku="RM-UPDATED-FLOUR",
            **{
                "conversions-TOTAL_FORMS": "2",
                "conversions-INITIAL_FORMS": "1",
                "conversions-0-id": str(conversion.pk),
            },
        )

        response = self.client.post(reverse("item_edit", args=[item.pk]), payload)

        self.assertRedirects(response, reverse("stock"))
        item.refresh_from_db()
        self.assertEqual(item.name, "Updated flour")
        self.assertEqual(item.sku, "RM-UPDATED-FLOUR")
        self.assertEqual(
            set(item.conversions.values_list("unit", flat=True)),
            {"kg", "g"},
        )

    def test_base_unit_cannot_change_after_stock_is_posted(self):
        item = Item.objects.create(
            name="Used flour",
            sku="RM-USED-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        create_movement(
            item=item,
            qty_in=Decimal("1000"),
            movement_type=StockMovement.MovementType.OPENING,
            source_doc_type="TEST",
            source_doc_id=1,
        )
        payload = self.item_payload(
            name=item.name,
            sku=item.sku,
            base_unit=Item.BaseUnit.MILLILITRE,
            **{
                "conversions-TOTAL_FORMS": "0",
                "conversions-INITIAL_FORMS": "0",
            },
        )

        response = self.client.post(reverse("item_edit", args=[item.pk]), payload)

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.base_unit, Item.BaseUnit.GRAM)
        self.assertEqual(balance(item), Decimal("1000"))

    def test_roles_follow_existing_stock_management_policy(self):
        for user in (self.admin, self.owner, self.production):
            with self.subTest(role=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("item_create")).status_code, 200)

        for user in (self.accountant, self.salesman):
            with self.subTest(role=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("item_create")).status_code, 403)
                stock_response = self.client.get(reverse("stock"))
                if user == self.accountant:
                    self.assertEqual(stock_response.status_code, 200)
                    self.assertNotContains(stock_response, "Add Item")
                else:
                    self.assertEqual(stock_response.status_code, 403)

    def test_deactivation_preserves_item_and_historical_stock(self):
        item = Item.objects.create(
            name="Retired flour",
            sku="RM-RETIRED-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        create_movement(
            item=item,
            qty_in=Decimal("500"),
            movement_type=StockMovement.MovementType.OPENING,
            source_doc_type="TEST",
            source_doc_id=2,
        )

        response = self.client.post(reverse("item_toggle_active", args=[item.pk]))

        self.assertRedirects(response, reverse("stock"))
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertEqual(balance(item), Decimal("500"))
        self.assertContains(self.client.get(reverse("stock")), "Inactive")

    def test_stock_list_search_filter_and_low_stock_state(self):
        flour = Item.objects.create(
            name="Searchable flour",
            sku="RM-SEARCH-FLOUR",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
            reorder_level=Decimal("10"),
        )
        Item.objects.create(
            name="Carton",
            sku="PK-CARTON-SEARCH",
            item_type=Item.ItemType.PACKAGING,
            base_unit=Item.BaseUnit.PIECE,
        )
        create_movement(
            item=flour,
            qty_in=Decimal("5"),
            movement_type=StockMovement.MovementType.OPENING,
            source_doc_type="TEST",
            source_doc_id=3,
        )

        response = self.client.get(
            reverse("stock"),
            {"q": "search", "item_type": Item.ItemType.RAW},
        )

        self.assertContains(response, "Searchable flour")
        self.assertContains(response, "RM-SEARCH-FLOUR")
        self.assertContains(response, "Low stock")
        self.assertNotContains(response, "PK-CARTON-SEARCH")

    def test_empty_stock_list_has_clear_message(self):
        response = self.client.get(reverse("stock"))

        self.assertContains(response, "No inventory items yet.")
