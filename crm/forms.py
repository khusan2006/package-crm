import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from accounts.models import User

from .utils import UZ_MONTH_NAMES

from .models import (
    Client,
    Employee,
    Expense,
    Payment,
    Product,
    ProductionAdjustment,
    ProductionReceipt,
    ProductionReceiptItem,
    ProductionRemittance,
    ProfitPayout,
    Return,
    Sale,
    SaleItem,
    StockEntry,
    seller_cash_on_hand,
    seller_remitted_total,
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


def _reject_future(value):
    """Money moves when it moves; it cannot move next month.

    A cash record dated ahead of today also splits the books in two: the kassa page
    counts only up to the day you are looking at, while the "is there enough in the
    till" guard counts every row there is. One future-dated handover is enough to make
    the page show money the form insists is already gone. Backdating stays allowed —
    old ledgers are entered with their real dates."""
    if value and value > timezone.localdate():
        raise forms.ValidationError("Sana kelajakda bo'lishi mumkin emas.")
    return value


def _som(value):
    """A money figure rounded to the whole so'm — the unit anything is ever paid in."""
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _not_enough(amount, available, subject, label):
    """The "there isn't that much in the till" error, or None when there is.

    Compared in WHOLE SO'M on purpose. The till carries kopeks — a 4% bank fee on a
    non-round payment leaves 28 239 066.99 behind — while every screen prints the
    figure rounded to 28 239 067. Comparing the raw Decimals then rejects the seller's
    own displayed balance with "you have 28 239 067, you cannot hand over
    28 239 067", which reads as nonsense and teaches them to type a smaller number
    until the form gives in. That habit is how phantom shortfalls get into the books.

    The message names the gap rather than leaving the seller to subtract two large
    numbers under pressure — if it is short, it says by how much."""
    if _som(amount) <= _som(available):
        return None
    return (
        f"{subject} {label}: {_som(available):,.0f} so'm. "
        f"Siz {_som(amount):,.0f} so'm kiritdingiz — "
        f"{_som(amount) - _som(available):,.0f} so'm yetmaydi."
    )


def _backdated_warning(seller, day, amount, **exclude):
    """Warning text when a payout is dated into a day that cannot carry it, else None.

    The till check is otherwise date-blind: it asks "does the seller hold this much
    today", so a payout backdated into an empty day passes on the strength of money
    collected since. The day itself goes negative and drags every later day with it,
    silently. A wage handed over on 17.08 but dated 31.07 put six days in the red
    exactly this way.

    A warning rather than a block: entering yesterday's work this morning is normal,
    and blocking would only teach the seller to shave the number down until the form
    relents — the habit that put phantom shortfalls in the books to begin with. So it
    names the day, the figure and the hole, and lets a second Saqlash through."""
    if seller is None or not amount or not day or day >= timezone.localdate():
        return None
    held = seller_cash_on_hand(seller, through=day, **exclude)
    after = _som(held) - _som(amount)
    if after >= 0:
        return None
    return (
        f"Diqqat: {day:%d.%m.%Y} kuni kassada {_som(held):,.0f} so'm bo'lgan. "
        f"{_som(amount):,.0f} so'm o'sha kunni {after:,.0f} so'mga tushiradi va "
        f"undan keyingi barcha kunlarni ham pastga suradi. Sana to'g'ri bo'lsa, "
        f"Saqlashni yana bosing."
    )


def _needs_second_press(form, warning):
    """Show `warning` once; the next submit carries the flag and goes through."""
    if not warning or form.cleaned_data.get("confirm_backdated"):
        return None
    form.data = form.data.copy()
    form.data["confirm_backdated"] = "1"
    return warning


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
    date = forms.DateField(
        label="To'lov sanasi",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Eski to'lovni kiritsangiz — o'sha kunni tanlang",
    )
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
    commission_payer = forms.ChoiceField(
        label="Komissiyani kim ko'taradi",
        choices=Payment.Payer.choices,
        initial=Payment.Payer.SELLER,
        required=False,
        widget=forms.RadioSelect,
        help_text=(
            "Sotuvchidan — mijoz qarzi yuborgan summasiga to'liq yopiladi. "
            "Mijozdan — qarzidan faqat komissiyasiz qismi yechiladi, foiz qarz bo'lib qoladi"
        ),
    )
    note = forms.CharField(
        label="Izoh",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ixtiyoriy — qo'shimcha ma'lumot"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

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
        # With no fee there is nobody to charge it to, so the choice collapses to the
        # default rather than being stored as a meaningless preference.
        payer = cleaned.get("commission_payer") or Payment.Payer.SELLER
        if not commission:
            payer = Payment.Payer.SELLER
        cleaned["commission_payer"] = payer
        if commission > amount:
            self.add_error(
                "commission_percent", "Komissiya to'lov summasidan ko'p bo'lishi mumkin emas."
            )
        # Overpayment is allowed on purpose: a client may hand over more than they
        # owe, and the surplus becomes their advance (kredit) — the views split it
        # off after the open receipts are settled. The full gross is what gets split:
        # a bank fee is withheld from the seller, so it never shrinks the client's
        # credit.
        cleaned["amount"] = amount  # canonical so'm value the views persist
        cleaned["amount_original"] = entered  # the physical figure (dollars for USD)
        # A backdated payment keeps the day the client actually handed the money
        # over (old sales are entered with their real dates, and so are their
        # payments); left empty it is simply today's.
        cleaned["date"] = cleaned.get("date") or timezone.localdate()
        cleaned["currency"] = currency
        cleaned["exchange_rate"] = rate
        cleaned["commission_percent"] = percent
        cleaned["commission"] = commission
        return cleaned


class AdvanceForm(DebtPaymentForm):
    """Taking an advance, or fixing one that was already taken.

    Everything a debt payment asks is asked here too; the one extra question is whether
    the cash is arriving in the drawer right now. Normally it is. But the old sverkas
    are full of money that was taken in months ago and only recorded as an advance
    today — book that as a kirim and the day's income swells by cash nobody handed
    over. Until now the only way round it was to write the deposit and then go and
    correct the till by hand, every single time.

    Saying no keeps the client's credit exactly the same and only holds the money out
    of the till (`Payment.is_opening`) — the same treatment the imported pre-CRM
    advances already get, for the same reason."""

    IN_KASSA = "yes"
    OUT_OF_KASSA = "no"

    to_kassa = forms.ChoiceField(
        label="Pul kassaga kirim bo'lsinmi?",
        choices=[
            (IN_KASSA, "Ha — pul hozir qo'lga tegdi"),
            (OUT_OF_KASSA, "Yo'q — pul avvalroq olingan, faqat avans yozilsin"),
        ],
        initial=IN_KASSA,
        widget=forms.RadioSelect,
        help_text=(
            "“Yo'q” tanlansa mijozning avansi xuddi shunday yoziladi, lekin kassa "
            "kirimiga qo'shilmaydi — eski hisob-kitobdagi pulni ikkinchi marta "
            "kirim qilmaslik uchun"
        ),
    )

    def clean(self):
        cleaned = super().clean()
        # The view stores a boolean, not the radio's string: `is_opening` is what the
        # till and every advance figure actually read.
        cleaned["is_opening"] = cleaned.get("to_kassa") == self.OUT_OF_KASSA
        return cleaned


class AdvanceEditForm(AdvanceForm):
    """Fixing a deposit that is already written — plus the one question the plain
    form has no reason to ask: what the till does about the difference.

    Changing an amount in place quietly rewrites the day the deposit was taken. For a
    figure typed wrong an hour ago that is exactly right. For a deposit from three
    weeks back it is the same damage a deletion would do: a day that was counted and
    signed off stops matching the notebook, and nobody looking at it later can tell
    why. The two cases cannot share one behaviour, so the form asks which one this is.

    "Farq bugungi kassaga" writes the difference as its own dated row — a deposit if
    the figure went up, an advance return if it went down — and leaves the original
    alone. The client's credit lands in the same place whichever route is picked; all
    that changes is which day the money is said to have moved.

    The question only appears when it has something to decide: with the amount left
    alone there is no difference to place, and the answer is ignored."""

    RETRO = "retro"
    TODAY = "today"
    NO_KASSA = "no_kassa"

    diff_mode = forms.ChoiceField(
        label="Summa farqi kassada qanday ko'rinsin?",
        choices=[
            (RETRO, "Eski kunning o'zi to'g'rilansin"),
            (TODAY, "Farq bugungi kassaga yozilsin — ko'paysa kirim, kamaysa chiqim"),
            (NO_KASSA, "Kassaga tegmasin — faqat mijoz avansi o'zgarsin"),
        ],
        initial=RETRO,
        required=False,
        widget=forms.RadioSelect,
        help_text=(
            "Bugun kiritilgan summani to'g'rilayotgan bo'lsangiz birinchisini "
            "tanlang. Eski, sverka qilingan kunga tegmaslik uchun — ikkinchisini: "
            "o'sha kun o'z holicha qoladi, farq esa bugungi sanada alohida qator "
            "bo'lib yoziladi"
        ),
    )

    def clean_diff_mode(self):
        return self.cleaned_data.get("diff_mode") or self.RETRO


class AdvanceRemoveForm(forms.Form):
    """Taking an advance deposit off a client — and the question that decides what the
    till does about it.

    A deposit gets removed for two completely different reasons and they must not share
    one button. Either it should never have been written (wrong client, wrong figure,
    entered twice), in which case the record simply goes and the till is left as if it
    had never happened; or the client asked for their money back and got it, in which
    case the deposit stands and the cash leaving is recorded on the day it left. Doing
    the second as a deletion would quietly rewrite an old day's kirim — the very thing
    that makes a reconciled day stop matching the notebook it was counted against.

    A cash return can only give back credit the client is still holding, so it is capped
    at their advance balance: anything already spent on a receipt is not theirs to take,
    and handing it over would drag settled sales back into debt.

    Money going back gets the same till question the money coming in got (`AdvanceForm`),
    and for the same reason: the client's credit going away and a note leaving the
    drawer are two separate facts. Settle an old sverka balance and the credit has to
    go while the till stays put — charge it anyway and the drawer ends the day short by
    money it never held."""

    ERASE = "erase"
    CASH_OUT = "cash_out"
    FROM_KASSA = "yes"
    OUTSIDE_KASSA = "no"

    mode = forms.ChoiceField(
        label="Nima bo'ldi?",
        choices=[
            (ERASE, "Xato yozilgan — butunlay o'chirilsin"),
            (CASH_OUT, "Pul mijozga qaytarildi — kassadan chiqim bo'lsin"),
        ],
        initial=ERASE,
        widget=forms.RadioSelect,
    )
    amount = forms.DecimalField(
        label="Qaytarilgan summa (so'm)",
        max_digits=18,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
        help_text="Faqat pul qaytarilganda — hammasi emas, bir qismi ham bo'lishi mumkin",
    )
    date = forms.DateField(
        label="Qaytarilgan sana",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Pul kassadan qaysi kuni chiqqan bo'lsa — o'sha kun",
        validators=[_reject_future],
    )
    from_kassa = forms.ChoiceField(
        label="Pul kassadan chiqdimi?",
        choices=[
            (FROM_KASSA, "Ha — pul kassadan berildi"),
            (OUTSIDE_KASSA, "Yo'q — kassaga tegmasin, faqat avansdan yechilsin"),
        ],
        initial=FROM_KASSA,
        required=False,
        widget=forms.RadioSelect,
        help_text=(
            "“Yo'q” tanlansa mijozning avansi kamayadi, lekin kassadan chiqim "
            "yozilmaydi — pul kassadan chiqmagan bo'lsa (eski hisob-kitob) shunday"
        ),
    )
    note = forms.CharField(
        label="Izoh",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ixtiyoriy — nima uchun"}),
    )

    def __init__(self, *args, deposit_amount=None, balance=None, **kwargs):
        # What this deposit credited the client with (the sum offered back by default)
        # and what they still hold across every deposit (the ceiling).
        self.deposit_amount = deposit_amount or Decimal("0")
        self.balance = balance or Decimal("0")
        super().__init__(*args, **kwargs)
        _mark_money(self.fields["amount"])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") != self.CASH_OUT:
            return cleaned
        amount = cleaned.get("amount")
        if amount is None:
            # Left blank means "give back what this deposit was" — the common case,
            # so it is filled in rather than refused.
            amount = min(self.deposit_amount, self.balance)
            if amount <= 0:
                raise forms.ValidationError(
                    "Bu mijozda qaytariladigan avans qolmagan — avans allaqachon "
                    "sotuvlarga ishlatilgan. Xato bo'lsa, “butunlay o'chirilsin”ni "
                    "tanlang."
                )
            cleaned["amount"] = amount
        if amount > self.balance:
            raise forms.ValidationError(
                f"Mijozda hozir {self.balance:,.0f} so'm avans bor — undan ko'pini "
                f"qaytarib bo'lmaydi. Qolgani sotuvlarga ishlatilgan."
            )
        cleaned["date"] = cleaned.get("date") or timezone.localdate()
        # Same flag, same meaning as on the way in: this money is not in the drawer.
        cleaned["is_opening"] = cleaned.get("from_kassa") == self.OUTSIDE_KASSA
        return cleaned


class PaymentEditForm(forms.ModelForm):
    """Fix a mistaken payment (Kirim). Full edit of a single receipt: amount,
    currency + rate, method, bank commission and note. `amount` persists as so'm
    (a dollar payment is entered in dollars and converted at the hand-typed rate) and
    is what pays down the debt in full, so it may not exceed `max_amount` — the sale's
    remaining plus whatever this payment already covers, which keeps the sale from
    becoming over-paid. The bank fee sits on top, charged to the seller.
    `kind`/`sale`/`created_by` are fixed; only the money figures are editable."""

    class Meta:
        model = Payment
        fields = ["date", "amount", "currency", "exchange_rate", "method",
                  "commission_percent", "commission_payer", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "commission_payer": forms.RadioSelect,
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
        self.fields["commission_percent"].help_text = (
            "Faqat bank o'tkazmasi uchun — bank ushlab qoladigan foiz"
        )
        self.fields["commission_payer"].required = False
        self.fields["commission_payer"].help_text = (
            "Sotuvchidan — mijoz qarzi yuborgan summasiga to'liq yopiladi. "
            "Mijozdan — qarzidan faqat komissiyasiz qismi yechiladi, foiz qarz bo'lib qoladi"
        )
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])
        # Editing a dollar payment: show the original dollars in the amount field
        # (not the stored so'm), so re-saving converts at the rate correctly.
        if self.instance.pk and self.instance.currency == Payment.Currency.USD:
            self.initial["amount"] = self.instance.amount_original

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

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
        payer = cleaned.get("commission_payer") or Payment.Payer.SELLER
        if not commission:
            payer = Payment.Payer.SELLER
        cleaned["commission_payer"] = payer
        # What may not exceed the room left on the sale is what this payment CREDITS —
        # the gross when the seller carries the fee, the net when the client does.
        credit = amount - commission if payer == Payment.Payer.CLIENT else amount
        if self.max_amount is not None and credit > self.max_amount:
            self.add_error(
                "amount", f"Qoldiqdan ({self.max_amount:.0f} so'm) ko'p bo'lishi mumkin emas."
            )
        # `amount`/`exchange_rate`/`commission_percent`/`commission_payer` are form
        # fields (persisted via cleaned); `commission`/`amount_original` are not, so
        # set them on the instance directly.
        self.instance.commission = commission
        self.instance.amount_original = entered
        cleaned["amount"] = amount
        cleaned["exchange_rate"] = rate
        cleaned["commission_percent"] = percent
        return cleaned


def _month_choices(back=24, forward=1):
    """(YYYY-MM, "Avgust 2026") pairs around today, newest first. Spelled out in Uzbek
    rather than left to <input type="month">, which paints its own label in the
    browser's locale — the one place the UI stopped being Uzbek."""
    today = timezone.localdate()
    index = today.year * 12 + (today.month - 1)
    months = []
    for step in range(forward, -back, -1):
        year, month = divmod(index + step, 12)
        months.append((f"{year:04d}-{month + 1:02d}", f"{UZ_MONTH_NAMES[month]} {year}"))
    return months


def _parse_month(value):
    """"YYYY-MM" → the 1st of that month, or None if it is not a month."""
    try:
        year, month = (int(part) for part in str(value).split("-", 1))
        return date(year, month, 1)
    except (ValueError, TypeError):
        return None


class EmployeeForm(forms.ModelForm):
    """A payroll worker, their monthly wage, and where their account starts.

    Two fields exist only because the balance now carries from month to month. The
    opening pair (`start_month` + `opening_balance`) stops a worker hired years ago
    from arriving with years of invented arrears; `salary_from` dates a raise, so
    changing the wage today cannot re-price the months already settled."""

    start_month = forms.ChoiceField(
        label="Hisob boshlangan oy",
        help_text="Shu oydan boshlab oylik hisoblanadi — undan oldingi oylarga tegilmaydi",
    )
    salary_from = forms.ChoiceField(
        label="Yangi oylik qaysi oydan",
        required=False,
        help_text="Oylik o'zgartirilsa — shu oydan kuchga kiradi, oldingi oylar eski oylikda qoladi",
    )

    class Meta:
        model = Employee
        fields = ["name", "salary", "start_month", "opening_balance", "is_active", "note"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Masalan: Косимов Рахматжон"}),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — lavozimi, izoh"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        months = _month_choices()
        self.fields["start_month"].choices = months
        self.fields["salary_from"].choices = months
        this_month = timezone.localdate().replace(day=1)
        self.fields["start_month"].initial = (
            self.instance.start_month if self.instance.pk else this_month
        ).strftime("%Y-%m")
        # A raise defaults to "from this month": nobody edits a wage meaning to
        # backdate it, and the months behind it are already paid against.
        self.fields["salary_from"].initial = this_month.strftime("%Y-%m")
        self.fields["opening_balance"].help_text = (
            "Hisob boshlangan oyning boshiga xodimga qarzimiz. Aksincha, oldindan "
            "pul olib qo'ygan bo'lsa — minus bilan yozing (masalan −500000). "
            "Bilmasangiz 0 qoldiring."
        )
        if not self.instance.pk:
            # Nothing to re-price yet, so the question isn't asked on a new worker.
            del self.fields["salary_from"]
        _mark_money(self.fields["salary"], self.fields["opening_balance"])

    def clean_salary(self):
        salary = self.cleaned_data.get("salary")
        if salary is not None and salary <= 0:
            raise forms.ValidationError("Oylik 0 dan katta bo'lishi kerak.")
        return salary

    def clean_start_month(self):
        month = _parse_month(self.cleaned_data.get("start_month"))
        if month is None:
            raise forms.ValidationError("Oyni tanlang.")
        return month

    def clean_salary_from(self):
        return _parse_month(self.cleaned_data.get("salary_from"))

    def clean(self):
        cleaned = super().clean()
        # A raise cannot start before the account does — there is no month there to
        # apply it to, and the rate row would sort ahead of the opening figure.
        start, rate_from = cleaned.get("start_month"), cleaned.get("salary_from")
        if start and rate_from and rate_from < start:
            self.add_error(
                "salary_from",
                "Hisob boshlangan oydan oldin bo'lishi mumkin emas "
                f"({start.strftime('%m.%Y')}).",
            )
        return cleaned

    def clean_name(self):
        # Case-folded in Python rather than with `iexact`: the names here are Cyrillic
        # and SQLite's LIKE only case-folds ASCII, so an `iexact` guard would pass the
        # test suite and then behave differently on Postgres. The payroll is a handful
        # of rows, so comparing them in Python costs nothing.
        name = (self.cleaned_data.get("name") or "").strip()
        taken = Employee.objects.all()
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        folded = name.casefold()
        if any(existing.casefold() == folded for existing in taken.values_list("name", flat=True)):
            raise forms.ValidationError("Bu ismli xodim allaqachon bor.")
        return name


class ExpenseForm(forms.ModelForm):
    """A payout from the till. `method` records which wallet it left (cash/card/bank);
    `currency` records whether it left the so'm or the dollar till. A dollar expense is
    entered in dollars with a hand-typed rate and converted to a so'm `amount`.

    Tagging an `employee` names who the money went to or who spent it;
    `counts_against_salary` decides whether it also comes off that month's wage — on
    for a wage or an advance, off for petrol or lunch the worker bought for the
    business."""

    # Two spelled-out options rather than a lone tick box. This answer decides whether
    # money leaves somebody's wage, and an unticked box looks identical whether it was
    # answered or simply overlooked — the same reason the bank fee asks who carries it
    # with a pair of radios instead of a checkbox.
    counts_against_salary = forms.TypedChoiceField(
        label="Oylik hisobiga ta'siri",
        choices=[(True, "Oyligidan ushlansin"), (False, "Oyligidan ushlanmasin")],
        coerce=lambda value: value == "True",
        empty_value=None,
        required=False,  # hidden, and meaningless, while no worker is picked
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Expense
        fields = ["date", "amount", "currency", "exchange_rate", "category", "method",
                  "employee", "counts_against_salary", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.TextInput(attrs={"placeholder": "Ixtiyoriy — nima uchun"}),
            # Free text with a <datalist> of what's been used before: pick an
            # existing category or just type a new one.
            "category": forms.TextInput(attrs={
                "list": "expense-category-suggestions",
                "autocomplete": "off",
                "placeholder": "Masalan: Benzin / transport",
            }),
        }

    # Set once the seller has seen the backdating warning; the next submit passes.
    confirm_backdated = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Miqdor"
        self.fields["amount"].help_text = "Tanlangan valyutada — dollar tanlansa, dollardagi summa"
        self.fields["exchange_rate"].required = False
        self.fields["exchange_rate"].help_text = "Faqat dollar chiqimi uchun — qo'lda kiritiladi"
        # Only people still on the payroll are offered — but an expense already tied
        # to someone who has since left keeps showing them, or editing it would fail.
        staff = Employee.objects.filter(is_active=True)
        if self.instance.pk and self.instance.employee_id:
            staff = staff | Employee.objects.filter(pk=self.instance.employee_id)
        self.fields["employee"].queryset = staff.distinct()
        self.fields["employee"].required = False
        # A plain <select>, not the searchable combobox: this field is optional and
        # the combobox hides its blank row, which would leave no way to say "nobody"
        # (or to clear a mistagged expense).
        self.fields["employee"].empty_label = "— xodimga tegishli emas —"
        self.fields["employee"].help_text = "Kimga berildi yoki kim sarfladi"
        self.fields["counts_against_salary"].help_text = (
            "Oylik yoki avans bo'lsa — “ushlansin”. Benzin, obed kabi ish xarajatlarida "
            "— “ushlanmasin”: kassadan chiqim bo'ladi, xodim oyligiga tegmaydi."
        )
        _mark_money(self.fields["amount"], self.fields["exchange_rate"])
        # Editing a dollar expense: show the original dollars in the amount field
        # (not the stored so'm), so re-saving converts at the rate correctly.
        if self.instance.pk and self.instance.currency == Payment.Currency.USD:
            self.initial["amount"] = self.instance.amount_original

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

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
        # The answer only means anything next to a worker. Clearing it on an untagged
        # expense keeps a stale "yes" from coming back to life if the row is later
        # edited and someone is picked — the choice is hidden while nobody is.
        if not cleaned.get("employee"):
            cleaned["counts_against_salary"] = False
        elif cleaned.get("counts_against_salary") is None:
            # Someone is named but neither option was picked. Defaulting either way
            # would quietly decide whose money this is, so the form asks instead.
            self.add_error(
                "counts_against_salary",
                "Tanlang: bu pul xodim oyligidan ushlansinmi yoki yo'q.",
            )
        # A payout dated into a day the till could not carry — see _backdated_warning.
        seller = self.instance.created_by_id and self.instance.created_by or self.user
        warning = _needs_second_press(self, _backdated_warning(
            seller, cleaned.get("date"), som,
            exclude_expense_pk=self.instance.pk,
        ))
        if warning:
            raise forms.ValidationError(warning)
        return cleaned


class ProductionRemittanceForm(forms.ModelForm):
    """A seller handing collected cash back to production. So'm only — the debt it
    repays is a so'm figure. A seller records only their own handovers, so for a
    non-privileged user the `seller` field is fixed to themselves and hidden."""

    # Set once the seller has seen the backdating warning; the next submit passes.
    confirm_backdated = forms.BooleanField(required=False, widget=forms.HiddenInput)

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

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

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
            problem = _not_enough(
                amount, available, "Kassada yetarli pul yo'q.",
                f"{seller} qo'lida hozir",
            )
            if problem:
                raise forms.ValidationError(problem)
            # The total is enough, but the DAY it is dated into may not be.
            warning = _needs_second_press(self, _backdated_warning(
                seller, cleaned.get("date"), amount,
                exclude_remittance_pk=self.instance.pk,
            ))
            if warning:
                raise forms.ValidationError(warning)
        return cleaned


class ProductionRefundForm(ProductionRemittanceForm):
    """Production handing cash BACK to a seller (Ishlab chiqarishdan qaytarish) — the
    mirror of a handover: the seller's till grows and their production debt grows with
    it. The user types a plain positive figure; it is stored as a negative
    `ProductionRemittance.amount` so every handover total nets it off on its own.

    The only limit is symmetry: production cannot return more than it has received,
    so the amount is capped at the seller's net remitted so far."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = "Qaytarilgan summa (so'm)"
        # An existing return is stored negative; show it the way it was typed.
        if self.instance.pk and self.instance.amount < 0:
            self.initial["amount"] = -self.instance.amount

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Summa 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        # Deliberately skips ProductionRemittanceForm.clean: that one guards the till
        # against going negative, which a return can never do — it ADDS cash.
        cleaned = forms.ModelForm.clean(self)
        seller = cleaned.get("seller")
        if self.user is not None and not self.user.can_see_all_records:
            seller = self.user
        amount = cleaned.get("amount")
        if seller is not None and amount:
            remitted = seller_remitted_total(
                seller, exclude_remittance_pk=self.instance.pk
            )
            if amount > remitted:
                raise forms.ValidationError(
                    f"Qaytarish topshirilgandan ko'p bo'lishi mumkin emas. {seller} "
                    f"hozirgacha jami {remitted:,.0f} so'm topshirgan — "
                    f"{amount:,.0f} so'm qaytarib bo'lmaydi."
                )
            # Stored signed: a return is a handover running the other way.
            cleaned["amount"] = -amount
            self.instance.amount = -amount
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

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

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
            problem = _not_enough(
                amount, available, "Topshirish uchun yetarli foyda yo'q.",
                f"{seller} kassasida sof foyda",
            )
            if problem:
                raise forms.ValidationError(problem)
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
        # Razmer / mikron are optional free-text fields, only shown for products
        # that carry them (the JS reads has_size/has_micron and hides them
        # otherwise). They render as text inputs backed by a <datalist> so the
        # seller gets SIZE_CHOICES / MICRON_CHOICES as suggestions but can also
        # type any custom value.
        self.fields["size"].required = False
        self.fields["micron"].required = False
        self.fields["size"].widget.attrs.update(
            {"data-variant": "size", "list": "size-suggestions",
             "autocomplete": "off", "placeholder": "Razmer"}
        )
        self.fields["micron"].widget.attrs.update(
            {"data-variant": "micron", "list": "micron-suggestions",
             "autocomplete": "off", "placeholder": "Mikron"}
        )
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
            if _som(self.excess) > _som(on_hand):
                raise forms.ValidationError(
                    f"Naqd qaytarish uchun kassada pul yetarli emas: kerak "
                    f"{_som(self.excess):,.0f} so'm, kassada {_som(on_hand):,.0f} so'm — "
                    f"{_som(self.excess) - _som(on_hand):,.0f} so'm yetmaydi. "
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


class OpeningDebtForm(forms.Form):
    """Move a client's opening balance — the pre-CRM debt they carried in — up or down
    by a given sum.

    Until now these only ever arrived through the import commands, so a client whose old
    ledger was wrong (or who turned out to owe more than the sverka showed) could not be
    put right from the app at all.

    The sum entered is a DELTA, not the new total. A total field reads cleaner in
    isolation, but in practice the seller is told "add 1 138 300 to this client" and
    would have to add it to whatever is already on the card by hand — arithmetic done
    on a phone, against a figure they cannot see while typing. Here the current balance
    is shown, the amount is what they were told, and the direction is a choice.

    An opening balance carries no goods: it never touches revenue, profit or sold kg,
    only the receivable. That is also why it cannot be dropped below what has already
    been paid against it — the receipt would go negative with nobody owed the
    difference (`sale_edit` refuses the same thing for the same reason)."""

    ADD = "add"
    SUBTRACT = "subtract"

    operation = forms.ChoiceField(
        label="Amal",
        choices=[(ADD, "Qo'shish (+)"), (SUBTRACT, "Ayirish (−)")],
        initial=ADD,
        widget=forms.RadioSelect,
    )
    amount = forms.DecimalField(
        label="Summa (so'm)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Qo'shiladigan (yoki ayiriladigan) summa — umumiy qarz emas",
    )
    date = forms.DateField(
        label="Sana",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="Qarz qachondan hisoblanadi — kechikish shu sanadan o'lchanadi",
        validators=[_reject_future],
    )

    def __init__(self, *args, current=None, paid=None, **kwargs):
        # Where the balance stands now, and what has already been paid against it —
        # the floor the new figure cannot go under.
        self.current = current or Decimal("0")
        self.paid = paid or Decimal("0")
        # Filled in by clean(): the figure that will be stored. The view reads it so
        # the arithmetic lives in exactly one place.
        self.new_total = self.current
        super().__init__(*args, **kwargs)
        _mark_money(self.fields["amount"])

    def clean(self):
        cleaned = super().clean()
        amount, operation = cleaned.get("amount"), cleaned.get("operation")
        if amount is None or not operation:
            return cleaned
        delta = amount if operation == self.ADD else -amount
        new_total = self.current + delta
        if new_total < self.paid:
            room = self.current - self.paid
            raise forms.ValidationError(
                f"Bu qarzga allaqachon {self.paid:,.0f} so'm to'langan, shuning uchun "
                f"boshlang'ich qarzni {self.paid:,.0f} so'mdan kam qilib bo'lmaydi — "
                f"ko'pi bilan {room:,.0f} so'm ayirish mumkin."
            )
        self.new_total = new_total
        return cleaned


class ProductionAdjustForm(forms.ModelForm):
    """An admin moving a seller's production debt up or down without money changing
    hands.

    Two things are deliberate here. The amount is entered POSITIVE with a separate
    direction, because "add 5 000 000" is how the correction is spoken and a typed
    minus sign is easy to lose. And `reason` is mandatory: two of its four options are
    not really adjustments at all — a forgotten handover belongs in Topshirish, a
    missing sale in the sale form — and naming the case is what lets the form say so.

    Those two are warned about, not blocked. The right record cannot always be
    reconstructed years later, and an admin who knows that should still be able to
    close the gap; what matters is that the choice is on the record."""

    ADD = "add"
    SUBTRACT = "subtract"

    # Reasons whose real fix lives somewhere else — the form points there instead.
    STEERED = {
        ProductionAdjustment.Reason.REMITTANCE: (
            "Topshirilgan pulni bu yerda tuzatsangiz qarz to'g'rilanadi, lekin kassa "
            "o'sha summaga ortiqcha bo'lib qolaveradi. To'g'ri yo'li — «Topshirish» "
            "orqali o'sha to'lovni o'z sanasi bilan kiritish."
        ),
        ProductionAdjustment.Reason.SALE: (
            "Kiritilmagan sotuvni bu yerda tuzatsangiz qarz to'g'rilanadi, lekin "
            "sotuv, foyda va ombor hisoboti noto'g'ri qolaveradi. To'g'ri yo'li — "
            "o'sha sotuvni o'z sanasi bilan kiritish."
        ),
    }

    operation = forms.ChoiceField(
        label="Amal",
        choices=[(ADD, "Qarzga qo'shish (+)"), (SUBTRACT, "Qarzdan ayirish (−)")],
        initial=ADD,
        widget=forms.RadioSelect,
    )

    class Meta:
        model = ProductionAdjustment
        fields = ["date", "seller", "amount", "reason", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "reason": forms.RadioSelect,
            "note": forms.TextInput(attrs={"placeholder": "Nima bo'lganini yozing"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["amount"].label = "Summa (so'm)"
        self.fields["amount"].help_text = (
            "Qarzga qo'shiladigan yoki undan ayiriladigan summa"
        )
        _mark_money(self.fields["amount"])
        self.fields["reason"].empty_label = None
        self.fields["note"].help_text = "«Boshqa» tanlansa — majburiy"
        self.fields["seller"].queryset = User.objects.filter(
            is_active=True, role=User.Role.SALES
        ).order_by("first_name", "last_name", "username")
        _searchable_select(self.fields["seller"], "Sotuvchini tanlang")

    def clean_date(self):
        return _reject_future(self.cleaned_data.get("date"))

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Summa 0 dan katta bo'lishi kerak.")
        return amount

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("reason") == ProductionAdjustment.Reason.OTHER and not cleaned.get("note"):
            self.add_error("note", "«Boshqa» tanlanganda sababni yozish shart.")
        amount = cleaned.get("amount")
        if amount is not None and cleaned.get("operation") == self.SUBTRACT:
            cleaned["amount"] = -amount
            self.instance.amount = -amount
        return cleaned
