import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


MONEY = {"max_digits": 12, "decimal_places": 2}
QUANTITY = {"max_digits": 16, "decimal_places": 3}


class Item(models.Model):
    class ItemType(models.TextChoices):
        RAW = "RAW", "Raw material"
        PACKAGING = "PACKAGING", "Packaging"
        FINISHED = "FINISHED", "Finished goods"

    class BaseUnit(models.TextChoices):
        GRAM = "g", "gram"
        MILLILITRE = "ml", "millilitre"
        PIECE = "piece", "piece"

    name = models.CharField(max_length=120)
    sku = models.CharField(max_length=40, unique=True)
    item_type = models.CharField(max_length=12, choices=ItemType.choices)
    base_unit = models.CharField(max_length=8, choices=BaseUnit.choices)
    hsn_code = models.CharField(max_length=20, blank=True)
    gst_rate = models.DecimalField(default=Decimal("0"), **MONEY)
    reorder_level = models.DecimalField(default=Decimal("0"), **QUANTITY)

    def __str__(self):
        return self.name


class UnitConversion(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="conversions")
    unit = models.CharField(max_length=20)
    to_base_multiplier = models.DecimalField(**QUANTITY)

    class Meta:
        unique_together = ("item", "unit")


class StockBatch(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="batches")
    batch_no = models.CharField(max_length=80)
    mfg_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "batch_no"],
                name="unique_stock_batch_item_number",
            ),
        ]


class StockInward(models.Model):
    submission_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_inwards")
    display_quantity = models.DecimalField(**QUANTITY)
    base_quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=20)
    unit_cost = models.DecimalField(**MONEY)
    batch = models.ForeignKey(StockBatch, null=True, blank=True, on_delete=models.PROTECT)
    supplier = models.ForeignKey("Supplier", null=True, blank=True, on_delete=models.PROTECT)
    entry_date = models.DateField()
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        OPENING = "OPENING", "Opening"
        PURCHASE = "PURCHASE", "Purchase"
        ISSUE = "ISSUE", "Production issue"
        OUTPUT = "OUTPUT", "Production output"
        DISPATCH = "DISPATCH", "Dispatch"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        REVERSAL = "REVERSAL", "Credit note reversal"
        WASTE = "WASTE", "Wastage"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(StockBatch, null=True, blank=True, on_delete=models.PROTECT)
    qty_in = models.DecimalField(default=Decimal("0"), **QUANTITY)
    qty_out = models.DecimalField(default=Decimal("0"), **QUANTITY)
    unit_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    source_doc_type = models.CharField(max_length=30)
    source_doc_id = models.PositiveBigIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp", "id"]


class Supplier(models.Model):
    name = models.CharField(max_length=160)
    gstin = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=80, blank=True)

    def __str__(self):
        return self.name


class Customer(models.Model):
    name = models.CharField(max_length=160)
    customer_type = models.CharField(max_length=20, default="RETAIL")
    gstin = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=40, unique=True)
    hsn_code = models.CharField(max_length=20)
    gst_rate = models.DecimalField(default=Decimal("5"), **MONEY)
    selling_price = models.DecimalField(**MONEY)

    def __str__(self):
        return self.name


class ProductPackSize(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="pack_sizes")
    name = models.CharField(max_length=30)
    packet_count = models.PositiveIntegerField(default=1)


class BOM(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="bom")
    batch_yield_packets = models.PositiveIntegerField(default=200)
    tolerance_pct = models.DecimalField(default=Decimal("3"), **QUANTITY)


class BOMLine(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty_per_batch = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=20)


class CompanySettings(models.Model):
    name = models.CharField(max_length=160, default="NIT Demo Snacks")
    gstin = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=80, default="Tamil Nadu")
    fy_start_month = models.PositiveSmallIntegerField(default=4)


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=20)
    entity_type = models.CharField(max_length=50)
    entity_id = models.PositiveBigIntegerField()
    field_name = models.CharField(max_length=80, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)


class Purchase(models.Model):
    number = models.CharField(max_length=40, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    purchase_date = models.DateField()
    posted = models.BooleanField(default=False)


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=20)
    unit_cost = models.DecimalField(**MONEY)


class ProductionRun(models.Model):
    number = models.CharField(max_length=40, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch_count = models.DecimalField(default=Decimal("1"), **QUANTITY)
    expected_output = models.PositiveIntegerField()
    actual_good_output = models.PositiveIntegerField()
    damaged_pouches = models.PositiveIntegerField(default=0)
    labour_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    power_gas_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    run_date = models.DateField()
    posted = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ProductionConsumption(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="consumptions")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    planned_qty = models.DecimalField(**QUANTITY)
    actual_qty = models.DecimalField(**QUANTITY)
    variance_pct = models.DecimalField(**QUANTITY)
    flagged = models.BooleanField(default=False)
