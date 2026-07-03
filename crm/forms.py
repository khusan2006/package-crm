from django import forms

from .models import Client, Product, Sale


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "company", "email", "phone", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "description", "cost_price", "price", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class SaleForm(forms.ModelForm):
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
