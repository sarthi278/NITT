from decimal import Decimal
from django.test import SimpleTestCase, TestCase

from core.models import Item, UnitConversion
from core.services.units import item_quantity_to_base, to_base, from_base


class UnitConversionTests(SimpleTestCase):
    def test_kg_to_grams(self):
        self.assertEqual(to_base("40", "kg"), Decimal("40000"))

    def test_litre_to_millilitres(self):
        self.assertEqual(to_base("10.5", "L"), Decimal("10500"))

    def test_carton_contains_twenty_packets(self):
        self.assertEqual(to_base(9, "carton"), Decimal("180"))

    def test_from_base_millilitres(self):
        self.assertEqual(from_base(Decimal("19500"), "L"), Decimal("19.500"))


class ItemAwareUnitConversionTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            name="Flour",
            sku="FLOUR-UNIT-TEST",
            item_type=Item.ItemType.RAW,
            base_unit=Item.BaseUnit.GRAM,
        )
        UnitConversion.objects.create(
            item=self.item,
            unit="kg",
            to_base_multiplier=Decimal("1000"),
        )

    def test_selected_item_conversion_uses_its_conversion_records(self):
        self.assertEqual(
            item_quantity_to_base("40", self.item, "kg"),
            Decimal("40000.000"),
        )

    def test_zero_is_allowed_only_for_optional_consumption(self):
        self.assertEqual(
            item_quantity_to_base("0", self.item, "kg", allow_zero=True),
            Decimal("0.000"),
        )
        with self.assertRaises(ValueError):
            item_quantity_to_base("0", self.item, "kg")

    def test_unconfigured_unit_is_rejected(self):
        with self.assertRaises(ValueError):
            item_quantity_to_base("1", self.item, "bag")
