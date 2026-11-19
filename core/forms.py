from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseFormSet, BaseInlineFormSet, formset_factory, inlineformset_factory
from django.utils import timezone

from core.models import (
    BOMLine,
    BOM,
    BOMTemplate,
    Customer,
    CustomerPrice,
    Item,
    Invoice,
    Payment,
    ProductionConsumption,
    Product,
    ProductPackSize,
    PriceList,
    PriceListItem,
    PurchaseLine,
    Supplier,
    UnitConversion,
)
from core.services.reports import REPORTS
from core.services.units import item_unit_multiplier, to_item_base


def item_has_business_history(item):
    return (
        item.movements.exists()
        or item.stock_inwards.exists()
        or item.batches.exists()
        or PurchaseLine.objects.filter(item=item).exists()
        or ProductionConsumption.objects.filter(item=item).exists()
        or BOMLine.objects.filter(item=item).exists()
    )


class ItemForm(forms.ModelForm):
    gst_rate = forms.DecimalField(
        label="GST rate (%)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    )
    reorder_level = forms.DecimalField(
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0"),
        help_text="Quantity in the item's base unit.",
    )

    class Meta:
        model = Item
        fields = ["name", "sku", "item_type", "base_unit", "hsn_code", "gst_rate", "reorder_level"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "sku": forms.TextInput(attrs={"autocomplete": "off"}),
            "hsn_code": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Item name"
        self.fields["sku"].label = "SKU / item code"
        self.fields["item_type"].label = "Item type"
        self.fields["base_unit"].label = "Base unit"
        self.fields["hsn_code"].label = "HSN code"
        self.fields["reorder_level"].label = "Reorder level"
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise ValidationError("Item name is required.")
        return name

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip()
        if not sku:
            raise ValidationError("SKU / item code is required.")
        return sku

    def clean_base_unit(self):
        base_unit = self.cleaned_data["base_unit"]
        if (
            self.instance.pk
            and base_unit != self.instance.base_unit
            and item_has_business_history(self.instance)
        ):
            raise ValidationError(
                "Base unit cannot be changed after this item has been used."
            )
        return base_unit


class UnitConversionForm(forms.ModelForm):
    to_base_multiplier = forms.DecimalField(
        label="Multiplier to base unit",
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )

    class Meta:
        model = UnitConversion
        fields = ["unit", "to_base_multiplier"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].label = "Unit name"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_unit(self):
        return self.cleaned_data["unit"].strip()


class ItemConversionFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        base_unit = self.instance.base_unit
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            if (
                form.cleaned_data.get("unit") == base_unit
                and form.cleaned_data.get("to_base_multiplier") != Decimal("1")
            ):
                form.add_error(
                    "to_base_multiplier",
                    "A conversion using the base unit must have a multiplier of 1.",
                )


ItemConversionFormSetFactory = inlineformset_factory(
    Item,
    UnitConversion,
    form=UnitConversionForm,
    formset=ItemConversionFormSet,
    extra=1,
    can_delete=True,
)


class OrderLineForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    quantity = forms.DecimalField(
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            finished_item__is_active=True,
            is_active=True,
        ).order_by("name")
        self.fields["product"].label = "Product"
        self.fields["quantity"].label = "Packets"
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class OrderLineFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(
            form.cleaned_data.get("product")
            for form in self.forms
            if form.cleaned_data
        ):
            raise ValidationError("Add at least one product to the order.")


OrderLineFormSetFactory = formset_factory(
    OrderLineForm,
    formset=OrderLineFormSet,
    extra=2,
)


class PaymentForm(forms.Form):
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.none())
    payment_date = forms.DateField()
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    method = forms.ChoiceField(choices=Payment.Method.choices)
    reference = forms.CharField(max_length=100, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice"].queryset = Invoice.objects.filter(
            status=Invoice.Status.POSTED,
        ).select_related("customer").order_by("invoice_date", "number")
        self.fields["payment_date"].initial = timezone.localdate()
        self.fields["payment_date"].widget = forms.DateInput(attrs={"type": "date"})
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class ReportFilterForm(forms.Form):
    report = forms.ChoiceField(choices=REPORTS)
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    export = forms.ChoiceField(
        choices=(("", "View report"), ("csv", "CSV"), ("xlsx", "Excel"), ("pdf", "PDF")),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["report"].initial = "sales"
        self.fields["start_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["end_date"].widget = forms.DateInput(attrs={"type": "date"})
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise ValidationError("Start date must be on or before end date.")
        return cleaned_data


class StockInwardForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    item = forms.ModelChoiceField(queryset=Item.objects.none())
    quantity = forms.DecimalField(
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    unit = forms.CharField(max_length=20)
    unit_cost = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
    )
    batch_no = forms.CharField(max_length=80, required=False)
    manufacturing_date = forms.DateField(required=False)
    expiry_date = forms.DateField(required=False)
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.none(),
        required=False,
    )
    entry_date = forms.DateField()
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.objects.filter(is_active=True).order_by("name")
        self.fields["supplier"].queryset = Supplier.objects.order_by("name")
        self.fields["entry_date"].initial = timezone.localdate()
        self.fields["manufacturing_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["expiry_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["entry_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["remarks"].widget.attrs["class"] = "form-control"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(
                field.widget, forms.Select
            ) else "form-control"

    def clean_batch_no(self):
        return self.cleaned_data["batch_no"].strip()

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        quantity = cleaned_data.get("quantity")
        unit = cleaned_data.get("unit")
        batch_no = cleaned_data.get("batch_no", "")
        manufacturing_date = cleaned_data.get("manufacturing_date")
        expiry_date = cleaned_data.get("expiry_date")
        entry_date = cleaned_data.get("entry_date")

        if item and quantity is not None and unit:
            try:
                to_item_base(quantity, item, unit)
            except ValueError as error:
                self.add_error("unit", str(error))

        if (manufacturing_date or expiry_date) and not batch_no:
            self.add_error("batch_no", "Enter a batch number when supplying batch dates.")
        if entry_date and manufacturing_date and manufacturing_date > entry_date:
            self.add_error(
                "manufacturing_date",
                "Manufacturing date cannot be after the stock entry date.",
            )
        if entry_date and expiry_date and expiry_date < entry_date:
            self.add_error("expiry_date", "Expired stock cannot be received.")
        if manufacturing_date and expiry_date and expiry_date <= manufacturing_date:
            self.add_error("expiry_date", "Expiry date must be after manufacturing date.")

        return cleaned_data


class ProductForm(forms.ModelForm):
    bom_template = forms.ModelChoiceField(
        queryset=BOMTemplate.objects.order_by("name"),
        required=False,
        help_text="Selecting a template replaces this product's current BOM lines.",
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "finished_item",
            "hsn_code",
            "gst_rate",
            "selling_price",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        finished_items = Item.objects.filter(item_type=Item.ItemType.FINISHED)
        if self.instance.pk and self.instance.finished_item_id:
            finished_items = finished_items.filter(
                Q(is_active=True) | Q(pk=self.instance.finished_item_id)
            )
        else:
            finished_items = finished_items.filter(is_active=True)
        self.fields["finished_item"].queryset = finished_items.order_by("name")
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        finished_item = cleaned_data.get("finished_item")
        if (
            finished_item
            and not finished_item.is_active
            and cleaned_data.get("is_active")
        ):
            self.add_error("finished_item", "An active product needs an active finished item.")
        return cleaned_data


class BOMForm(forms.ModelForm):
    batch_yield_packets = forms.IntegerField(min_value=1)
    tolerance_pct = forms.DecimalField(
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0"),
    )

    class Meta:
        model = BOM
        fields = ["batch_yield_packets", "tolerance_pct"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class BOMLineForm(forms.ModelForm):
    qty_per_batch = forms.DecimalField(
        max_digits=16,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )

    class Meta:
        model = BOMLine
        fields = ["item", "qty_per_batch", "unit"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        items = Item.objects.exclude(item_type=Item.ItemType.FINISHED)
        if self.instance.pk and self.instance.item_id:
            items = items.filter(Q(is_active=True) | Q(pk=self.instance.item_id))
        else:
            items = items.filter(is_active=True)
        self.fields["item"].queryset = items.order_by("name")
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        unit = cleaned_data.get("unit")
        if item and unit:
            try:
                item_unit_multiplier(item, unit)
            except ValueError as error:
                self.add_error("unit", str(error))
        return cleaned_data


class BOMLineFormSet(BaseInlineFormSet):
    def add_formset_error(self, message):
        self.non_form_errors().append(message)


BOMLineFormSetFactory = inlineformset_factory(
    BOM,
    BOMLine,
    form=BOMLineForm,
    formset=BOMLineFormSet,
    extra=1,
    can_delete=True,
    max_num=30,
    validate_max=True,
)


class ProductPackSizeForm(forms.ModelForm):
    packet_count = forms.IntegerField(min_value=1)

    class Meta:
        model = ProductPackSize
        fields = ["name", "packet_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


ProductPackSizeFormSetFactory = inlineformset_factory(
    Product,
    ProductPackSize,
    form=ProductPackSizeForm,
    extra=1,
    can_delete=True,
    max_num=10,
    validate_max=True,
)


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name",
            "customer_type",
            "gstin",
            "state",
            "address",
            "phone",
            "email",
            "assigned_salesman",
            "price_list",
            "is_active",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        salesman = self.fields["assigned_salesman"].queryset.filter(
            groups__name="Salesman",
        )
        if self.instance.pk and self.instance.assigned_salesman_id:
            salesman = self.fields["assigned_salesman"].queryset.filter(
                Q(groups__name="Salesman")
                | Q(pk=self.instance.assigned_salesman_id)
            )
        self.fields["assigned_salesman"].queryset = salesman.distinct().order_by("username")
        price_lists = PriceList.objects.filter(is_active=True)
        if self.instance.pk and self.instance.price_list_id:
            price_lists = PriceList.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.price_list_id)
            )
        self.fields["price_list"].queryset = price_lists.order_by("name")
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class CustomerPriceForm(forms.ModelForm):
    class Meta:
        model = CustomerPrice
        fields = ["product", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.order_by("name")
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


CustomerPriceFormSetFactory = inlineformset_factory(
    Customer,
    CustomerPrice,
    form=CustomerPriceForm,
    extra=1,
    can_delete=True,
    max_num=30,
    validate_max=True,
)


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "gstin", "state", "address", "phone", "email", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class PriceListForm(forms.ModelForm):
    class Meta:
        model = PriceList
        fields = ["name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class PriceListItemForm(forms.ModelForm):
    class Meta:
        model = PriceListItem
        fields = ["product", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True).order_by("name")


PriceListItemFormSetFactory = inlineformset_factory(
    PriceList,
    PriceListItem,
    form=PriceListItemForm,
    extra=1,
    can_delete=True,
    max_num=50,
    validate_max=True,
)
