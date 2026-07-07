from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import Client, Expense, Payment, Product, Return, Sale, SaleItem, StockEntry

DEFAULT_DEBT_DAYS = 7


class ClientForm(forms.ModelForm):
    allow_duplicate = forms.BooleanField(
        label="Bir xil nomli mijoz bo'lsa ham, baribir qo'shilsin",
        required=False,
    )

    class Meta:
        model = Client
        fields = ["name", "company", "email", "phone", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, check_duplicates=True, **kwargs):
        self.user = user
        self.check_duplicates = check_duplicates
        super().__init__(*args, **kwargs)
        # The override checkbox is only meaningful when creating a new client
        if not check_duplicates:
            self.fields.pop("allow_duplicate", None)

    def clean(self):
        cleaned = super().clean()
        if not self.check_duplicates or cleaned.get("allow_duplicate"):
            return cleaned
        match = Client.find_duplicate(
            self.user, cleaned.get("name", ""), exclude_pk=self.instance.pk
        )
        if match:
            raise forms.ValidationError(
                f"“{match.name}” nomli mijoz allaqachon bor. Agar bu boshqa mijoz "
                f"bo'lsa, quyidagi katakchani belgilab qayta saqlang."
            )
        return cleaned


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
        label="Miqdor", max_digits=18, decimal_places=2, min_value=Decimal("0.01"),
        help_text="Tanlangan valyutada — dollar tanlansa, dollardagi summa",
    )
    currency = forms.ChoiceField(
        label="Valyuta",
        choices=Payment.Currency.choices,
        initial=Payment.Currency.UZS,
        required=False,
    )
    exchange_rate = forms.DecimalField(
        label="Dollar kursi (1$ = so'm)",
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text="Faqat dollar to'lovi uchun — har safar qo'lda kiritiladi",
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

    def clean(self):
        cleaned = super().clean()
        # Convert the entered amount to so'm — the base currency the debt lives in.
        # A dollar payment is amount(USD) × rate; a so'm payment passes through.
        currency = cleaned.get("currency") or Payment.Currency.UZS
        entered = cleaned.get("amount") or Decimal("0")
        rate = cleaned.get("exchange_rate") or Decimal("0")
        if currency == Payment.Currency.USD:
            if rate <= 0:
                self.add_error("exchange_rate", "Dollar to'lovi uchun kursni kiriting.")
                rate = Decimal("0")
            amount = (entered * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            rate = Decimal("0")
            amount = entered

        percent = cleaned.get("commission_percent") or Decimal("0")
        # Commission only applies to bank transfers; ignore it otherwise
        if cleaned.get("method") != Payment.Method.TRANSFER:
            percent = Decimal("0")
        commission = (amount * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if commission > amount:
            self.add_error(
                "commission_percent", "Komissiya to'lov summasidan ko'p bo'lishi mumkin emas."
            )
        # Only the net (amount − commission) pays down the debt, so the net —
        # not the gross — is what may not exceed the outstanding balance. This
        # lets a transfer be grossed up to cover the bank fee and still settle.
        net = amount - commission
        if self.max_amount is not None and net > self.max_amount:
            self.add_error(
                "amount", f"Qoldiqdan ({self.max_amount:.0f} so'm) ko'p bo'lishi mumkin emas."
            )
        cleaned["amount"] = amount  # canonical so'm value the views persist
        cleaned["amount_original"] = entered  # the physical figure (dollars for USD)
        cleaned["currency"] = currency
        cleaned["exchange_rate"] = rate
        cleaned["commission_percent"] = percent
        cleaned["commission"] = commission
        return cleaned


class ExpenseForm(forms.ModelForm):
    """A payout from the till. `method` records which wallet it left (cash/card/bank);
    `currency` records whether it left the so'm or the dollar till. A dollar expense is
    entered in dollars with a hand-typed rate and converted to a so'm `amount`."""

    class Meta:
        model = Expense
        fields = ["date", "amount", "currency", "exchange_rate", "category", "method", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — nima uchun"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Miqdor"
        self.fields["amount"].help_text = "Tanlangan valyutada — dollar tanlansa, dollardagi summa"
        self.fields["exchange_rate"].required = False
        self.fields["exchange_rate"].help_text = "Faqat dollar chiqimi uchun — qo'lda kiritiladi"
        # Editing a dollar expense: show the original dollars in the amount field
        # (not the stored so'm), so re-saving converts at the rate correctly.
        if self.instance.pk and self.instance.currency == Payment.Currency.USD:
            self.initial["amount"] = self.instance.amount_original

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Summa 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        cleaned = super().clean()
        # Convert the entered amount to so'm — the base the kassa/profit math uses.
        currency = cleaned.get("currency") or Payment.Currency.UZS
        entered = cleaned.get("amount") or Decimal("0")
        rate = cleaned.get("exchange_rate") or Decimal("0")
        if currency == Payment.Currency.USD:
            if rate <= 0:
                self.add_error("exchange_rate", "Dollar chiqimi uchun kursni kiriting.")
                rate = Decimal("0")
            som = (entered * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            rate = Decimal("0")
            som = entered
        # `amount` persists as so'm (via cleaned); `amount_original` keeps the
        # physical figure and isn't a form field, so it's set on the instance here.
        self.instance.amount_original = entered
        cleaned["amount"] = som
        cleaned["exchange_rate"] = rate
        return cleaned


class StockAdjustForm(forms.Form):
    """Set the exact current quantity; the view logs the difference as a movement."""

    quantity = forms.DecimalField(
        label="Yangi miqdor (kg)", max_digits=12, decimal_places=3
    )
    note = forms.CharField(label="Izoh (ixtiyoriy)", max_length=255, required=False)


class SaleForm(forms.ModelForm):
    """The sale receipt header. Every sale is a receivable; if no deadline is
    entered it defaults to the sale date + DEFAULT_DEBT_DAYS. On create, an
    optional immediate payment (any currency) can settle it right away."""

    # Optional "pay now" block, only added when creating a sale (with_payment).
    pay_amount = forms.DecimalField(
        label="Darhol to'lov (ixtiyoriy)", max_digits=18, decimal_places=2,
        required=False, min_value=Decimal("0.01"),
        help_text="Bo'sh qolsa — sotuv qarz sifatida qoladi",
    )
    pay_currency = forms.ChoiceField(
        label="Valyuta", choices=Payment.Currency.choices,
        initial=Payment.Currency.UZS, required=False,
    )
    pay_exchange_rate = forms.DecimalField(
        label="Dollar kursi (1$ = so'm)", max_digits=12, decimal_places=2,
        required=False, min_value=Decimal("0"),
        help_text="Faqat dollar to'lovi uchun — qo'lda kiritiladi",
    )
    pay_method = forms.ChoiceField(
        label="To'lov usuli", choices=Payment.Method.choices,
        initial=Payment.Method.CASH, required=False,
    )

    class Meta:
        model = Sale
        fields = ["date", "client", "debt_deadline"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "debt_deadline": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, with_payment=False, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.can_see_all_records:
            self.fields["client"].queryset = Client.objects.filter(owner=user)
        self.fields["debt_deadline"].required = False
        self.fields["debt_deadline"].help_text = (
            f"Bo'sh qolsa — sotuv sanasidan +{DEFAULT_DEBT_DAYS} kun"
        )
        self.fields["client"].widget.attrs["data-combobox"] = ""
        # The pay-now block only makes sense when creating; drop it on edit.
        if not with_payment:
            for name in ("pay_amount", "pay_currency", "pay_exchange_rate", "pay_method"):
                self.fields.pop(name, None)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("debt_deadline"):
            base_date = cleaned.get("date") or timezone.localdate()
            cleaned["debt_deadline"] = base_date + timedelta(days=DEFAULT_DEBT_DAYS)
            self.instance.debt_deadline = cleaned["debt_deadline"]
        # Convert an optional immediate payment to so'm (the "≤ sale total" check
        # needs the line items, so it lives in the view). pay_som == 0 means none.
        cleaned["pay_som"] = Decimal("0")
        if "pay_amount" in self.fields and cleaned.get("pay_amount"):
            amount = cleaned["pay_amount"]
            currency = cleaned.get("pay_currency") or Payment.Currency.UZS
            rate = cleaned.get("pay_exchange_rate") or Decimal("0")
            if currency == Payment.Currency.USD:
                if rate <= 0:
                    self.add_error("pay_exchange_rate", "Dollar to'lovi uchun kursni kiriting.")
                    rate = Decimal("0")
                som = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                rate = Decimal("0")
                som = amount
            cleaned["pay_som"] = som
            cleaned["pay_original"] = amount
            cleaned["pay_rate"] = rate
            cleaned["pay_currency"] = currency
            cleaned["pay_method"] = cleaned.get("pay_method") or Payment.Method.CASH
        return cleaned


class SaleItemForm(forms.ModelForm):
    class Meta:
        model = SaleItem
        fields = ["product", "dimension", "weight", "price", "cost_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["product"].widget.attrs["data-combobox"] = ""
        self.fields["cost_price"].required = False
        self.fields["cost_price"].widget.attrs["placeholder"] = "Bo'sh qolsa — mahsulot tannarxi"

    def clean_weight(self):
        weight = self.cleaned_data.get("weight")
        if weight is not None and weight <= 0:
            raise forms.ValidationError("Og'irlik 0 dan katta bo'lishi kerak.")
        return weight

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is not None and price <= 0:
            raise forms.ValidationError("Narx 0 dan katta bo'lishi kerak.")
        return price

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


class ReturnForm(forms.ModelForm):
    """Record goods returned on a sale. Products are limited to those actually on
    the sale, and the returned quantity can't exceed what was sold (net of prior
    returns)."""

    class Meta:
        model = Return
        fields = ["product", "dimension", "weight", "price", "restock", "note"]
        widgets = {"note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy"})}

    def __init__(self, *args, sale=None, **kwargs):
        self.sale = sale
        super().__init__(*args, **kwargs)
        if sale is not None:
            self.fields["product"].queryset = Product.objects.filter(
                sale_items__sale=sale
            ).distinct()
        self.fields["product"].widget.attrs["data-combobox"] = ""

    def clean_weight(self):
        weight = self.cleaned_data.get("weight")
        if weight is not None and weight <= 0:
            raise forms.ValidationError("Og'irlik 0 dan katta bo'lishi kerak.")
        return weight

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is not None and price <= 0:
            raise forms.ValidationError("Narx 0 dan katta bo'lishi kerak.")
        return price

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        weight = cleaned.get("weight")
        dimension = cleaned.get("dimension")
        if self.sale and product and weight and dimension:
            weight_kg = (
                weight / Decimal("1000") if dimension == Sale.Dimension.G else weight
            )
            sold_kg = sum(
                (i.weight_kg for i in self.sale.items.all() if i.product_id == product.pk),
                Decimal("0"),
            )
            already_kg = sum(
                (r.weight_kg for r in self.sale.returns.all() if r.product_id == product.pk),
                Decimal("0"),
            )
            if weight_kg + already_kg > sold_kg:
                raise forms.ValidationError(
                    "Qaytarilayotgan miqdor sotilganidan ko'p bo'lishi mumkin emas."
                )
        return cleaned
