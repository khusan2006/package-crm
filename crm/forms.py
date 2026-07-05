from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import Client, Payment, Product, Sale, SaleItem, StockEntry

DEFAULT_DEBT_DAYS = 7


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


TRANSFER_COMMISSION_PCT = Decimal("1")  # default bank fee suggested for transfers


class DebtPaymentForm(forms.Form):
    amount = forms.DecimalField(
        label="Miqdor (so'm)", max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    method = forms.ChoiceField(
        label="To'lov usuli", choices=Payment.Method.choices, initial=Payment.Method.CASH
    )
    commission_percent = forms.DecimalField(
        label="Bank ushlagan foiz (%)",
        max_digits=5,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        help_text="Faqat bank o'tkazmasi uchun — bank ushlab qoladigan foiz",
    )
    note = forms.CharField(
        label="Izoh",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ixtiyoriy — qo'shimcha ma'lumot"}),
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

    def clean(self):
        cleaned = super().clean()
        percent = cleaned.get("commission_percent") or Decimal("0")
        # Commission only applies to bank transfers; ignore it otherwise
        if cleaned.get("method") != Payment.Method.TRANSFER:
            percent = Decimal("0")
        amount = cleaned.get("amount") or Decimal("0")
        commission = (amount * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if commission > amount:
            self.add_error(
                "commission_percent", "Komissiya to'lov summasidan ko'p bo'lishi mumkin emas."
            )
        cleaned["commission_percent"] = percent
        cleaned["commission"] = commission
        return cleaned


class StockAdjustForm(forms.Form):
    """Set the exact current quantity; the view logs the difference as a movement."""

    quantity = forms.DecimalField(
        label="Yangi miqdor (kg)", max_digits=12, decimal_places=3
    )
    note = forms.CharField(label="Izoh (ixtiyoriy)", max_length=255, required=False)


class SaleForm(forms.ModelForm):
    """The sale receipt header. Every sale is a receivable; if no deadline is
    entered it defaults to the sale date + DEFAULT_DEBT_DAYS."""

    class Meta:
        model = Sale
        fields = ["date", "client", "debt_deadline"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "debt_deadline": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.can_see_all_records:
            self.fields["client"].queryset = Client.objects.filter(owner=user)
        self.fields["debt_deadline"].required = False
        self.fields["debt_deadline"].help_text = (
            f"Bo'sh qolsa — sotuv sanasidan +{DEFAULT_DEBT_DAYS} kun"
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("debt_deadline"):
            base_date = cleaned.get("date") or timezone.localdate()
            cleaned["debt_deadline"] = base_date + timedelta(days=DEFAULT_DEBT_DAYS)
            self.instance.debt_deadline = cleaned["debt_deadline"]
        return cleaned


class SaleItemForm(forms.ModelForm):
    class Meta:
        model = SaleItem
        fields = ["product", "dimension", "weight", "price", "cost_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["cost_price"].required = False
        self.fields["cost_price"].widget.attrs["placeholder"] = "Bo'sh qolsa — mahsulot tannarxi"

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        dimension = cleaned.get("dimension")
        # Empty cost price falls back to the product's cost, converted to the sale unit
        if product and dimension and not cleaned.get("cost_price"):
            cleaned["cost_price"] = product.cost_price_for(dimension)
        return cleaned


SaleItemFormSet = inlineformset_factory(
    Sale,
    SaleItem,
    form=SaleItemForm,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)
