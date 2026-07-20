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
RETURN_COST = ExpressionWrapper(F("weight") * F("cost_price"), output_field=MONEY)
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
    def with_stock(self, seller=None):
        """Annotate each product with stock_in, stock_out, and current stock (kg).

        With `seller`, the numbers are that seller's own ombor: goods received from
        production (`ProductionReceiptItem`) minus what they've sold, plus their
        restocked returns — and only movements dated on/after `OMBOR_START_DATE`
        count. Without a seller it's the legacy shared-warehouse view (StockEntry)."""
        if seller is not None:
            start = settings.OMBOR_START_DATE
            received = Subquery(
                ProductionReceiptItem.objects.filter(
                    product=OuterRef("pk"),
                    receipt__seller=seller,
                    receipt__date__gte=start,
                )
                .values("product")
                .annotate(s=Sum("quantity_kg"))
                .values("s"),
                output_field=QTY,
            )
            sold = Subquery(
                SaleItem.objects.filter(
                    product=OuterRef("pk"),
                    sale__sales_rep=seller,
                    sale__date__gte=start,
                )
                .values("product")
                .annotate(s=Sum(ITEM_WEIGHT_KG))
                .values("s"),
                output_field=QTY,
            )
            returned = Subquery(
                Return.objects.filter(
                    product=OuterRef("pk"),
                    sale__sales_rep=seller,
                    restock=True,
                    date__gte=start,
                )
                .values("product")
                .annotate(s=Sum(RETURN_WEIGHT_KG))
                .values("s"),
                output_field=QTY,
            )
        else:
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


# Payment kinds that represent the client paying money INTO a sale. The two
# return-settlement kinds also carry a `sale`, but they move money the other way,
# so they must never be counted here — see `_sale_settlement_sum`.
PAYING_KINDS = ("sale", "debt", "advance_used")


def _sale_paid_sum():
    """A subquery summing the net payments credited against one sale.

    Only the net (amount − commission) reduces the debt — on a bank transfer the
    client bears the fee, so a 100k transfer with a 5k fee clears only 95k."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(sale=OuterRef("pk"), kind__in=PAYING_KINDS)
            .values("sale")
            .annotate(s=Sum(PAYMENT_NET))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


def _sale_settlement_sum():
    """A subquery summing what has been given back to the client on one sale — the
    excess of a return over the debt it cancelled, settled either as advance credit
    (RETURN_CREDIT) or as cash handed over (REFUND_OUT).

    This is what stops an over-returned sale from sitting at a permanent negative
    balance: the goods leave via `returned`, the money the client had already paid
    comes back via `settled`, and the receipt lands back on zero."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(
                sale=OuterRef("pk"),
                kind__in=(Payment.Kind.RETURN_CREDIT, Payment.Kind.REFUND_OUT),
            )
            .values("sale")
            .annotate(s=Sum("amount"))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


def _sale_return_sum(expr, restocked_only=False):
    """A subquery summing a money-expression over the returns on one sale.

    `restocked_only` limits it to goods that physically came back into the warehouse —
    the distinction that decides whether a return also relieves the seller of the
    tannarx they owe production."""
    qs = Return.objects.filter(sale=OuterRef("pk"))
    if restocked_only:
        qs = qs.filter(restock=True)
    return Coalesce(
        Subquery(
            qs.values("sale").annotate(s=Sum(expr)).values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


class SaleQuerySet(models.QuerySet):
    def with_totals(self):
        """Annotate each sale (header) with revenue/cost/profit over its items.

        `total` and `cost_total` stay GROSS — they are what was sold, and reports read
        them that way. Returns are exposed alongside as `returned` /
        `returned_cost_total`, with `net_revenue` / `net_cost_total` giving the
        after-returns figures.

        Only restocked returns give their tannarx back: if the goods did not come back
        into the warehouse they were still consumed, so the cost stands and
        `profit_total` absorbs it as a loss.

        Annotation names deliberately differ from the same-meaning properties on Sale
        (`net_total`, `net_cost`, `returned_cost`) — Django assigns annotations onto the
        instance, and a name shared with a read-only property blows up on assignment."""
        return self.annotate(
            total=_sale_item_sum(REVENUE),
            cost_total=_sale_item_sum(COST),
            returned=_sale_return_sum(RETURN_AMOUNT),
            returned_cost_total=_sale_return_sum(RETURN_COST, restocked_only=True),
        ).annotate(
            net_revenue=F("total") - F("returned"),
            net_cost_total=F("cost_total") - F("returned_cost_total"),
        ).annotate(profit_total=F("net_revenue") - F("net_cost_total"))

    def with_balance(self):
        """with_totals plus paid / settled / remaining, so debt status can be filtered
        in SQL.

        Returned goods shrink what the client owes (`net_revenue`); money already handed
        back to them for those goods shrinks what they are credited with having paid
        (`net_paid`). A sale that is fully returned and fully settled lands on exactly
        zero either way round."""
        return self.with_totals().annotate(
            paid=_sale_paid_sum(),
            settled=_sale_settlement_sum(),
        ).annotate(
            net_paid=F("paid") - F("settled"),
        ).annotate(remaining=F("net_revenue") - F("net_paid"))

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
        """After-returns profit. A non-restocked return drops the revenue but keeps
        the cost, so writing goods off shows up here as a loss."""
        return self.net_total - self.net_cost

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
        # Net of bank fees: only (amount − commission) counts toward the debt. Limited
        # to the kinds that move money IN — a settlement row also carries this sale.
        return (
            self.payments.filter(kind__in=PAYING_KINDS).aggregate(s=Sum(PAYMENT_NET))["s"]
            or Decimal("0")
        )

    @property
    def settled_amount(self):
        """Money handed back to the client on this sale — the over-returned excess,
        parked as advance credit or paid out in cash."""
        return (
            self.payments.filter(
                kind__in=(Payment.Kind.RETURN_CREDIT, Payment.Kind.REFUND_OUT)
            ).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )

    @property
    def returned_amount(self):
        return sum((r.amount for r in self.returns.all()), Decimal("0"))

    @property
    def returned_cost(self):
        """Tannarx of returns that went back into the warehouse — the only ones that
        relieve the seller of what they owe production."""
        return sum(
            (r.cost_amount for r in self.returns.all() if r.restock), Decimal("0")
        )

    @property
    def net_total(self):
        """What the client owes before payments: sold value minus returns."""
        return self.total_price - self.returned_amount

    @property
    def net_cost(self):
        return self.total_cost - self.returned_cost

    @property
    def debt_remaining(self):
        return self.net_total - (self.paid_amount - self.settled_amount)

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
    # Order fulfilment. `fulfilled_kg` is how much of the line has been backed by
    # stock (partial fills allowed); `fulfilled_at` is set only once it's FULLY
    # filled. A line sold short (zakaz) starts at 0 and gets topped up as stock
    # arrives. Orthogonal to the ombor stock math — a pending line still counts as
    # sold.
    fulfilled_kg = models.DecimalField(
        "Bajarilgan miqdor (kg)", max_digits=12, decimal_places=3, default=0
    )
    fulfilled_at = models.DateField("To'liq bajarilgan sana", null=True, blank=True)
    fulfilled_by_receipt = models.ForeignKey(
        "ProductionReceipt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fulfilled_items",
        verbose_name="Qabul (biriktirilgan)",
    )

    class Meta:
        verbose_name = "Sotuv qatori"
        verbose_name_plural = "Sotuv qatorlari"

    @property
    def is_pending(self):
        """A zakaz line not yet fully backed by stock."""
        return self.fulfilled_at is None

    @property
    def pending_kg(self):
        """The still-unfilled quantity of this line, in kg."""
        return max(Decimal("0"), self.weight_kg - self.fulfilled_kg)

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
    value and, when restocked, flows the quantity back into the warehouse.

    A return always points at the exact sale line it reverses. Product, dimension,
    price and cost_price are copied from that line by `save()` and are never typed
    in by hand — a free-typed price would let a seller shrink a debt by more than
    the goods were sold for, and a line-level link keeps things unambiguous when the
    same product appears twice on one receipt at different prices."""

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="returns", verbose_name="Sotuv"
    )
    sale_item = models.ForeignKey(
        SaleItem, on_delete=models.CASCADE, related_name="returns", verbose_name="Sotuv qatori"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="returns", verbose_name="Mahsulot"
    )
    dimension = models.CharField(
        "O'lchov birligi", max_length=2, choices=Sale.Dimension.choices, default=Sale.Dimension.KG
    )
    weight = models.DecimalField("Og'irligi", max_digits=12, decimal_places=3)
    price = models.DecimalField("Narxi (1 birlik, so'm)", max_digits=14, decimal_places=2)
    # Tannarx of the returned goods, snapshotted from the sale line. Drives both the
    # profit adjustment and — only when restocked — the seller's production debt.
    cost_price = models.DecimalField(
        "Tannarxi (1 birlik, so'm)", max_digits=14, decimal_places=2, default=0
    )
    date = models.DateField("Sana", default=timezone.localdate)
    restock = models.BooleanField("Omborga qaytarilsin", default=True)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="returns",
        verbose_name="Kim qabul qildi",
    )
    # The settlement this return generated for its excess value — money the client had
    # already paid and is owed back: a REFUND_OUT cash payout or a RETURN_CREDIT advance.
    # Null when the return only cancelled open debt (no money moved). Kept as an explicit
    # link so undoing the return voids exactly the right till entry instead of guessing.
    settlement = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settled_return",
        verbose_name="Hisob-kitob to'lovi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Qaytarish"
        verbose_name_plural = "Qaytarishlar"

    def save(self, *args, **kwargs):
        """Mirror the sale line's identity and pricing onto the return, so the two can
        never drift apart. The seller only ever chooses a line and a quantity."""
        item = self.sale_item
        self.sale_id = item.sale_id
        self.product_id = item.product_id
        self.dimension = item.dimension
        self.price = item.price
        self.cost_price = item.cost_price
        super().save(*args, **kwargs)

    @property
    def weight_kg(self):
        if self.dimension == Sale.Dimension.G:
            return self.weight / Decimal("1000")
        return self.weight

    @property
    def amount(self):
        """Revenue reversed by this return — what the client is credited."""
        return self.weight * self.price

    @property
    def cost_amount(self):
        """Tannarx of the returned goods."""
        return self.weight * self.cost_price

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


class PaymentQuerySet(models.QuerySet):
    def till_income(self):
        """Only payments that represent real cash arriving in a till.

        Three kinds are excluded. ADVANCE_USED: that money already entered the till as
        an ADVANCE_IN deposit, so counting the consumption again would double it.
        RETURN_CREDIT: likewise already in the till from the original payment — the
        return only re-labels it as the client's credit. REFUND_OUT: money leaving,
        not arriving; it is subtracted separately in `seller_cash_on_hand`.

        Per-sale debt math is separate and DOES count ADVANCE_USED (it settles the
        receipt) — see `_sale_paid_sum`."""
        return self.exclude(
            kind__in=(
                self.model.Kind.ADVANCE_USED,
                self.model.Kind.RETURN_CREDIT,
                self.model.Kind.REFUND_OUT,
            )
        )


class Payment(models.Model):
    """A money movement (To'lov): either paid at the time of sale, or a debt repayment."""

    class Method(models.TextChoices):
        CASH = "cash", "Naqd"
        CARD = "card", "Karta"
        TRANSFER = "transfer", "Bank o'tkazmasi"

    class Kind(models.TextChoices):
        SALE = "sale", "Sotuvda to'langan"
        DEBT = "debt", "Qarz to'lovi"
        # A client's prepayment: cash taken in BEFORE (or beyond) any specific sale.
        # ADVANCE_IN is real money entering the till; ADVANCE_USED spends that held
        # credit on a sale WITHOUT adding new till income (see PaymentQuerySet).
        ADVANCE_IN = "advance_in", "Oldindan to'lov (avans)"
        ADVANCE_USED = "advance_used", "Avansdan yechildi"
        # Settlement of a return whose value exceeds the sale's open debt — the client
        # had already paid for those goods, so the money is owed back to them.
        # RETURN_CREDIT parks it as advance credit: no cash moves (it is already in the
        # till from the original payment), so it must stay out of till income while
        # still counting toward the client's advance balance. REFUND_OUT is the other
        # route — cash physically handed back, so it leaves the till.
        RETURN_CREDIT = "return_credit", "Qaytarishdan kredit"
        REFUND_OUT = "refund_out", "Qaytarish (naqd berildi)"

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
    kind = models.CharField("Turi", max_length=16, choices=Kind.choices)
    # A per-sale payment (sale/debt/advance_used) carries `sale`; a client-level
    # advance deposit (advance_in) carries only `client` and leaves `sale` null.
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="payments", verbose_name="Sotuv",
        null=True, blank=True,
    )
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="advance_payments",
        verbose_name="Mijoz", null=True, blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Kim qabul qildi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PaymentQuerySet.as_manager()

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


class ProfitPayout(models.Model):
    """Profit a seller hands up to the owner/boss (Foyda topshirish).

    Once a seller has remitted the tannarx of what they've sold to production
    (ProductionRemittance), the cash left in their till is the markup — their
    realized profit. Handing it to the boss empties the till: like a remittance it
    is a cash outflow, but unlike one it does NOT touch the production debt (that's
    already settled) and it is NOT a business expense (profit earned isn't reduced —
    this only distributes it). So'm only, mirroring ProductionRemittance."""

    date = models.DateField("Sana", default=timezone.localdate)
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="profit_payouts",
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
        related_name="recorded_profit_payouts",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Foyda topshiruvi"
        verbose_name_plural = "Foyda topshiruvlari"

    def __str__(self):
        return f"Foyda topshiruvi · {self.seller}: {self.amount:,.0f} so'm ({self.date})"


def seller_cash_on_hand(seller, exclude_remittance_pk=None, exclude_payout_pk=None):
    """Cash physically in a seller's till right now — the same figure the kassa page
    shows as "Kassadagi pul": net client payments they collected, minus cash refunded
    to clients, minus expenses they paid out, minus what they've handed to production,
    minus profit already handed to the boss. A new handover can't exceed this
    (otherwise the till would go negative). The `exclude_*_pk` args drop one existing
    row from the tally so editing it checks against the delta, not itself."""
    income = (
        Payment.objects.filter(created_by=seller).till_income().aggregate(s=Sum(PAYMENT_NET))["s"]
        or Decimal("0")
    )
    # Cash handed back on an over-returned sale. Refunds are recorded without a bank
    # fee, so the full amount is what leaves the drawer.
    refunded = (
        Payment.objects.filter(
            created_by=seller, kind=Payment.Kind.REFUND_OUT
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    expense = (
        Expense.objects.filter(created_by=seller).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    remitted_qs = ProductionRemittance.objects.filter(seller=seller)
    if exclude_remittance_pk:
        remitted_qs = remitted_qs.exclude(pk=exclude_remittance_pk)
    remitted = remitted_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    payout_qs = ProfitPayout.objects.filter(seller=seller)
    if exclude_payout_pk:
        payout_qs = payout_qs.exclude(pk=exclude_payout_pk)
    paid_profit = payout_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return income - refunded - expense - remitted - paid_profit


def client_advance_balance(client, seller=None):
    """The credit a client holds: money put in (ADVANCE_IN deposits and RETURN_CREDIT
    from over-returned sales) minus what sales have since consumed (ADVANCE_USED).
    Positive = money held that the client hasn't taken goods for yet; zero = nothing
    prepaid. Nets bank fees out, so only usable money counts. Advance is seller-bound,
    so pass `seller` to get the balance in that one seller's till; omit it for the
    client's total across all sellers (the admin overview figure)."""
    rows = Payment.objects.filter(client=client)
    if seller is not None:
        rows = rows.filter(created_by=seller)
    deposited = (
        rows.filter(
            kind__in=(Payment.Kind.ADVANCE_IN, Payment.Kind.RETURN_CREDIT)
        ).aggregate(s=Sum(PAYMENT_NET))["s"]
        or Decimal("0")
    )
    used = (
        rows.filter(kind=Payment.Kind.ADVANCE_USED).aggregate(s=Sum(PAYMENT_NET))["s"]
        or Decimal("0")
    )
    return deposited - used


def seller_production_debt(seller):
    """What a seller still owes production: the tannarx (cost) of everything they've
    sold, minus the tannarx of goods that came back into the warehouse, minus what
    they've already remitted.

    Only RESTOCKED returns count against the debt. If the goods did not come back
    (spoiled, written off) the seller consumed them, so they still owe production the
    cost — otherwise a write-off would silently erase a real liability."""
    sold_cost = (
        SaleItem.objects.filter(sale__sales_rep=seller).aggregate(s=Sum(COST))["s"]
        or Decimal("0")
    )
    returned_cost = (
        Return.objects.filter(sale__sales_rep=seller, restock=True)
        .aggregate(s=Sum(RETURN_COST))["s"]
        or Decimal("0")
    )
    sold_cost -= returned_cost
    remitted = (
        ProductionRemittance.objects.filter(seller=seller).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    return sold_cost - remitted


def seller_withdrawable_profit(seller, exclude_payout_pk=None):
    """The profit sitting in a seller's till that may be handed to the boss: cash on
    hand minus what's still owed to production. Handing this over drops the till toward
    zero without disturbing the production debt. A profit payout can't exceed it."""
    return seller_cash_on_hand(
        seller, exclude_payout_pk=exclude_payout_pk
    ) - seller_production_debt(seller)


class ProductionReceipt(models.Model):
    """Goods a seller receives from production into their own ombor (warehouse).

    The mirror of `ProductionRemittance` (the cash a seller hands back): this is
    the goods handed forward, production → seller. Every seller keeps their own
    stock; on-hand per product = received − sold + restocked returns (see
    `ProductQuerySet.with_stock(seller=…)`). Purely an inventory record — it does
    NOT touch the kassa / production-debt figures."""

    date = models.DateField("Sana", default=timezone.localdate)
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="production_receipts",
        verbose_name="Sotuvchi",
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_receipts",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ishlab chiqarishdan qabul"
        verbose_name_plural = "Ishlab chiqarishdan qabullar"

    @property
    def total_kg(self):
        return sum((it.quantity_kg for it in self.items.all()), Decimal("0"))

    def __str__(self):
        return f"Qabul · {self.seller} ({self.date})"


class ProductionReceiptItem(models.Model):
    """One product line on a production receipt, in kg. May be negative for an
    admin write-off / correction (a line can subtract from the seller's ombor)."""

    receipt = models.ForeignKey(
        ProductionReceipt, on_delete=models.CASCADE, related_name="items",
        verbose_name="Qabul",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="receipt_items",
        verbose_name="Mahsulot",
    )
    quantity_kg = models.DecimalField("Miqdori (kg)", max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Qabul qatori"
        verbose_name_plural = "Qabul qatorlari"

    def __str__(self):
        return f"{self.product.name}: {self.quantity_kg} kg"


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
        if t == "Foyda":
            if a == self.Action.DELETE:
                return e("Foyda topshiruvi o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Foyda topshiruvi o'zgartirildi", AMBER, "edit")
            return e("Foyda boshliqqa topshirildi", "badge-info", "out", "out")
        if t == "Qaytarish":
            if a == self.Action.VOID:
                return e("Qaytarish bekor qilindi", RED, "trash")
            return e("Mahsulot qaytdi", AMBER, "return")
        if t == "Qabul":
            if a == self.Action.DELETE:
                return e("Qabul o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Qabul o'zgartirildi", AMBER, "edit")
            return e("Ombordan qabul qilindi", GREEN, "in")
        if t == "Zakaz":
            return e("Zakaz biriktirildi", "badge-info", "in")
        if a == self.Action.TRANSFER:
            return e("Sotuvchi o'zgardi", GREY, "transfer")
        return e(self.get_action_display(), GREY, "dot")

    def __str__(self):
        return f"{self.get_action_display()} · {self.target_type} · {self.summary}"
