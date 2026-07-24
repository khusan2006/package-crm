import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from accounts.models import User

from .models import (
    Client,
    Expense,
    Payment,
    Product,
    ProductionReceipt,
    ProductionReceiptItem,
    ProductionRemittance,
    ProfitPayout,
    Return,
    Sale,
    SaleItem,
    StockEntry,
    seller_cash_on_hand,
    seller_withdrawable_profit,
)

DEFAULT_DEBT_DAYS = 7

# Marks an amount field so the frontend groups it as "1 000 000" while typing.
# The raw numeric value is restored before submit, so nothing changes server-side.
MONEY_WIDGET_ATTRS = {"data-money": "", "inputmode": "decimal"}


def _mark_money(*fields):
    """Attach the money-input marker to the given bound form fields."""
    for field in fields:
        if field is not None:
            field.widget.attrs.update(MONEY_WIDGET_ATTRS)


def _searchable_select(field, placeholder=""):
    """Turn a model-choice field into a searchable combobox picker: drop Django's
    "---------" blank label so the box shows `placeholder` instead of a dashed
    row, and mark it for the front-end enhancement. The empty choice stays in the
    <select> (so a required field still forces a real pick), but the combobox
    hides that blank row and shows the placeholder in the input."""
    field.empty_label = ""
    field.widget.attrs["data-combobox"] = ""
    if placeholder:
        field.widget.attrs["data-placeholder"] = placeholder


class ClientForm(forms.ModelForm):
    allow_duplicate = forms.BooleanField(
        label="Bir xil nomli mijoz bo'lsa ham, baribir qo'shilsin",
        required=False,
    )

    class Meta:
        model = Client
        fields = ["name", "company", "owner", "phone", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, check_duplicates=True, **kwargs):
        self.user = user
        self.check_duplicates = check_duplicates
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs["data-phone"] = ""
        # "Mas'ul xodim" — which employee this client is attached to. Only
        # admins/managers assign it across the team; a seller's clients stay
        # owned by themselves (the view fills that in), so drop the field for them.
        if user is not None and user.can_see_all_records:
            self.fields["owner"].label = "Mas'ul xodim"
            self.fields["owner"].queryset = User.objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
            _searchable_select(self.fields["owner"], "Xodimni tanlang")
            if user is not None and not self.instance.pk:
                self.fields["owner"].initial = user.pk
        else:
            self.fields.pop("owner", None)
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
    """The product catalog form — a plain reference list (nomi, narx, tannarx). No
    stock/qoldiq: goods aren't received into a warehouse anymore, they're just sold."""

    class Meta:
        model = Product
        fields = [
            "name", "sku", "description", "cost_price", "price",
            "has_size", "has_micron", "is_active",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, with_stock=False, **kwargs):
        # `with_stock` kept for signature compatibility with older callers; ignored.
        super().__init__(*args, **kwargs)
        _mark_money(self.fields["cost_price"], self.fields["price"])


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
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])

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


class PaymentEditForm(forms.ModelForm):
    """Fix a mistaken payment (Kirim). Full edit of a single receipt: amount,
    currency + rate, method, bank commission and note. `amount` persists as so'm
    (a dollar payment is entered in dollars and converted at the hand-typed rate);
    `net` (amount − commission) is what pays down the debt, so it may not exceed
    `max_amount` — the sale's remaining plus whatever this payment already covers,
    which keeps the sale from becoming over-paid. `kind`/`sale`/`created_by` are
    fixed; only the money figures are editable."""

    class Meta:
        model = Payment
        fields = ["date", "amount", "currency", "exchange_rate", "method",
                  "commission_percent", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — qo'shimcha ma'lumot"}),
        }

    def __init__(self, *args, max_amount=None, **kwargs):
        self.max_amount = max_amount
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Miqdor"
        self.fields["amount"].help_text = "Tanlangan valyutada — dollar tanlansa, dollardagi summa"
        self.fields["currency"].required = False
        self.fields["exchange_rate"].required = False
        self.fields["exchange_rate"].help_text = "Faqat dollar to'lovi uchun — har safar qo'lda kiritiladi"
        self.fields["commission_percent"].required = False
        self.fields["commission_percent"].help_text = "Faqat bank o'tkazmasi uchun — bank ushlab qoladigan foiz"
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])
        # Editing a dollar payment: show the original dollars in the amount field
        # (not the stored so'm), so re-saving converts at the rate correctly.
        if self.instance.pk and self.instance.currency == Payment.Currency.USD:
            self.initial["amount"] = self.instance.amount_original

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Miqdor 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        cleaned = super().clean()
        # Convert the entered amount to so'm — the base currency the debt lives in.
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
        # Commission only applies to bank transfers; ignore it otherwise.
        if cleaned.get("method") != Payment.Method.TRANSFER:
            percent = Decimal("0")
        commission = (amount * percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if commission > amount:
            self.add_error(
                "commission_percent", "Komissiya to'lov summasidan ko'p bo'lishi mumkin emas."
            )
        # Only the net (amount − commission) pays down the debt, so the net — not
        # the gross — is what may not exceed the room left on the sale.
        net = amount - commission
        if self.max_amount is not None and net > self.max_amount:
            self.add_error(
                "amount", f"Qoldiqdan ({self.max_amount:.0f} so'm) ko'p bo'lishi mumkin emas."
            )
        # `amount`/`exchange_rate`/`commission_percent` are form fields (persisted
        # via cleaned); `commission`/`amount_original` are not, so set them on the
        # instance directly.
        self.instance.commission = commission
        self.instance.amount_original = entered
        cleaned["amount"] = amount
        cleaned["exchange_rate"] = rate
        cleaned["commission_percent"] = percent
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
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])
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


class ProductionRemittanceForm(forms.ModelForm):
    """A seller handing collected cash back to production. So'm only — the debt it
    repays is a so'm figure. A seller records only their own handovers, so for a
    non-privileged user the `seller` field is fixed to themselves and hidden."""

    class Meta:
        model = ProductionRemittance
        fields = ["date", "seller", "amount", "method", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — izoh"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["amount"].label = "Summa (so'm)"
        _mark_money(self.fields["amount"])
        sellers = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        self.fields["seller"].queryset = sellers
        _searchable_select(self.fields["seller"], "Sotuvchini tanlang")
        # A seller only ever hands over their own cash: lock the picker to them.
        if user is not None and not user.can_see_all_records:
            self.fields["seller"].queryset = sellers.filter(pk=user.pk)
            self.fields["seller"].initial = user
            self.fields["seller"].disabled = True

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Summa 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        cleaned = super().clean()
        # Can't hand over more cash than the seller's till actually holds — otherwise
        # the kassa would go negative. A seller's `seller` field is disabled, so its
        # value comes from the initial (themselves); admins pick it explicitly.
        seller = cleaned.get("seller")
        if self.user is not None and not self.user.can_see_all_records:
            seller = self.user
        amount = cleaned.get("amount")
        if seller is not None and amount:
            available = seller_cash_on_hand(seller, exclude_remittance_pk=self.instance.pk)
            if amount > available:
                raise forms.ValidationError(
                    f"Kassada yetarli pul yo'q. {seller} qo'lida hozir "
                    f"{available:,.0f} so'm bor — {amount:,.0f} so'm topshirib bo'lmaydi."
                )
        return cleaned


class ProfitPayoutForm(forms.ModelForm):
    """A seller handing realized profit up to the boss (Foyda topshirish). So'm only,
    and structurally a twin of ProductionRemittanceForm — a non-privileged user's
    `seller` is fixed to themselves and hidden. The amount can't exceed the profit
    actually sitting in the till (cash on hand beyond the production debt)."""

    class Meta:
        model = ProfitPayout
        fields = ["date", "seller", "amount", "method", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — izoh"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["amount"].label = "Summa (so'm)"
        _mark_money(self.fields["amount"])
        sellers = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        self.fields["seller"].queryset = sellers
        _searchable_select(self.fields["seller"], "Sotuvchini tanlang")
        if user is not None and not user.can_see_all_records:
            self.fields["seller"].queryset = sellers.filter(pk=user.pk)
            self.fields["seller"].initial = user
            self.fields["seller"].disabled = True

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Summa 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        cleaned = super().clean()
        # Can only hand over profit that's actually in the till (cash beyond the
        # production debt) — so it never eats into what's still owed to production.
        seller = cleaned.get("seller")
        if self.user is not None and not self.user.can_see_all_records:
            seller = self.user
        amount = cleaned.get("amount")
        if seller is not None and amount:
            available = seller_withdrawable_profit(seller, exclude_payout_pk=self.instance.pk)
            if amount > available:
                raise forms.ValidationError(
                    f"Topshirish uchun yetarli foyda yo'q. {seller} kassasida hozir "
                    f"{available:,.0f} so'm sof foyda bor — {amount:,.0f} so'm topshirib bo'lmaydi."
                )
        return cleaned


class ProductionReceiptForm(forms.ModelForm):
    """Header of a production→seller goods handover. A seller logs only their own
    receipts, so for a non-privileged user the `seller` field is fixed to
    themselves and disabled (mirrors ProductionRemittanceForm)."""

    class Meta:
        model = ProductionReceipt
        fields = ["date", "seller", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — izoh"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        sellers = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        self.fields["seller"].queryset = sellers
        _searchable_select(self.fields["seller"], "Sotuvchini tanlang")
        if user is not None and not user.can_see_all_records:
            self.fields["seller"].queryset = sellers.filter(pk=user.pk)
            self.fields["seller"].initial = user
            self.fields["seller"].disabled = True


class ProductionReceiptItemForm(forms.ModelForm):
    class Meta:
        model = ProductionReceiptItem
        fields = ["product", "quantity_kg"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulotni tanlang")

    def clean_quantity_kg(self):
        qty = self.cleaned_data.get("quantity_kg")
        # Negatives are allowed (an admin write-off); only zero is meaningless.
        if qty is not None and qty == 0:
            raise forms.ValidationError("Miqdor 0 bo'lishi mumkin emas.")
        return qty


ProductionReceiptItemFormSet = inlineformset_factory(
    ProductionReceipt,
    ProductionReceiptItem,
    form=ProductionReceiptItemForm,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class StockAdjustForm(forms.Form):
    """Set the exact current quantity; the view logs the difference as a movement."""

    quantity = forms.DecimalField(
        label="Yangi miqdor (kg)", max_digits=12, decimal_places=3
    )
    note = forms.CharField(label="Izoh (ixtiyoriy)", max_length=255, required=False)


class ClientSelect(forms.Select):
    """A client picker whose <option>s carry the data the front-end combobox
    needs to search by name, phone or address (and combinations of them):

    - ``data-search``: a lowercased haystack of name + company + phone + address,
      plus a digits-only copy of the phone so "998901234567" matches a stored
      "+998 90 123 45 67". The combobox keeps an option when every typed word is
      a substring of this — so "Ali Chilonzor" (name + address) narrows too.
    - ``data-subtitle``: "phone · address" for the muted second line in results.

    The blank "— choose —" option has no client instance and is left untouched.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        client = getattr(value, "instance", None)
        if client is not None:
            phone = client.phone or ""
            digits = re.sub(r"\D", "", phone)
            parts = [p for p in (client.name, client.company, phone, client.address) if p]
            if digits:
                parts.append(digits)
            option["attrs"]["data-search"] = " ".join(parts).lower()
            subtitle = " · ".join(p for p in (phone, client.address) if p)
            if subtitle:
                option["attrs"]["data-subtitle"] = subtitle
        return option


class SaleForm(forms.ModelForm):
    """The sale receipt header. Every sale is a receivable. The deadline is
    entered as a number of days from the sale date (the model stores the
    resulting `debt_deadline`); blank falls back to DEFAULT_DEBT_DAYS."""

    debt_days = forms.IntegerField(
        label="Qarz muddati (kun)",
        required=False,
        min_value=0,
        help_text=f"Necha kundan keyin qaytariladi — bo'sh qolsa {DEFAULT_DEBT_DAYS} kun",
        widget=forms.NumberInput(
            attrs={"min": "0", "inputmode": "numeric", "data-debt-days": ""}
        ),
    )

    class Meta:
        model = Sale
        fields = ["date", "client"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "client": ClientSelect,
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.can_see_all_records:
            self.fields["client"].queryset = Client.objects.filter(owner=user)
        _searchable_select(self.fields["client"], "Mijozni qidiring yoki tanlang")
        # Pre-fill the days input: on edit, derive it from the stored deadline;
        # on create, seed with the default so the preview shows a date up front.
        if self.instance.pk and self.instance.debt_deadline and self.instance.date:
            self.fields["debt_days"].initial = max(
                (self.instance.debt_deadline - self.instance.date).days, 0
            )
        else:
            self.fields["debt_days"].initial = DEFAULT_DEBT_DAYS

    def clean(self):
        cleaned = super().clean()
        base_date = cleaned.get("date") or timezone.localdate()
        days = cleaned.get("debt_days")
        if days is None:
            days = DEFAULT_DEBT_DAYS
        self.instance.debt_deadline = base_date + timedelta(days=days)
        cleaned["debt_deadline"] = self.instance.debt_deadline
        return cleaned


class SaleItemForm(forms.ModelForm):
    class Meta:
        model = SaleItem
        fields = ["product", "size", "micron", "dimension", "weight", "price", "cost_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        _searchable_select(self.fields["product"], "Mahsulotni tanlang")
        # Razmer / mikron are optional and only shown for products that carry them
        # (the JS reads has_size/has_micron and hides the dropdown otherwise).
        self.fields["size"].required = False
        self.fields["micron"].required = False
        self.fields["size"].widget.attrs["data-variant"] = "size"
        self.fields["micron"].widget.attrs["data-variant"] = "micron"
        for key in ("size", "micron"):
            self.fields[key].choices = [("", "—")] + [
                c for c in self.fields[key].choices if c[0]
            ]
        self.fields["cost_price"].required = False
        self.fields["cost_price"].widget.attrs["placeholder"] = "Bo'sh qolsa — mahsulot tannarxi"
        _mark_money(self.fields["price"], self.fields["cost_price"])

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
        # A product that doesn't carry razmer/mikron never keeps one, even if a stale
        # value slipped through from a previously-picked product on the same row.
        if product and not product.has_size:
            cleaned["size"] = ""
        if product and not product.has_micron:
            cleaned["micron"] = ""
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
    """Record goods returned on a sale.

    The seller picks a sale LINE and a quantity — nothing else about the goods. Price,
    tannarx, product and unit all come from that line (see `Return.save`), so a return
    can never be worth more per unit than the sale it reverses. The quantity is capped
    at what that line still has outstanding, net of earlier returns against it."""

    SETTLE_ADVANCE = "advance"
    SETTLE_REFUND = "refund"

    settlement = forms.ChoiceField(
        label="Mijoz allaqachon to'lagan qismi",
        choices=[
            (SETTLE_ADVANCE, "Avans bo'lib qolsin (keyingi savdolarga ishlatiladi)"),
            (SETTLE_REFUND, "Naqd qaytarilsin (kassadan chiqadi)"),
        ],
        initial=SETTLE_ADVANCE,
        widget=forms.RadioSelect,
        # Optional on purpose: if nothing is picked the excess becomes advance credit,
        # which keeps the money in the till. Silently paying cash out would be the
        # riskier default.
        required=False,
        help_text=(
            "Bu chek to'langani uchun qaytarilgan tovar puli mijozga qaytariladi."
        ),
    )

    class Meta:
        model = Return
        fields = ["sale_item", "weight", "restock", "note"]
        widgets = {"note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy"})}

    @staticmethod
    def returnable_value(sale):
        """Worth of everything still returnable on this sale, at the sale's own prices.

        If that total can't exceed the open debt then no return, of any size, can leave
        money owed to the client — so the settlement choice is meaningless and gets
        dropped from the form entirely."""
        return sum(
            (
                (item.weight - sum((r.weight for r in item.returns.all()), Decimal("0")))
                * item.price
                for item in sale.items.all()
            ),
            Decimal("0"),
        )

    @classmethod
    def can_overpay(cls, sale):
        open_debt = max(Decimal("0"), sale.debt_remaining)
        return cls.returnable_value(sale) > open_debt

    def __init__(self, *args, sale=None, user=None, **kwargs):
        self.sale = sale
        self.user = user
        # Filled in by clean(): how the return's value splits between cancelling debt
        # and money owed back to the client. The view reads these to post the
        # settlement, so the split is worked out in exactly one place.
        self.credited_to_debt = Decimal("0")
        self.excess = Decimal("0")
        super().__init__(*args, **kwargs)
        # An unpaid receipt can only ever have its debt reduced — asking the seller how
        # to hand money back would be a question with no answer.
        if sale is not None and not self.can_overpay(sale):
            del self.fields["settlement"]
        field = self.fields["sale_item"]
        field.queryset = (
            SaleItem.objects.filter(sale=sale).select_related("product")
            if sale is not None
            else SaleItem.objects.none()
        )
        field.label_from_instance = self._line_label
        _searchable_select(field, "Sotuv qatorini tanlang")

    @staticmethod
    def _line_label(item):
        """Name the line by product and unit price, so two lines of the same product
        at different prices stay tellable apart in the dropdown."""
        return (
            f"{item.product.name} · {item.weight:g} {item.dimension} "
            f"× {item.price:,.0f} so'm"
        )

    @staticmethod
    def returnable(item):
        """How much of one sale line is still returnable, in the line's own unit."""
        already = sum((r.weight for r in item.returns.all()), Decimal("0"))
        return item.weight - already

    def clean_weight(self):
        weight = self.cleaned_data.get("weight")
        if weight is not None and weight <= 0:
            raise forms.ValidationError("Og'irlik 0 dan katta bo'lishi kerak.")
        return weight

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("sale_item")
        weight = cleaned.get("weight")
        if not (item and weight):
            return cleaned

        if not cleaned.get("settlement"):
            cleaned["settlement"] = self.SETTLE_ADVANCE

        left = self.returnable(item)
        if weight > left:
            raise forms.ValidationError(
                f"Bu qatordan ko'pi bilan {left:g} {item.dimension} qaytarish mumkin "
                f"(sotilgan: {item.weight:g}, avval qaytarilgan: {item.weight - left:g})."
            )

        # Split the return: it cancels open debt first, and only what's left over is
        # money the client had already paid and is owed back.
        value = weight * item.price
        open_debt = max(Decimal("0"), self.sale.debt_remaining) if self.sale else Decimal("0")
        self.credited_to_debt = min(value, open_debt)
        self.excess = value - self.credited_to_debt

        if (
            self.excess > 0
            and cleaned.get("settlement") == self.SETTLE_REFUND
            and self.user is not None
        ):
            on_hand = seller_cash_on_hand(self.user)
            if self.excess > on_hand:
                raise forms.ValidationError(
                    f"Naqd qaytarish uchun kassada pul yetarli emas: kerak "
                    f"{self.excess:,.0f} so'm, kassada {on_hand:,.0f} so'm. "
                    f"Avans variantini tanlang yoki avval kassaga pul kiriting."
                )
        return cleaned


class ClientTransferForm(forms.Form):
    """Reassign a client to another seller. The target list excludes the current
    owner, so transferring to who already owns them is not selectable."""

    new_owner = forms.ModelChoiceField(
        label="Yangi sotuvchi",
        queryset=User.objects.none(),
    )

    def __init__(self, *args, client=None, **kwargs):
        self.client = client
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(is_active=True)
        if client is not None:
            qs = qs.exclude(pk=client.owner_id)
        self.fields["new_owner"].queryset = qs.order_by(
            "first_name", "last_name", "username"
        )
        _searchable_select(self.fields["new_owner"], "Sotuvchini tanlang")
