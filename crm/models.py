from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import (
    Case,
    Count,
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

# Product variants captured on a sale line. Razmer (roll width) and mikron
# (thickness) are picked at sale time, not baked into the product — the client sells
# 7 named products, and the film ones come in these widths/thicknesses while the
# ҚОП (bag) ones have neither. Both are optional on a line; the stored value IS the
# label the client uses. These are datalist *suggestions* only — the sale line's
# size/micron are free text, so a seller can type a value outside this list.
SIZE_CHOICES = [("1,5м", "1,5м"), ("2м", "2м"), ("6м", "6м")]
MICRON_CHOICES = [(m, m) for m in ("015", "01", "08", "06", "05", "04", "03", "02")]

# Reusable money aggregates for SaleItem querysets
REVENUE = ExpressionWrapper(F("weight") * F("price"), output_field=MONEY)
COST = ExpressionWrapper(F("weight") * F("cost_price"), output_field=MONEY)
PROFIT = ExpressionWrapper(F("weight") * (F("price") - F("cost_price")), output_field=MONEY)

# A gram figure in kilograms. Written as ×0.001 rather than ÷1000 on purpose:
# SQLite gives a stored 500.000 and a bare 1000 both INTEGER affinity, so the
# division truncates — 500 g came out as 0 kg instead of 0.5. A decimal factor
# can never be integer-affine, so the multiplication is right on every backend.
# (PostgreSQL, which production runs, divided correctly either way.)
GRAM_TO_KG = Value(Decimal("0.001"))

# A sale item's weight expressed in kilograms (gram sales are scaled down)
ITEM_WEIGHT_KG = Case(
    When(dimension="g", then=F("weight") * GRAM_TO_KG),
    default=F("weight"),
    output_field=QTY,
)

# Same conversions, reused for returned goods
RETURN_AMOUNT = ExpressionWrapper(F("weight") * F("price"), output_field=MONEY)
RETURN_COST = ExpressionWrapper(F("weight") * F("cost_price"), output_field=MONEY)
RETURN_WEIGHT_KG = Case(
    When(dimension="g", then=F("weight") * GRAM_TO_KG),
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
    # Whether a sale line for this product offers the Razmer / Mikron dropdowns.
    # The 5 film products have both; the ҚОП (bag) products have neither.
    has_size = models.BooleanField("Razmer bor", default=False)
    has_micron = models.BooleanField("Mikron bor", default=False)
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
        return self.name


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


# What a payment actually leaves in the SELLER's till: the gross paid minus the bank
# fee. Cash/card carry no commission, so net == amount there. The till loses the fee
# whichever way `commission_payer` points — the bank takes its cut before the money
# lands. Who ends up bearing it is a debt-side question; see PAYMENT_CREDIT.
PAYMENT_NET = ExpressionWrapper(F("amount") - F("commission"), output_field=MONEY)


# What a payment takes off the CLIENT's debt. Per-payment choice: with the fee on the
# seller the client is credited everything they sent; with the fee on the client only
# the net clears, so they still owe the bank's cut.
PAYMENT_CREDIT = Case(
    When(commission_payer="client", then=F("amount") - F("commission")),
    default=F("amount"),
    output_field=MONEY,
)


# Payment kinds that represent the client paying money INTO a sale. The
# settlement kinds also carry a `sale`, but they move money the other way,
# so they must never be counted here — see `_sale_settlement_sum`.
PAYING_KINDS = ("sale", "debt", "advance_used")


# Money handed BACK to the client on a sale, grouped by what it does to the till.
# Two situations produce it and they must stay tellable apart — goods came back
# (return_*), or the goods are still with the client and only the price was wrong
# (adjust_*) — but the money behaves identically, so every balance reads the group,
# never the individual kind. Written as raw strings, like PAYING_KINDS above,
# because they are needed before `Payment.Kind` exists.
#
# CREDIT_BACK: nothing leaves the drawer — the cash is already in it from the
# original payment and is merely re-labelled as the client's advance credit.
# REFUND: cash physically handed over, so it leaves the till.
CREDIT_BACK_KINDS = ("return_credit", "adjust_credit")
REFUND_KINDS = ("refund_out", "adjust_refund")
SETTLEMENT_KINDS = CREDIT_BACK_KINDS + REFUND_KINDS


# Everything that takes cash back OUT of the till and hands it to a client. The
# settlement refunds above are one route; giving a client their prepaid advance back
# (`advance_out`) is the other, and it hangs off no sale at all. They are grouped
# because every till figure — cash on hand, the chiqim ledger, the drawer summary —
# cares only that the money left, not why. What tells them apart is the debt side:
# a refund settles a receipt, an advance return only shrinks the credit the client
# was holding (see `client_advance_balance`).
CASH_OUT_KINDS = REFUND_KINDS + ("advance_out",)


# The two sides of a client's advance pool. Credit goes in as a deposit or as money
# owed back on a sale; it leaves either onto a receipt (`advance_used`) or into the
# client's hand (`advance_out`). `client_advance_balance` is deposits minus spent, and
# the batched maps in the views must count exactly these — a list that disagrees with
# the balance on the client's own page is worse than no figure at all.
ADVANCE_DEPOSIT_KINDS = ("advance_in",) + CREDIT_BACK_KINDS
ADVANCE_SPENT_KINDS = ("advance_used", "advance_out")


# A deposit whose figure was wrong can be corrected without rewriting the day it was
# taken: the difference is written as its own dated row instead. Money-wise such a row
# is an ordinary deposit or an ordinary advance return — no till or balance figure
# needs to know it apart, which is exactly why it gets no new `Payment.Kind`. Only the
# screens care, so that a correction is not read as "the client took their money back",
# and they tell it by this note prefix.
ADVANCE_ADJUST_NOTE = "Avans tuzatildi"


def _sale_paid_sum():
    """A subquery summing the payments credited against one sale.

    How much a transfer clears depends on who was made to carry the bank's fee — see
    `PAYMENT_CREDIT`. With the fee on the seller a 100k transfer clears the full 100k;
    with it on the client only 95k of the debt goes away and they still owe 5k."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(sale=OuterRef("pk"), kind__in=PAYING_KINDS)
            .values("sale")
            .annotate(s=Sum(PAYMENT_CREDIT))
            .values("s"),
            output_field=MONEY,
        ),
        Value(Decimal("0"), output_field=MONEY),
    )


def _sale_settlement_sum():
    """A subquery summing what has been given back to the client on one sale —
    settled either as advance credit (CREDIT_BACK_KINDS) or as cash handed over
    (REFUND_KINDS). Two things put money here: the excess of a return over the debt
    it cancelled, and a downward price correction on an already-paid sale.

    This is what stops such a sale from sitting at a permanent negative balance: the
    value leaves via `returned` (or via the lower total), the money the client had
    already paid comes back via `settled`, and the receipt lands back on zero."""
    return Coalesce(
        Subquery(
            Payment.objects.filter(
                sale=OuterRef("pk"),
                kind__in=SETTLEMENT_KINDS,
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
        ).annotate(
            # opening_amount (0 on a normal sale) is the carried-over pre-CRM debt; it
            # lifts the receivable without ever touching the revenue/profit annotations.
            remaining=F("net_revenue") - F("net_paid") + F("opening_amount")
        )

    def outstanding(self):
        """Sales that still owe money (a receivable / qarz)."""
        return self.with_balance().filter(remaining__gt=0)

    def visible_to(self, user):
        return self if user.can_see_all_records else self.filter(sales_rep=user)

    def real(self):
        """Only genuine sales — drops the opening-balance carry-overs. The sales lists
        and sales figures use this; the debt/receivable views deliberately do not, so a
        client's old balance still shows as money owed."""
        return self.filter(is_opening=False)


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
    # An opening balance carried over from before go-live: a client's old debt that
    # was never a CRM sale. Such a "sale" has NO line items — the debt is this amount
    # alone, and it flows into `debt_remaining`/`remaining` only. Because it has no
    # items, every revenue/cost/profit/sold-kg report (which sums SaleItems) ignores
    # it automatically; it shows up purely as a receivable. Paid down by ordinary debt
    # payments. `is_opening` marks these so the sales lists can hide them.
    is_opening = models.BooleanField("Ochilish qoldig'i", default=False)
    opening_amount = models.DecimalField(
        "Ochilish qarzi (so'm)", max_digits=18, decimal_places=2, default=0
    )
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
        """Short label for list rows: the first product's name (with its razmer/mikron
        when the line has one) plus a "+N" for any further lines."""
        if self.is_opening:
            return "Ochilish qoldig'i (eski qarz)"
        items = list(self.items.all())
        if not items:
            return "—"
        item = items[0]
        first = item.product.name
        if item.variant_label:
            first = f"{first} · {item.variant_label}"
        extra = len(items) - 1
        return f"{first}  +{extra}" if extra > 0 else first

    @property
    def paid_amount(self):
        # Credited, not gross: a transfer whose fee was put on the client clears only
        # its net. Limited to the kinds that move money IN — a settlement row also
        # carries this sale.
        return (
            self.payments.filter(kind__in=PAYING_KINDS)
            .aggregate(s=Sum(PAYMENT_CREDIT))["s"]
            or Decimal("0")
        )

    @property
    def settled_amount(self):
        """Money handed back to the client on this sale — the over-returned excess or a
        downward price correction, parked as advance credit or paid out in cash."""
        return (
            self.payments.filter(kind__in=SETTLEMENT_KINDS)
            .aggregate(s=Sum("amount"))["s"]
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
        # opening_amount is the pre-CRM carried-over debt (0 on a normal sale); it adds
        # straight onto what the client owes and is whittled down by debt payments.
        return (
            self.net_total + self.opening_amount - (self.paid_amount - self.settled_amount)
        )

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
    # Product variant chosen for this line — optional, and only offered when the
    # product supports it (see Product.has_size / has_micron). Descriptive only:
    # they do not change the price, which comes from the product. Free text: the
    # form offers SIZE_CHOICES / MICRON_CHOICES as datalist suggestions, but the
    # seller may type any value.
    size = models.CharField("Razmer", max_length=20, blank=True)
    micron = models.CharField("Mikron", max_length=20, blank=True)
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
    def variant_label(self):
        """Razmer + mikron as a short label ("1,5м · 015"), or "" if the line has
        neither — used wherever the sold item is shown."""
        return " · ".join(p for p in (self.size, self.micron) if p)

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

        ADVANCE_USED is excluded: that money already entered the till as an ADVANCE_IN
        deposit, so counting the consumption again would double it. So is CREDIT_BACK —
        likewise already in the till from the original payment, merely re-labelled as
        the client's credit — and so is every CASH_OUT kind, which is money leaving
        rather than arriving; those are subtracted separately in `seller_cash_on_hand`.

        Per-sale debt math is separate and DOES count ADVANCE_USED (it settles the
        receipt) — see `_sale_paid_sum`.

        Advances marked `is_opening` are also excluded: that cash is not sitting in any
        till today — either it arrived pre-CRM, or the seller booked the advance
        precisely because the money had already been taken in earlier — so it only
        carries the client's prepaid credit and must never inflate cash on hand."""
        return self.exclude(is_opening=True).exclude(
            kind__in=(self.model.Kind.ADVANCE_USED,) + CREDIT_BACK_KINDS + CASH_OUT_KINDS
        )

    def till_outflow(self):
        """The mirror of `till_income`: money physically handed back to a client, so
        it has to come off the drawer.

        `is_opening` reads the same on both sides — the row moves the client's balance
        but its cash is not in today's till. An advance returned outside the kassa is
        the case: the client's credit goes, yet no note left the drawer, so charging
        the till for it would leave the drawer short by money it never held."""
        return self.filter(kind__in=CASH_OUT_KINDS).exclude(is_opening=True)


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
        # Held credit given back to the client in cash instead of being spent on a
        # sale. It shrinks their advance balance exactly like ADVANCE_USED does, but
        # the money leaves the drawer, so the till counts it as an outflow. The
        # deposit it undoes is left standing: the cash really did come in on its own
        # date, and rubbing that day's kirim out to account for money returned weeks
        # later is how a closed day silently changes underneath the person who
        # counted it.
        ADVANCE_OUT = "advance_out", "Avans qaytarildi"
        # Settlement of a return whose value exceeds the sale's open debt — the client
        # had already paid for those goods, so the money is owed back to them.
        # RETURN_CREDIT parks it as advance credit: no cash moves (it is already in the
        # till from the original payment), so it must stay out of till income while
        # still counting toward the client's advance balance. REFUND_OUT is the other
        # route — cash physically handed back, so it leaves the till.
        RETURN_CREDIT = "return_credit", "Qaytarishdan kredit"
        REFUND_OUT = "refund_out", "Qaytarish (naqd berildi)"
        # The same two routes, for the other reason money can be owed back: the goods
        # are still with the client and only the PRICE was wrong. A return would be the
        # wrong tool — it would shrink the sold kg, offer to restock goods that never
        # came back, and still leave the tannarx untouched. See `_settle_overpay`.
        ADJUST_CREDIT = "adjust_credit", "Narx tuzatildi (kreditga)"
        ADJUST_REFUND = "adjust_refund", "Narx tuzatildi (naqd berildi)"

    class Currency(models.TextChoices):
        UZS = "uzs", "So'm"
        USD = "usd", "Dollar"

    class Payer(models.TextChoices):
        """Who is out of pocket for a bank transfer's fee.

        SELLER: the client's debt falls by the whole sum they transferred and the fee
        comes out of the seller's till and earnings — the firm absorbs the bank's cut.
        CLIENT: only the net reaches the client's debt, so they still owe the fee and
        will have to send it separately — the older arrangement, kept because some
        clients are billed that way."""

        SELLER = "seller", "Sotuvchidan ushlansin"
        CLIENT = "client", "Mijozdan ushlansin"

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
    # Bank fee withheld on a transfer. It always leaves the till (the bank takes it
    # in transit); `commission_payer` decides who ends up bearing it — the seller,
    # whose earnings shrink, or the client, whose debt only falls by the net.
    commission = models.DecimalField(
        "Bank komissiyasi (so'm)", max_digits=18, decimal_places=2, default=0
    )
    # Percentage the bank withholds on a transfer; `commission` is derived from it.
    commission_percent = models.DecimalField(
        "Bank ushlagan foiz (%)", max_digits=5, decimal_places=2, default=0
    )
    # Chosen per payment on the form. Defaults to SELLER, which is also what every
    # pre-existing row gets: the fee was already coming out of the till, so treating
    # history as seller-borne keeps those tills and debts exactly as audited.
    commission_payer = models.CharField(
        "Komissiyani kim ko'taradi", max_length=6,
        choices=Payer.choices, default=Payer.SELLER,
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    kind = models.CharField("Turi", max_length=16, choices=Kind.choices)
    # An advance whose cash is NOT in any till today, so till_income() drops it. It
    # still counts toward the client's advance balance like any ADVANCE_IN. Two things
    # set it: the pre-CRM balances the import commands carry in, and an advance the
    # seller books for money that was already taken into the drawer earlier — the old
    # sverkas are full of those, and counting the kirim a second time now would inflate
    # the day's income by cash nobody handed over today.
    is_opening = models.BooleanField("Ochilish avansi", default=False)
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
        """What actually reaches the seller's till after the bank fee — always the
        gross less the fee, since the bank takes its cut before the money lands."""
        return self.amount - (self.commission or Decimal("0"))

    @property
    def credited_amount(self):
        """What this payment takes off the client's debt: everything they sent, unless
        the fee was put on them, in which case only the net clears and the fee stays
        on their balance."""
        if self.commission_payer == self.Payer.CLIENT:
            return self.net_amount
        return self.amount

    @property
    def fee_on_client(self):
        return bool(self.commission) and self.commission_payer == self.Payer.CLIENT

    @property
    def original_amount(self):
        """The amount in the currency the client actually handed over — the dollars
        for a USD payment, otherwise the so'm figure. Stored on entry; older rows
        (recorded before this field existed) fall back to the so'm `amount`."""
        return self.amount_original or self.amount

    def __str__(self):
        return f"{self.get_kind_display()}: {self.amount} so'm ({self.date})"


def first_of_month():
    """This month's first day — the default month a payroll account opens on."""
    return timezone.localdate().replace(day=1)


def month_span(start, end):
    """Every (year, month) from `start` through `end`, inclusive. Both are dates; only
    their year/month matter. Empty when `end` falls before `start`."""
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


class Employee(models.Model):
    """A salaried worker (Xodim), the monthly wage they are owed, and what is left of
    it once the till has paid out.

    Deliberately NOT a CRM login: these are the people on the payroll, most of whom
    never touch the system. Money reaches them as an ordinary till outflow tagged with
    `Expense.employee`, so a wage payment is a kassa chiqim like any other — what this
    model adds is the monthly figure those payouts are measured against.

    A month does not settle itself. Whatever is left unpaid rides into the next month
    (and an advance drawn beyond the wage rides in the other direction), so the balance
    is cumulative from `start_month` — see `balance_through`. That accumulation is why
    the account needs an explicit opening: without one, adding a worker who has been on
    the job for a year would invent a year of unpaid wages on the spot."""

    name = models.CharField("Ismi", max_length=120)
    # The CURRENT wage. Every month's own figure lives in `rates` (SalaryRate); this
    # field is the latest of those, kept for the forms, the pickers and every existing
    # caller that just wants "what do they earn now".
    salary = models.DecimalField("Oylik (so'm)", max_digits=18, decimal_places=2)
    # The first month this account is accountable for. Nothing accrues before it, so a
    # worker who has been here for years can still be entered today without the CRM
    # claiming to know what happened before it was told.
    start_month = models.DateField("Hisob boshlangan oy", default=first_of_month)
    # What was already owed on the first day of `start_month`. Positive: the firm owes
    # them. Negative: they had drawn ahead. Typed in by hand, exactly like a client's
    # opening debt, because only the boss knows the figure the CRM never saw.
    opening_balance = models.DecimalField(
        "Boshlang'ich qoldiq (so'm)", max_digits=18, decimal_places=2, default=0
    )
    # The last month a wage accrues. Set when someone is taken off the payroll, so a
    # worker who left in March does not keep earning through December.
    end_month = models.DateField("Oxirgi oy", null=True, blank=True)
    is_active = models.BooleanField("Faol", default=True)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Xodim"
        verbose_name_plural = "Xodimlar"

    def paid_in(self, year, month):
        """What they have already drawn against that month's pay — the wage itself and
        any advance alike, since both are money against the same month.

        Expenses merely *tagged* with them are excluded: a worker who buys petrol or
        lunch for the business is spending the firm's money, not their own wage, so
        those rows are `counts_against_salary=False` and only move the till."""
        return (
            self.expenses.filter(
                date__year=year, date__month=month, counts_against_salary=True
            )
            .aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )

    def salary_for(self, year, month):
        """The wage in force in that month — the latest rate starting no later than it.

        Falls back to `salary` for a worker with no rate rows at all, which is what
        every record looked like before wages were dated."""
        rate = (
            self.rates.filter(effective_from__lte=date(year, month, 1))
            .order_by("-effective_from")
            .first()
        )
        return rate.amount if rate else self.salary

    def accrues_in(self, year, month):
        """Whether a wage is earned in that month: on or after the account opened, and
        not after the month they left."""
        first = date(year, month, 1)
        if first < self.start_month.replace(day=1):
            return False
        if self.end_month and first > self.end_month.replace(day=1):
            return False
        return True

    def accrued_in(self, year, month):
        """The wage earned in that month — zero outside the months they are on."""
        if not self.accrues_in(year, month):
            return Decimal("0")
        return self.salary_for(year, month)

    def remaining_in(self, year, month):
        """What that month ALONE left over: its wage less what was drawn against it.
        Deliberately ignores earlier months — `balance_through` is the running figure."""
        return self.accrued_in(year, month) - self.paid_in(year, month)

    def balance_through(self, year, month):
        """Everything owed minus everything drawn, from the opening balance through the
        end of the given month. Positive: still owed to the worker — this is the sum
        that rides into next month. Negative: they have drawn ahead of their wage.

        Single-employee use (a detail page, a test). The payroll list computes the same
        figure for everyone in a couple of queries — see `_payroll_rows`."""
        total = self.opening_balance
        for y, m in month_span(self.start_month, date(year, month, 1)):
            total += self.accrued_in(y, m) - self.paid_in(y, m)
        return total

    def __str__(self):
        return self.name


class SalaryRate(models.Model):
    """What a worker's monthly wage is, from one month onward.

    A wage is not a fact about today — it is a fact about each month it was in force.
    With only the current figure on `Employee`, a raise silently re-priced every month
    behind it: last month's agreed balance would change because this month's pay went
    up. Each row here says "from this month on, the wage is X"; any month reads the
    latest row not after it (`Employee.salary_for`)."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="rates", verbose_name="Xodim"
    )
    # Always the 1st: a wage changes by the month, never mid-month.
    effective_from = models.DateField("Qaysi oydan")
    amount = models.DecimalField("Oylik (so'm)", max_digits=18, decimal_places=2)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_rates",
        verbose_name="Kim belgiladi",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "effective_from"], name="one_rate_per_month"
            )
        ]
        verbose_name = "Oylik stavkasi"
        verbose_name_plural = "Oylik stavkalari"

    def __str__(self):
        return f"{self.employee.name}: {self.amount} so'm ({self.effective_from:%m.%Y})"


class Expense(models.Model):
    """A cash-register outflow (Chiqim): money paid out of the till — fuel,
    salaries, meals, purchases, and the like. Reduces the kassa balance. Unlike a
    bank commission (which the client bears), an expense is the business's own cost,
    tagged with the wallet it left (naqd/karta/bank) so each method's balance is right."""

    # The categories the business started with. They are datalist *suggestions*
    # only — `category` is free text, so a new kind of outflow can simply be typed
    # and it joins the suggestions for next time (see `Expense.used_categories`).
    CATEGORY_SUGGESTIONS = [
        "Benzin / transport",
        "Oylik / xodim",
        "Ijara",
        "Ovqat (obed)",
        "Mahsulot xaridi",
        "Boshqa",
    ]

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
    # Free text: whatever the bookkeeper actually calls this outflow. The form
    # offers what's been used before as suggestions, but never restricts. More than
    # one may be ticked ("Ovqat (obed), СУВ") — they are joined with ", " into this
    # one field, which is why it is wider than a single label needs.
    category = models.CharField("Turkum", max_length=120)
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Payment.Method.choices,
        default=Payment.Method.CASH,
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    # Set when a payroll worker is involved in this outflow — either as the person
    # being paid, or as the person who spent the money. Left empty for every other
    # kind of expense.
    employee = models.ForeignKey(
        "Employee",
        on_delete=models.PROTECT,
        related_name="expenses",
        verbose_name="Xodim",
        null=True,
        blank=True,
    )
    # Only meaningful alongside `employee`, and the reason tagging someone does not
    # by itself touch their pay: a wage or an advance is money against this month's
    # salary, but petrol or lunch the worker bought for the business is not — that
    # leaves the till and stops there. `Employee.paid_in` counts only the former,
    # so the worker can still be named on the receipt without losing wage over it.
    counts_against_salary = models.BooleanField("Oyligidan ushlansin", default=True)
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

    @classmethod
    def used_categories(cls):
        """Every category anyone has actually written, most-used first, with the
        starter list appended so a fresh database still offers something. This is
        what the form's datalist and the filter dropdown are built from."""
        counts = (
            cls.objects.exclude(category="")
            .values("category")
            .annotate(n=Count("pk"))
            .order_by("-n", "category")
        )
        seen = [row["category"] for row in counts]
        return seen + [c for c in cls.CATEGORY_SUGGESTIONS if c not in seen]

    def __str__(self):
        return f"{self.category}: {self.amount} so'm ({self.date})"


class ProductionRemittance(models.Model):
    """Money a seller hands back to production (Ishlab chiqarishga topshirish).

    The firm's flow: a seller takes goods from the shared warehouse and sells them
    on to clients at a markup; the *cost price* (tannarx) of what they've sold is
    the seller's debt to production. When the seller hands their collected cash to
    production, that debt shrinks and the cash leaves the seller's till. So a
    remittance is both a till outflow AND a repayment of the seller→production debt —
    it is NOT an ordinary expense (an expense is the business's own cost).

    Always so'm: the production debt is denominated in so'm (tannarx is stored in
    so'm), so a handover is recorded in so'm too.

    A NEGATIVE `amount` is the same movement running backwards: production handing
    cash back to the seller (Ishlab chiqarishdan qaytarish). Storing it as a signed
    amount on this one model is deliberate — every figure that touches handovers is a
    plain `Sum("amount")`, so a return raises the seller's till and their production
    debt automatically, with no aggregate left to update separately."""

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

    @property
    def is_refund(self):
        """True when production handed the cash back instead of receiving it."""
        return self.amount < 0

    @property
    def abs_amount(self):
        """The figure people say out loud — a return of 50 000 is "50 000", not
        "−50 000". The sign only ever lives in the stored `amount`."""
        return abs(self.amount)

    def __str__(self):
        label = "Qaytarish" if self.is_refund else "Topshiruv"
        return f"{label} · {self.seller}: {self.abs_amount:,.0f} so'm ({self.date})"


class ProductionAdjustment(models.Model):
    """An admin correction to what a seller owes production, with NO money moving.

    The debt is otherwise fully derived — opening balance, plus the tannarx of what
    the seller sold, less restocked returns, less what they handed over — and each of
    those terms has a document behind it. Reality sometimes disagrees with the sum
    anyway: an old ledger was wrong, or a figure was carried over badly. This is the
    one place that difference can be written down and named.

    The line NOT to cross: a remittance moves cash (it leaves the till AND pays down
    the debt); an adjustment only restates the debt. Recording a forgotten handover
    here would fix the debt and leave the till permanently overstated — which is why
    `reason` is mandatory and the form steers those cases to the right tool instead.

    A NEGATIVE `amount` lowers the debt, positive raises it, so every figure that
    reads adjustments is a plain `Sum("amount")` — the same trick
    `ProductionRemittance` uses for returns."""

    class Reason(models.TextChoices):
        LEDGER = "ledger", "Eski daftar/sverka xato edi"
        REMITTANCE = "remittance", "Topshirilgan pul kiritilmagan"
        SALE = "sale", "Sotuv kiritilmagan"
        OTHER = "other", "Boshqa"

    date = models.DateField("Sana", default=timezone.localdate)
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="production_adjustments",
        verbose_name="Sotuvchi",
    )
    amount = models.DecimalField("Summa (so'm)", max_digits=18, decimal_places=2)
    reason = models.CharField("Sababi", max_length=12, choices=Reason.choices)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_production_adjustments",
        verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ishlab chiqarish qarzi tuzatishi"
        verbose_name_plural = "Ishlab chiqarish qarzi tuzatishlari"

    @property
    def lowers_debt(self):
        return self.amount < 0

    @property
    def abs_amount(self):
        """The figure people say out loud — the sign only lives in `amount`."""
        return abs(self.amount)

    def __str__(self):
        sign = "−" if self.lowers_debt else "+"
        return f"Tuzatish · {self.seller}: {sign}{self.abs_amount:,.0f} so'm ({self.date})"


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


def _through(queryset, day):
    """Everything up to and including `day` — or the lot when no day is given."""
    return queryset.filter(date__lte=day) if day else queryset


def _excluding(queryset, pk):
    return queryset.exclude(pk=pk) if pk else queryset


def seller_cash_on_hand(
    seller, exclude_remittance_pk=None, exclude_payout_pk=None,
    through=None, exclude_expense_pk=None,
):
    """Cash physically in a seller's till right now — the same figure the kassa page
    shows as "Kassadagi pul": net client payments they collected, minus cash refunded
    to clients, minus expenses they paid out, minus what they've handed to production,
    minus profit already handed to the boss. A new handover can't exceed this
    (otherwise the till would go negative). The `exclude_*_pk` args drop one existing
    row from the tally so editing it checks against the delta, not itself.

    `through` cuts the tally at a date — what the till held at the END of that day.
    Without it the figure is date-blind, and a payout backdated into a day the seller
    had nothing on sails through: the total is fat with money collected since, while
    the day itself quietly goes negative and drags every later day down with it. That
    is exactly how a wage handed over on 17.08 but dated 31.07 put six days in the
    red without a single warning."""
    income = (
        _through(Payment.objects.filter(created_by=seller).till_income(), through)
        .aggregate(s=Sum(PAYMENT_NET))["s"]
        or Decimal("0")
    )
    # Cash handed back to a client — on an over-returned or over-priced sale, or as
    # their advance returned to them. These are recorded without a bank fee, so the
    # full amount is what leaves the drawer.
    refunded = (
        _through(Payment.objects.filter(created_by=seller).till_outflow(), through)
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    expense = (
        _through(_excluding(Expense.objects.filter(created_by=seller), exclude_expense_pk), through)
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    remitted_qs = ProductionRemittance.objects.filter(seller=seller)
    if exclude_remittance_pk:
        remitted_qs = remitted_qs.exclude(pk=exclude_remittance_pk)
    remitted = _through(remitted_qs, through).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    payout_qs = ProfitPayout.objects.filter(seller=seller)
    if exclude_payout_pk:
        payout_qs = payout_qs.exclude(pk=exclude_payout_pk)
    paid_profit = _through(payout_qs, through).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return income - refunded - expense - remitted - paid_profit


def client_advance_balance(client, seller=None):
    """The credit a client holds: money put in (ADVANCE_IN deposits, plus the
    CREDIT_BACK_KINDS owed back from over-returned or price-corrected sales) minus what
    has since left the pool — spent on a sale (ADVANCE_USED) or handed back to the
    client in cash (ADVANCE_OUT).
    Positive = money held that the client hasn't taken goods for yet; zero = nothing
    prepaid. Each deposit counts for whatever it credited the client with, so a bank
    fee only shrinks their credit when the fee was put on them. Advance is
    seller-bound, so pass `seller` to get the balance in that one seller's till; omit
    it for the client's total across all sellers (the admin overview figure)."""
    rows = Payment.objects.filter(client=client)
    if seller is not None:
        rows = rows.filter(created_by=seller)
    deposited = (
        rows.filter(kind__in=ADVANCE_DEPOSIT_KINDS)
        .aggregate(s=Sum(PAYMENT_CREDIT))["s"]
        or Decimal("0")
    )
    used = (
        rows.filter(kind__in=ADVANCE_SPENT_KINDS)
        .aggregate(s=Sum(PAYMENT_CREDIT))["s"]
        or Decimal("0")
    )
    return deposited - used


def seller_production_debt(seller):
    """What a seller still owes production: the tannarx (cost) of everything they've
    sold, minus the tannarx of goods that came back into the warehouse, minus what
    they've already remitted.

    Only RESTOCKED returns count against the debt. If the goods did not come back
    (spoiled, written off) the seller consumed them, so they still owe production the
    cost — otherwise a write-off would silently erase a real liability.

    `opening_production_debt` is the carried-over pre-CRM liability (0 for a normal
    seller); it lifts the debt from day one and is paid down by the same remittances.

    Admin corrections (`ProductionAdjustment`) are added on top, signed. They restate
    the debt without any money moving, so they deliberately touch nothing else — the
    till, the profit and every sales figure are left exactly as they were."""
    opening = seller.opening_production_debt or Decimal("0")
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
    adjusted = (
        ProductionAdjustment.objects.filter(seller=seller).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    return opening + sold_cost - remitted + adjusted


def seller_remitted_total(seller, exclude_remittance_pk=None):
    """Net cash a seller has handed to production: handovers minus anything production
    has already handed back. This is the ceiling on a new return — production can't
    give back more than it ever received. `exclude_remittance_pk` drops one existing
    row so editing it checks against the delta, not itself."""
    qs = ProductionRemittance.objects.filter(seller=seller)
    if exclude_remittance_pk:
        qs = qs.exclude(pk=exclude_remittance_pk)
    return qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")


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
        # Records that move no money but decide what the money later does — who the
        # client is, what a product costs, how much stock there is, who may log in.
        if t == "Mijoz":
            if a == self.Action.DELETE:
                return e("Mijoz o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Mijoz o'zgartirildi", AMBER, "edit")
            return e("Mijoz qo'shildi", GREEN, "dot")
        if t == "Mahsulot":
            if a == self.Action.DELETE:
                return e("Mahsulot o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Mahsulot o'zgartirildi", AMBER, "edit")
            return e("Mahsulot qo'shildi", GREEN, "dot")
        if t == "Ombor":
            if a == self.Action.UPDATE:
                return e("Ombor miqdori tuzatildi", AMBER, "edit")
            return e("Omborga kirim", GREEN, "in")
        if t == "Xodim":
            if a == self.Action.DELETE:
                return e("Xodim o'chirildi", RED, "trash")
            if a == self.Action.UPDATE:
                return e("Xodim o'zgartirildi", AMBER, "edit")
            return e("Xodim qo'shildi", GREEN, "dot")
        if t == "Foydalanuvchi":
            if a == self.Action.UPDATE:
                return e("Foydalanuvchi o'zgartirildi", AMBER, "edit")
            return e("Foydalanuvchi yaratildi", "badge-info", "dot")
        if a == self.Action.TRANSFER:
            return e("Sotuvchi o'zgardi", GREY, "transfer")
        return e(self.get_action_display(), GREY, "dot")

    def __str__(self):
        return f"{self.get_action_display()} · {self.target_type} · {self.summary}"
