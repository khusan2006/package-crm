from decimal import Decimal

from django import forms

from .models import Client, Payment, Product, Sale, StockEntry


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "company", "email", "phone", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "description", "cost_price", "price", "low_stock_threshold", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class StockEntryForm(forms.ModelForm):
    class Meta:
        model = StockEntry
        fields = ["date", "quantity_kg", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class DebtPaymentForm(forms.Form):
    amount = forms.DecimalField(
        label="Miqdor (so'm)", max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    method = forms.ChoiceField(
        label="To'lov usuli", choices=Payment.Method.choices, initial=Payment.Method.CASH
    )

    def __init__(self, *args, max_amount=None, **kwargs):
        self.max_amount = max_amount
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.max_amount is not None and amount > self.max_amount:
            raise forms.ValidationError(
                f"Qoldiqdan ({self.max_amount:.0f} so'm) ko'p bo'lishi mumkin emas."
            )
        return amount


class StockAdjustForm(forms.Form):
    """Set the exact current quantity; the view logs the difference as a movement."""

    quantity = forms.DecimalField(
        label="Yangi miqdor (kg)", max_digits=12, decimal_places=3
    )
    note = forms.CharField(label="Izoh (ixtiyoriy)", max_length=255, required=False)


class SaleForm(forms.ModelForm):
    payment_method = forms.ChoiceField(
        label="To'lov usuli",
        choices=Payment.Method.choices,
        initial=Payment.Method.CASH,
        required=False,
    )

    class Meta:
        model = Sale
        fields = [
            "date", "client", "product", "dimension", "weight", "price",
            "cost_price", "is_debt", "debt_deadline",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "debt_deadline": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        if user is not None and not user.can_see_all_records:
            self.fields["client"].queryset = Client.objects.filter(owner=user)
        self.fields["cost_price"].required = False
        self.fields["cost_price"].widget.attrs["placeholder"] = "Bo'sh qolsa — mahsulot tannarxi"

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        dimension = cleaned.get("dimension")
        # Empty cost price falls back to the product's cost, converted to the sale unit
        if product and dimension and not cleaned.get("cost_price"):
            cost = product.cost_price_for(dimension)
            cleaned["cost_price"] = cost
            self.instance.cost_price = cost
        if cleaned.get("is_debt") and not cleaned.get("debt_deadline"):
            self.add_error("debt_deadline", "Qarzga sotilganda muddat kiritilishi shart.")
        if not cleaned.get("is_debt"):
            cleaned["debt_deadline"] = None
            self.instance.debt_deadline = None
        return cleaned
