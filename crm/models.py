from decimal import Decimal

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


class Client(models.Model):
    name = models.CharField("Ismi", max_length=200)
    company = models.CharField("Kompaniya", max_length=200, blank=True)
    email = models.EmailField("Email", blank=True)
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
        return self.annotate(
            stock_in=Coalesce(received, ZERO_QTY),
            stock_out=Coalesce(sold, ZERO_QTY),
        ).annotate(stock=F("stock_in") - F("stock_out"))


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
    def current_stock(self):
        return self.total_received - self.total_sold

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


def _sale_paid_sum():
    """A subquery summing the payments recorded against one sale."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(s=Sum("amount"))
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
        """with_totals plus paid / remaining, so debt status can be filtered in SQL."""
        return self.with_totals().annotate(
            paid=_sale_paid_sum(),
        ).annotate(remaining=F("total") - F("paid"))

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
        """Short label for list rows: first product name plus a "+N" for the rest."""
        items = list(self.items.all())
        if not items:
            return "—"
        first = items[0].product.name
        extra = len(items) - 1
        return f"{first}  +{extra}" if extra > 0 else first

    @property
    def paid_amount(self):
        return self.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    @property
    def debt_remaining(self):
        return self.total_price - self.paid_amount

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

    date = models.DateField("Sana", default=timezone.localdate)
    amount = models.DecimalField("Miqdor (so'm)", max_digits=18, decimal_places=2)
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Method.choices, default=Method.CASH
    )
    # Bank fee withheld on a transfer: the debt is credited the full amount, but
    # only (amount − commission) actually lands in the till.
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
        """What actually reaches the till after the bank fee."""
        return self.amount - (self.commission or Decimal("0"))

    def __str__(self):
        return f"{self.get_kind_display()}: {self.amount} so'm ({self.date})"
