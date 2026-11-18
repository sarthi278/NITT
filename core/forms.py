from decimal import Decimal

from django import forms
from django.utils import timezone

from core.models import Item, Supplier
from core.services.units import to_item_base


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
        self.fields["item"].queryset = Item.objects.order_by("name")
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
