from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from core.models import (
    BOMLine,
    Item,
    ProductionConsumption,
    PurchaseLine,
    Supplier,
    UnitConversion,
)
from core.services.units import to_item_base


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
