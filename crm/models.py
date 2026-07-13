from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

MONEY = DecimalField(max_digits=18, decimal_places=2)
QTY = DecimalField(max_digits=18, decimal_places=3)
ZERO_QTY = Value(Decimal("0"), output_field=QTY)

# Reusable money aggregates for SaleItem querysets
REVENUE = ExpressionWrapper(F("weight") * F("price"), output_field=MONEY)
COST = ExpressionWrapper(F("weight") * F("cost_price"), output_field=MONEY)
PROFIT = ExpressionWrapper(F("weight") * (F("price") - F("cost_price")), output_field=MONEY)

# A sale item's weight expressed in kilograms (gram sales are divided by 1000)
ITEM_WEIGHT_KG = Case(
    When(dimension="g", then=F("weight") / Value(Decimal("1000"))),
    default=F("weight"),
    output_field=QTY,
)

# Same conversions, reused for returned goods
RETURN_AMOUNT = ExpressionWrapper(F("weight") * F("price"), output_field=MONEY)
RETURN_WEIGHT_KG = Case(
    When(dimension="g", then=F("weight") / Value(Decimal("1000"))),
    default=F("weight"),
    output_field=QTY,
)


class Client(models.Model):
    name = models.CharField("Ismi", max_length=200)
    company = models.CharField("Kompaniya", max_length=200, blank=True)
    phone = models.CharField("Telefon", max_length=30, blank=True)
    address = models.CharField("Manzil", max_length=300, blank=True)
    notes = models.TextField("Izoh", blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="clients",
        verbose_name="Mas'ul sotuvchi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Mijoz"
        verbose_name_plural = "Mijozlar"

    @classmethod
    def find_duplicate(cls, user, name, exclude_pk=None):
        """An existing client with the same name (case-insensitive), within the
        user's visible scope. Sales users only clash with their own clients;
        admins/managers clash with anyone's. Returns the match or None."""
        qs = cls.objects.filter(name__iexact=(name or "").strip())
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        if user is not None and not user.can_see_all_records:
            qs = qs.filter(owner=user)
        return qs.first()

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def with_stock(self):
        """Annotate each product with stock_in, stock_out, and current stock (kg)."""
        received = Subquery(
            StockEntry.objects.filter(product=OuterRef("pk"))
            .values("product")
            .annotate(s=Sum("quantity_kg"))
            .values("s"),
            output_field=QTY,
        )
        sold = Subquery(
            SaleItem.objects.filter(product=OuterRef("pk"))
            .values("product")
            .annotate(s=Sum(ITEM_WEIGHT_KG))
            .values("s"),
            output_field=QTY,
        )
        returned = Subquery(
            Return.objects.filter(product=OuterRef("pk"), restock=True)
            .values("product")
            .annotate(s=Sum(RETURN_WEIGHT_KG))
            .values("s"),
            output_field=QTY,
        )
        return self.annotate(
            stock_in=Coalesce(received, ZERO_QTY),
            stock_out=Coalesce(sold, ZERO_QTY),
            stock_returned=Coalesce(returned, ZERO_QTY),
        ).annotate(stock=F("stock_in") - F("stock_out") + F("stock_returned"))


class Product(models.Model):
    name = models.CharField("Nomi", max_length=200)
    sku = models.CharField("Artikul (SKU)", max_length=50, unique=True)
    description = models.TextField("Tavsif", blank=True)
    cost_price = models.DecimalField(
        "Tannarx (1 kg, so'm)", max_digits=14, decimal_places=2, default=0
    )
    price = models.DecimalField("Sotish narxi (1 kg, so'm)", max_digits=14, decimal_places=2)
    low_stock_threshold = models.DecimalField(
        "Kam qoldi chegarasi (kg)", max_digits=12, decimal_places=3, default=0
    )
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Mahsulot"
        verbose_name_plural = "Mahsulotlar"

    def cost_price_for(self, dimension):
        """Cost price per one unit of the given dimension (product prices are per kg)."""
        if dimension == Sale.Dimension.G:
            return self.cost_price / Decimal(1000)
        return self.cost_price

    @property
    def total_received(self):
        return self.stock_entries.aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")

    @property
    def total_sold(self):
        return self.sale_items.aggregate(s=Sum(ITEM_WEIGHT_KG))["s"] or Decimal("0")

    @property
    def total_returned(self):
        """Restocked returns (in kg) that flow back into the warehouse."""
        return (
            self.returns.filter(restock=True).aggregate(s=Sum(RETURN_WEIGHT_KG))["s"]
            or Decimal("0")
        )

    @property
    def current_stock(self):
        return self.total_received - self.total_sold + self.total_returned

    @property
    def is_low_stock(self):
        return self.current_stock <= self.low_stock_threshold

    def __str__(self):
        return f"{self.name} ({self.sku})"


def _sale_item_sum(expr):
    """A subquery summing an item money-expression for one sale (avoids join fan-out)."""
    return Coalesce(
        Subquery(
            SaleItem.objects.filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(s=Sum(expr))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


# A payment's net contribution to the debt: the gross paid minus the bank fee.
# Cash/card carry no commission, so net == amount there.
PAYMENT_NET = ExpressionWrapper(F("amount") - F("commission"), output_field=MONEY)


def _sale_paid_sum():
    """A subquery summing the net payments credited against one sale.

    Only the net (amount − commission) reduces the debt — on a bank transfer the
    client bears the fee, so a 100k transfer with a 5k fee clears only 95k."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(s=Sum(PAYMENT_NET))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


def _sale_return_sum():
    """A subquery summing the value of goods returned on one sale."""
    return Coalesce(
        Subquery(
            Return.objects.filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(s=Sum(RETURN_AMOUNT))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


class SaleQuerySet(models.QuerySet):
    def with_totals(self):
        """Annotate each sale (header) with revenue/cost/profit summed over its items."""
        return self.annotate(
            total=_sale_item_sum(REVENUE),
            cost_total=_sale_item_sum(COST),
            profit_total=_sale_item_sum(PROFIT),
        )

    def with_balance(self):
        """with_totals plus paid / returned / remaining, so debt status can be
        filtered in SQL. Returned goods reduce what the client owes."""
        return self.with_totals().annotate(
            paid=_sale_paid_sum(),
            returned=_sale_return_sum(),
        ).annotate(remaining=F("total") - F("returned") - F("paid"))

    def outstanding(self):
        """Sales that still owe money (a receivable / qarz)."""
        return self.with_balance().filter(remaining__gt=0)

    def visible_to(self, user):
        return self if user.can_see_all_records else self.filter(sales_rep=user)


class Sale(models.Model):
    """A sale receipt (chek): one client, one date/deadline, one or more line items."""

    class Dimension(models.TextChoices):
        KG = "kg", "kg"
        G = "g", "g"

    date = models.DateField("Sana", default=timezone.localdate)
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="sales", verbose_name="Mijoz"
    )
    sales_rep = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name="Sotuvchi",
    )
    # Every sale is a receivable with a deadline; "paid" is derived from payments.
    debt_deadline = models.DateField("To'lov muddati", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SaleQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Sotuv"
        verbose_name_plural = "Sotuvlar"

    @property
    def total_price(self):
        return sum((item.total_price for item in self.items.all()), Decimal("0"))

    @property
    def total_cost(self):
        return sum((item.total_cost for item in self.items.all()), Decimal("0"))

    @property
    def profit(self):
        return self.total_price - self.total_cost

    @property
    def item_summary(self):
        """Short label for list rows: first product (name · SKU) plus a "+N" for
        the rest."""
        items = list(self.items.all())
        if not items:
            return "—"
        product = items[0].product
        first = f"{product.name} · {product.sku}"
        extra = len(items) - 1
        return f"{first}  +{extra}" if extra > 0 else first

    @property
    def paid_amount(self):
        # Net of bank fees: only (amount − commission) counts toward the debt.
        return self.payments.aggregate(s=Sum(PAYMENT_NET))["s"] or Decimal("0")

    @property
    def returned_amount(self):
        return sum((r.amount for r in self.returns.all()), Decimal("0"))

    @property
    def net_total(self):
        """What the client owes before payments: sold value minus returns."""
        return self.total_price - self.returned_amount

    @property
    def debt_remaining(self):
        return self.net_total - self.paid_amount

    @property
    def is_paid(self):
        return self.debt_remaining <= 0

    @property
    def is_outstanding(self):
        """Still owes money — a live debt/receivable."""
        return self.debt_remaining > 0

    @property
    def is_overdue(self):
        return (
            self.is_outstanding
            and self.debt_deadline is not None
            and self.debt_deadline < timezone.localdate()
        )

    def __str__(self):
        return f"{self.date} · {self.client}"


class SaleItem(models.Model):
    """One product line on a sale receipt."""

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="items", verbose_name="Sotuv"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="sale_items", verbose_name="Mahsulot"
    )
    dimension = models.CharField(
        "O'lchov birligi", max_length=2, choices=Sale.Dimension.choices, default=Sale.Dimension.KG
    )
    weight = models.DecimalField("Og'irligi", max_digits=12, decimal_places=3)
    price = models.DecimalField("Narxi (1 birlik, so'm)", max_digits=14, decimal_places=2)
    cost_price = models.DecimalField(
        "Tannarxi (1 birlik, so'm)", max_digits=14, decimal_places=2
    )

    class Meta:
        verbose_name = "Sotuv qatori"
        verbose_name_plural = "Sotuv qatorlari"

    @property
    def weight_kg(self):
        if self.dimension == Sale.Dimension.G:
            return self.weight / Decimal("1000")
        return self.weight

    @property
    def total_price(self):
        return self.weight * self.price

    @property
    def total_cost(self):
        return self.weight * self.cost_price

    @property
    def profit(self):
        return self.total_price - self.total_cost

    def __str__(self):
        return f"{self.product.name}: {self.weight} {self.dimension}"


class Return(models.Model):
    """Goods returned from a sale. Credits the client's debt by the returned
    value and, when restocked, flows the quantity back into the warehouse."""

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="returns", verbose_name="Sotuv"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="returns", verbose_name="Mahsulot"
    )
    dimension = models.CharField(
        "O'lchov birligi", max_length=2, choices=Sale.Dimension.choices, default=Sale.Dimension.KG
    )
    weight = models.DecimalField("Og'irligi", max_digits=12, decimal_places=3)
    price = models.DecimalField("Narxi (1 birlik, so'm)", max_digits=14, decimal_places=2)
    date = models.DateField("Sana", default=timezone.localdate)
    restock = models.BooleanField("Omborga qaytarilsin", default=True)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="returns",
        verbose_name="Kim qabul qildi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Qaytarish"
        verbose_name_plural = "Qaytarishlar"

    @property
    def weight_kg(self):
        if self.dimension == Sale.Dimension.G:
            return self.weight / Decimal("1000")
        return self.weight

    @property
    def amount(self):
        return self.weight * self.price

    def __str__(self):
        return f"Qaytarish · {self.product.name}: {self.weight} {self.dimension}"


class StockEntry(models.Model):
    """A warehouse stock movement (kirim / adjustment), in kg. Positive adds stock,
    negative removes it (write-off or correction)."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stock_entries", verbose_name="Mahsulot"
    )
    date = models.DateField("Sana", default=timezone.localdate)
    quantity_kg = models.DecimalField("Miqdori (kg)", max_digits=12, decimal_places=3)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_entries",
        verbose_name="Kim qo'shdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ombor harakati"
        verbose_name_plural = "Ombor harakatlari"

    def __str__(self):
        sign = "+" if self.quantity_kg >= 0 else ""
        return f"{self.product.name}: {sign}{self.quantity_kg} kg ({self.date})"


class Payment(models.Model):
    """A money movement (To'lov): either paid at the time of sale, or a debt repayment."""

    class Method(models.TextChoices):
        CASH = "cash", "Naqd"
        CARD = "card", "Karta"
        TRANSFER = "transfer", "Bank o'tkazmasi"

    class Kind(models.TextChoices):
        SALE = "sale", "Sotuvda to'langan"
        DEBT = "debt", "Qarz to'lovi"

    class Currency(models.TextChoices):
        UZS = "uzs", "So'm"
        USD = "usd", "Dollar"

    date = models.DateField("Sana", default=timezone.localdate)
    # `amount` is always the so'm value — the canonical figure every debt, till and
    # report total is built on. A dollar payment is converted here at entry time.
    amount = models.DecimalField("Miqdor (so'm)", max_digits=18, decimal_places=2)
    currency = models.CharField(
        "Valyuta", max_length=3, choices=Currency.choices, default=Currency.UZS
    )
    # So'm per 1 USD, typed in by hand on each dollar payment; 0 for so'm payments.
    exchange_rate = models.DecimalField(
        "Dollar kursi (1$ = so'm)", max_digits=12, decimal_places=2, default=0
    )
    # The physical amount handed over, in its own currency (dollars for a USD
    # payment). `amount` is its so'm value; this is what the dollar till counts.
    amount_original = models.DecimalField(
        "Asl summa (valyutada)", max_digits=18, decimal_places=2, default=0
    )
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Method.choices, default=Method.CASH
    )
    # Bank fee withheld on a transfer. Only the net (amount − commission) both
    # lands in the till AND reduces the client's debt — the client bears the fee.
    commission = models.DecimalField(
        "Bank komissiyasi (so'm)", max_digits=18, decimal_places=2, default=0
    )
    # Percentage the bank withholds on a transfer; `commission` is derived from it.
    commission_percent = models.DecimalField(
        "Bank ushlagan foiz (%)", max_digits=5, decimal_places=2, default=0
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    kind = models.CharField("Turi", max_length=4, choices=Kind.choices)
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="payments", verbose_name="Sotuv"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Kim qabul qildi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "To'lov"
        verbose_name_plural = "To'lovlar"

    @property
    def net_amount(self):
        """What actually reaches the till after the bank fee — and, since the
        client bears the fee, also the amount credited against their debt."""
        return self.amount - (self.commission or Decimal("0"))

    @property
    def original_amount(self):
        """The amount in the currency the client actually handed over — the dollars
        for a USD payment, otherwise the so'm figure. Stored on entry; older rows
        (recorded before this field existed) fall back to the so'm `amount`."""
        return self.amount_original or self.amount

    def __str__(self):
        return f"{self.get_kind_display()}: {self.amount} so'm ({self.date})"


class Expense(models.Model):
    """A cash-register outflow (Chiqim): money paid out of the till — fuel,
    salaries, meals, purchases, and the like. Reduces the kassa balance. Unlike a
    bank commission (which the client bears), an expense is the business's own cost,
    tagged with the wallet it left (naqd/karta/bank) so each method's balance is right."""

    class Category(models.TextChoices):
        FUEL = "fuel", "Benzin / transport"
        SALARY = "salary", "Oylik / xodim"
        RENT = "rent", "Ijara"
        MEAL = "meal", "Ovqat (obed)"
        PURCHASE = "purchase", "Mahsulot xaridi"
        OTHER = "other", "Boshqa"

    date = models.DateField("Sana", default=timezone.localdate)
    # `amount` is always the so'm value — the base every kassa and profit figure
    # uses. A dollar expense is converted here; `amount_original` keeps the dollars.
    amount = models.DecimalField("Summa (so'm)", max_digits=18, decimal_places=2)
    currency = models.CharField(
        "Valyuta", max_length=3, choices=Payment.Currency.choices,
        default=Payment.Currency.UZS,
    )
    exchange_rate = models.DecimalField(
        "Dollar kursi (1$ = so'm)", max_digits=12, decimal_places=2, default=0
    )
    amount_original = models.DecimalField(
        "Asl summa (valyutada)", max_digits=18, decimal_places=2, default=0
    )
    category = models.CharField(
        "Turkum", max_length=10, choices=Category.choices, default=Category.OTHER
    )
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Payment.Method.choices,
        default=Payment.Method.CASH,
    )
    # A salary/advance expense is tagged with the factory worker it was paid to.
    # This is the SINGLE source for HR advances — the payroll calc reads these rows,
    # so a handout is never counted twice (once as an expense, once as an advance).
    # Only meaningful when category == SALARY; blank for every other expense.
    employee = models.ForeignKey(
        "Employee",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="salary_expenses",
        verbose_name="Xodim (oylik/avans)",
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="expenses",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Chiqim"
        verbose_name_plural = "Chiqimlar"

    @property
    def original_amount(self):
        """The dollars for a USD expense, otherwise the so'm figure."""
        return self.amount_original or self.amount

    def __str__(self):
        return f"{self.get_category_display()}: {self.amount} so'm ({self.date})"


class ProductionRemittance(models.Model):
    """Money a seller hands back to production (Ishlab chiqarishga topshirish).

    The firm's flow: a seller takes goods from the shared warehouse and sells them
    on to clients at a markup; the *cost price* (tannarx) of what they've sold is
    the seller's debt to production. When the seller hands their collected cash to
    production, that debt shrinks and the cash leaves the seller's till. So a
    remittance is both a till outflow AND a repayment of the seller→production debt —
    it is NOT an ordinary expense (an expense is the business's own cost).

    Always so'm: the production debt is denominated in so'm (tannarx is stored in
    so'm), so a handover is recorded in so'm too."""

    date = models.DateField("Sana", default=timezone.localdate)
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="remittances",
        verbose_name="Sotuvchi",
    )
    amount = models.DecimalField("Summa (so'm)", max_digits=18, decimal_places=2)
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Payment.Method.choices,
        default=Payment.Method.CASH,
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_remittances",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ishlab chiqarishga topshiruv"
        verbose_name_plural = "Ishlab chiqarishga topshiruvlar"

    def __str__(self):
        return f"Topshiruv · {self.seller}: {self.amount:,.0f} so'm ({self.date})"


class AuditLog(models.Model):
    """An append-only trail of money-relevant actions: who did what, and when.
    Written explicitly from the views so the acting user is always known."""

    class Action(models.TextChoices):
        CREATE = "create", "Qo'shildi"
        UPDATE = "update", "O'zgartirildi"
        DELETE = "delete", "O'chirildi"
        VOID = "void", "Bekor qilindi"
        PAYMENT = "payment", "To'lov"
        RETURN = "return", "Qaytarish"
        TRANSFER = "transfer", "Sotuvchi o'zgartirildi"

    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
        verbose_name="Kim",
    )
    action = models.CharField("Amal", max_length=10, choices=Action.choices)
    target_type = models.CharField("Obyekt", max_length=40)
    target_id = models.IntegerField("ID", null=True, blank=True)
    summary = models.CharField("Tafsilot", max_length=255)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit yozuvi"
        verbose_name_plural = "Audit jurnali"

    @classmethod
    def record(cls, user, action, target_type, target_id, summary):
        return cls.objects.create(
            user=user,
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
        )

    @property
    def event(self):
        """A domain-level view of the log line for the reports feed, derived from
        the action and what it acted on (e.g. a created sale reads "Sotuv bo'ldi").
        Returns a dict: label, cls (badge colour), icon (key), flow ('in'/'out'/'').
        `flow` drives the signed, coloured amount in the Summa column."""
        a, t = self.action, self.target_type
        GREEN, RED, AMBER, GREY = "badge-ok", "badge-danger", "badge-shipped", "badge-neutral"

        def e(label, cls, icon, flow=""):
            return {"label": label, "cls": cls, "icon": icon, "flow": flow}

        if t == "Sotuv":
            if a == self.Action.CREATE:
                return e("Sotuv bo'ldi", "badge-info", "sale", "sale")
            if a == self.Action.DELETE:
                return e("Sotuv o'chirildi", RED, "trash")
            return e("Sotuv o'zgartirildi", AMBER, "edit")
        if t == "Chiqim":
            if a == self.Action.DELETE:
                return e("Chiqim o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Chiqim o'zgartirildi", AMBER, "edit")
            return e("Chiqim bo'ldi", RED, "out", "out")
        if t == "To'lov":
            if a == self.Action.VOID:
                return e("To'lov bekor qilindi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("To'lov o'zgartirildi", AMBER, "edit")
            return e("Qarz to'landi", GREEN, "in", "in")
        if t == "Topshiruv":
            if a == self.Action.DELETE:
                return e("Topshiruv o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Topshiruv o'zgartirildi", AMBER, "edit")
            return e("Ishlab chiqarishga topshirildi", "badge-info", "out", "out")
        if t == "Qaytarish":
            return e("Mahsulot qaytdi", AMBER, "return")
        if a == self.Action.TRANSFER:
            return e("Sotuvchi o'zgardi", GREY, "transfer")
        return e(self.get_action_display(), GREY, "dot")

    def __str__(self):
        return f"{self.get_action_display()} · {self.target_type} · {self.summary}"


def workdays_mon_sat(start, end):
    """Number of Mon–Sat days in [start, end] inclusive. Sunday (weekday 6) is the
    firm's rest day, so it is the only day excluded from a fixed worker's norm."""
    if end < start:
        return 0
    n, day, one = 0, start, timedelta(days=1)
    while day <= end:
        if day.weekday() != 6:
            n += 1
        day += one
    return n


@dataclass
class Payroll:
    """One employee's computed pay for a period. Both salary types funnel into the
    same `soat × soat_narxi` shape (see TZ §5), so the template renders them uniformly."""

    employee: "Employee"
    total_hours: Decimal   # jami ishlagan soat (davr)
    norm_hours: Decimal    # dush–shanba soat (fiks maxraj bilan hisoblanadi)
    sunday_hours: Decimal  # yakshanba soat (fiks: qo'shimcha)
    workdays: int          # oy ish kunlari (fiks maxraj) — soatbayda 0
    hour_rate: Decimal     # amaldagi 1 soat narxi (so'm)
    computed: Decimal      # hisoblangan (so'm)
    advance: Decimal       # davr avanslari (so'm)
    remaining: Decimal     # qoldiq = hisoblangan − avans (so'm)


class Employee(models.Model):
    """A factory worker (salafan ishlab chiqarish). Not a system user — they don't
    log in — so this is a separate model from `accounts.User`. Pay is always
    hour-based (§1); the two types differ only in how the hourly rate is derived."""

    class SalaryType(models.TextChoices):
        HOURLY = "hourly", "Soatbay (smena)"
        FIXED = "fixed", "Fiks oylik"

    name = models.CharField("Ismi", max_length=200)
    salary_type = models.CharField(
        "Ish haqi turi", max_length=6, choices=SalaryType.choices, default=SalaryType.HOURLY
    )
    # Soatbay uchun: 1 soat narxi. Fiks ishchida ishlatilmaydi.
    hourly_rate = models.DecimalField(
        "Soat narxi (so'm)", max_digits=14, decimal_places=2, default=0
    )
    # Fiks uchun: oylik summa. Soatbayda ishlatilmaydi.
    monthly_salary = models.DecimalField(
        "Oylik summa (so'm)", max_digits=14, decimal_places=2, default=0
    )
    # Fiks soat narxi maxraji: oylik ÷ (oy ish kunlari × shu son). Kunduzi 8:00–19:00 = 11.
    standard_daily_hours = models.DecimalField(
        "Kunlik standart soat", max_digits=5, decimal_places=2, default=Decimal("11")
    )
    works_shifts = models.BooleanField("Smena bilan ishlaydi", default=True)
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Xodim"
        verbose_name_plural = "Xodimlar"

    @property
    def is_fixed(self):
        return self.salary_type == self.SalaryType.FIXED

    def payroll(self, date_from, date_to):
        """Compute this worker's pay for [date_from, date_to] (see TZ §5). Reads
        attendance hours and SALARY-category advances tagged to this employee."""
        q = Decimal("0.01")
        rows = list(self.attendances.filter(date__gte=date_from, date__lte=date_to))
        total_hours = sum((r.hours for r in rows), Decimal("0"))
        advance = self.salary_expenses.filter(
            category=Expense.Category.SALARY, date__gte=date_from, date__lte=date_to
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

        if self.is_fixed:
            std = self.standard_daily_hours or Decimal("0")
            # A fixed worker's hourly rate is a per-MONTH figure:
            # monthly_salary ÷ (that month's Mon–Sat days × standard_daily_hours).
            # Applying it day-by-day keeps ANY date range correct — a full month pays
            # exactly monthly_salary, a partial or cross-month range pays pro-rata.
            rate_cache = {}

            def _month_rate(day):
                key = (day.year, day.month)
                if key not in rate_cache:
                    first = day.replace(day=1)
                    nxt = date(day.year + 1, 1, 1) if day.month == 12 \
                        else date(day.year, day.month + 1, 1)
                    wd = workdays_mon_sat(first, nxt - timedelta(days=1))
                    denom = Decimal(wd) * std
                    rate_cache[key] = (self.monthly_salary / denom) if denom > 0 else Decimal("0")
                return rate_cache[key]

            norm_hours = sum((r.hours for r in rows if r.date.weekday() != 6), Decimal("0"))
            sunday_hours = sum((r.hours for r in rows if r.date.weekday() == 6), Decimal("0"))
            computed = sum((r.hours * _month_rate(r.date) for r in rows), Decimal("0"))
            workdays = workdays_mon_sat(date_from, date_to)
            # Representative rate to display: the effective so'm/soat over the range
            # (falls back to the start month's rate when no hours are logged).
            hour_rate = (computed / total_hours) if total_hours else _month_rate(date_from)
        else:
            workdays = 0
            hour_rate = self.hourly_rate
            norm_hours, sunday_hours = total_hours, Decimal("0")
            computed = total_hours * hour_rate

        computed = computed.quantize(q, rounding=ROUND_HALF_UP)
        return Payroll(
            employee=self,
            total_hours=total_hours,
            norm_hours=norm_hours,
            sunday_hours=sunday_hours,
            workdays=workdays,
            hour_rate=hour_rate.quantize(q, rounding=ROUND_HALF_UP),
            computed=computed,
            advance=advance,
            remaining=(computed - advance).quantize(q, rounding=ROUND_HALF_UP),
        )

    def __str__(self):
        return self.name


class Attendance(models.Model):
    """One worker's hours on one day. The single source for payroll — kelmagan kun
    is simply no row (or 0 hours), kech qolish is fewer hours (§1)."""

    class Shift(models.TextChoices):
        DAY = "day", "Kunduzi"
        NIGHT = "night", "Kechki"

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="attendances", verbose_name="Xodim"
    )
    date = models.DateField("Sana", default=timezone.localdate)
    hours = models.DecimalField("Ishlagan soat", max_digits=5, decimal_places=2)
    shift = models.CharField("Smena", max_length=5, choices=Shift.choices, blank=True)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"], name="uniq_attendance_employee_date"
            )
        ]
        verbose_name = "Davomad"
        verbose_name_plural = "Davomad"

    def __str__(self):
        return f"{self.employee} · {self.date}: {self.hours} soat"
