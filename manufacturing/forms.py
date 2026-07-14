from django import forms

from accounts.models import User
from crm.forms import _mark_money, _searchable_select
from crm.models import Product

from .models import (
    MaterialPurchase, ProductionRun, ProductionRunItem, RawMaterial,
    SellerStockEntry, StockTransfer,
)

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class RawMaterialForm(forms.ModelForm):
    class Meta:
        model = RawMaterial
        fields = ["name", "sku", "note", "low_stock_threshold", "is_active"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class MaterialPurchaseForm(forms.ModelForm):
    class Meta:
        model = MaterialPurchase
        fields = ["material", "date", "quantity_kg", "price_per_kg", "method", "supplier", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.filter(is_active=True)
        _searchable_select(self.fields["material"], "Xomashyo tanlang")
        _mark_money(self.fields["price_per_kg"])


class ProductionRunForm(forms.ModelForm):
    class Meta:
        model = ProductionRun
        fields = ["product", "date", "output_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")


class ProductionRunItemForm(forms.ModelForm):
    class Meta:
        model = ProductionRunItem
        fields = ["material", "quantity_kg"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.filter(is_active=True)
        _searchable_select(self.fields["material"], "Xomashyo")


ProductionRunItemFormSet = forms.inlineformset_factory(
    ProductionRun, ProductionRunItem, form=ProductionRunItemForm,
    extra=1, can_delete=True, min_num=1, validate_min=True,
)


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ["product", "seller", "date", "quantity_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["seller"].queryset = User.objects.filter(role=User.Role.SALES)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")
        _searchable_select(self.fields["seller"], "Sotuvchi tanlang")


class SellerStockEntryForm(forms.ModelForm):
    class Meta:
        model = SellerStockEntry
        fields = ["product", "date", "quantity_kg", "note"]
        widgets = {"date": _DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulot tanlang")
        self.fields["quantity_kg"].help_text = "Qo'shish uchun musbat, hisobdan chiqarish uchun manfiy"
