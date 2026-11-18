from decimal import Decimal
from django.test import SimpleTestCase
from core.services.units import to_base, from_base


class UnitConversionTests(SimpleTestCase):
    def test_kg_to_grams(self):
        self.assertEqual(to_base("40", "kg"), Decimal("40000"))

    def test_litre_to_millilitres(self):
        self.assertEqual(to_base("10.5", "L"), Decimal("10500"))

    def test_carton_contains_twenty_packets(self):
        self.assertEqual(to_base(9, "carton"), Decimal("180"))

    def test_from_base_millilitres(self):
        self.assertEqual(from_base(Decimal("19500"), "L"), Decimal("19.500"))
