import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from core.validators import validate_gstin


MONEY = {"max_digits": 12, "decimal_places": 2}
QUANTITY = {"max_digits": 16, "decimal_places": 3}


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Append-only records cannot be updated.")

    def delete(self):
        raise ValueError("Append-only records cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Append-only records cannot be updated.")


class Item(models.Model):
    class ItemType(models.TextChoices):
        RAW = "RAW", "Raw Material"
        PACKAGING = "PACKAGING", "Packaging Material"
        FINISHED = "FINISHED", "Finished Goods"

    class BaseUnit(models.TextChoices):
        GRAM = "g", "Gram (g)"
        MILLILITRE = "ml", "Millilitre (ml)"
        PIECE = "piece", "Piece"

    name = models.CharField(max_length=120)
    sku = models.CharField(max_length=40, unique=True)
    item_type = models.CharField(max_length=12, choices=ItemType.choices)
    base_unit = models.CharField(max_length=8, choices=BaseUnit.choices)
    hsn_code = models.CharField(max_length=20, blank=True)
    gst_rate = models.DecimalField(
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        **MONEY,
    )
    reorder_level = models.DecimalField(
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        **QUANTITY,
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class UnitConversion(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="conversions")
    unit = models.CharField(max_length=20)
    to_base_multiplier = models.DecimalField(
        validators=[MinValueValidator(Decimal("0.001"))],
        **QUANTITY,
    )

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
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal("0"))
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    source_doc_type = models.CharField(max_length=30)
    source_doc_id = models.PositiveBigIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp", "id"]

    objects = ImmutableQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Stock movements are immutable; post a reversal or adjustment.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Stock movements cannot be deleted.")


class Supplier(models.Model):
    name = models.CharField(max_length=160)
    gstin = models.CharField(max_length=20, blank=True, validators=[validate_gstin])
    state = models.CharField(max_length=80, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Customer(models.Model):
    class CustomerType(models.TextChoices):
        RETAIL = "RETAIL", "Retail"
        DISTRIBUTOR = "DISTRIBUTOR", "Distributor"
        WHOLESALE = "WHOLESALE", "Wholesale"

    name = models.CharField(max_length=160)
    customer_type = models.CharField(
        max_length=20,
        choices=CustomerType.choices,
        default=CustomerType.RETAIL,
    )
    gstin = models.CharField(max_length=20, blank=True, validators=[validate_gstin])
    state = models.CharField(max_length=80)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    assigned_salesman = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_customers",
    )
    price_list = models.ForeignKey(
        "PriceList",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="customers",
    )

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=40, unique=True)
    finished_item = models.OneToOneField(
        Item,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="finished_product",
    )
    hsn_code = models.CharField(max_length=20)
    gst_rate = models.DecimalField(
        default=Decimal("5"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        **MONEY,
    )
    selling_price = models.DecimalField(**MONEY)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PriceList(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PriceListItem(models.Model):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    unit_price = models.DecimalField(**MONEY)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "product"],
                name="unique_product_price_per_list",
            ),
        ]


class CustomerPrice(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    unit_price = models.DecimalField(**MONEY)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "product"],
                name="unique_customer_product_price",
            ),
        ]


class Order(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        INVOICED = "INVOICED", "Invoiced"
        DISPATCHED = "DISPATCHED", "Dispatched"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_orders",
    )
    client_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_sales_orders",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at", "-id"]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QUANTITY)
    approved_quantity = models.DecimalField(default=Decimal("0"), **QUANTITY)
    unit_price = models.DecimalField(**MONEY)


class InvoiceSequence(models.Model):
    financial_year = models.CharField(max_length=7, unique=True)
    last_number = models.PositiveIntegerField(default=0)


class Invoice(models.Model):
    class Status(models.TextChoices):
        POSTED = "POSTED", "Posted"
        CREDITED = "CREDITED", "Credited"

    number = models.CharField(max_length=20, unique=True)
    financial_year = models.CharField(max_length=7)
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="invoice")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField()
    customer_gstin = models.CharField(max_length=20, blank=True)
    company_gstin = models.CharField(max_length=20, blank=True)
    company_name = models.CharField(max_length=160, blank=True)
    company_address = models.TextField(blank=True)
    company_state = models.CharField(max_length=80, blank=True)
    company_phone = models.CharField(max_length=30, blank=True)
    company_email = models.EmailField(blank=True)
    place_of_supply = models.CharField(max_length=80)
    taxable_total = models.DecimalField(default=Decimal("0"), **MONEY)
    cgst_total = models.DecimalField(default=Decimal("0"), **MONEY)
    sgst_total = models.DecimalField(default=Decimal("0"), **MONEY)
    igst_total = models.DecimalField(default=Decimal("0"), **MONEY)
    total = models.DecimalField(default=Decimal("0"), **MONEY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]

    def __str__(self):
        return self.number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=160)
    hsn_code = models.CharField(max_length=20)
    quantity = models.DecimalField(**QUANTITY)
    unit_price = models.DecimalField(**MONEY)
    taxable_value = models.DecimalField(**MONEY)
    gst_rate = models.DecimalField(**MONEY)
    cgst = models.DecimalField(default=Decimal("0"), **MONEY)
    sgst = models.DecimalField(default=Decimal("0"), **MONEY)
    igst = models.DecimalField(default=Decimal("0"), **MONEY)
    total = models.DecimalField(**MONEY)


class Dispatch(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="dispatches")
    submission_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    dispatch_date = models.DateField()
    dispatched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class DispatchLine(models.Model):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="lines")
    invoice_line = models.ForeignKey(
        InvoiceLine,
        on_delete=models.PROTECT,
        related_name="dispatch_lines",
    )
    quantity = models.DecimalField(**QUANTITY)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dispatch", "invoice_line"],
                name="unique_invoice_line_per_dispatch",
            ),
        ]


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank transfer"
        UPI = "UPI", "UPI"
        CHEQUE = "CHEQUE", "Cheque"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=10, choices=Method.choices)
    reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class CreditNote(models.Model):
    number = models.CharField(max_length=20, unique=True)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="credit_note")
    issue_date = models.DateField()
    reason = models.CharField(max_length=255)
    taxable_total = models.DecimalField(**MONEY)
    cgst_total = models.DecimalField(**MONEY)
    sgst_total = models.DecimalField(**MONEY)
    igst_total = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


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


class BOMTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    batch_yield_packets = models.PositiveIntegerField(default=200)
    tolerance_pct = models.DecimalField(default=Decimal("3"), **QUANTITY)
    lines = models.ManyToManyField(Item, through="BOMTemplateLine", related_name="bom_templates")

    def __str__(self):
        return self.name


class BOMTemplateLine(models.Model):
    template = models.ForeignKey(BOMTemplate, on_delete=models.CASCADE, related_name="components")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty_per_batch = models.DecimalField(**QUANTITY)
    unit = models.CharField(max_length=20)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "item"],
                name="unique_item_per_bom_template",
            ),
        ]


class CompanySettings(models.Model):
    name = models.CharField(max_length=160, default="NIT Demo Snacks")
    address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True, validators=[validate_gstin])
    state = models.CharField(max_length=80, default="Tamil Nadu")
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
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
    source_doc_type = models.CharField(max_length=30, blank=True)
    source_doc_id = models.PositiveBigIntegerField(null=True, blank=True)

    objects = ImmutableQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit log records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit log records cannot be deleted.")


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
    display_quantity = models.DecimalField(default=Decimal("0"), **QUANTITY)
    display_unit = models.CharField(max_length=20, blank=True)
    unit_cost = models.DecimalField(**MONEY)


class ProductionRun(models.Model):
    number = models.CharField(max_length=40, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    bom = models.ForeignKey("BOM", null=True, blank=True, on_delete=models.PROTECT, related_name="runs")
    batch_count = models.DecimalField(default=Decimal("1"), **QUANTITY)
    expected_output = models.PositiveIntegerField()
    actual_good_output = models.PositiveIntegerField()
    damaged_pouches = models.PositiveIntegerField(default=0)
    labour_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    power_gas_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    material_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    overhead_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    total_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    cost_per_packet = models.DecimalField(default=Decimal("0"), **MONEY)
    margin_per_packet = models.DecimalField(default=Decimal("0"), **MONEY)
    margin_pct = models.DecimalField(default=Decimal("0"), **MONEY)
    run_date = models.DateField()
    posted = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ProductionConsumption(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="consumptions")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    planned_qty = models.DecimalField(**QUANTITY)
    actual_qty = models.DecimalField(**QUANTITY)
    wasted_qty = models.DecimalField(default=Decimal("0"), **QUANTITY)
    variance_pct = models.DecimalField(**QUANTITY)
    material_cost = models.DecimalField(default=Decimal("0"), **MONEY)
    flagged = models.BooleanField(default=False)
