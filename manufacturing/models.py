from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from crm.models import Payment  # Method choices reused

MONEY = models.DecimalField(max_digits=18, decimal_places=2)
QTY = models.DecimalField(max_digits=18, decimal_places=3)


class RawMaterial(models.Model):
    """A raw material (xomashyo) bought to manufacture finished products. Priced
    in so'm per kg; `avg_cost` is the running weighted average of stock on hand,
    recomputed from purchase/consumption history on every change."""

    name = models.CharField("Nomi", max_length=200)
    sku = models.CharField("Artikul (SKU)", max_length=50, blank=True)
    note = models.TextField("Izoh", blank=True)
    avg_cost = models.DecimalField(
        "O'rtacha tannarx (1 kg, so'm)", max_digits=14, decimal_places=2, default=0
    )
    low_stock_threshold = models.DecimalField(
        "Kam qoldi chegarasi (kg)", max_digits=12, decimal_places=3, default=0
    )
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Xomashyo"
        verbose_name_plural = "Xomashyolar"

    @property
    def total_purchased(self):
        return self.purchases.aggregate(s=Sum("quantity_kg"))["s"] or Decimal("0")

    @property
    def total_consumed(self):
        return (
            ProductionRunItem.objects.filter(material=self).aggregate(s=Sum("quantity_kg"))["s"]
            or Decimal("0")
        )

    @property
    def current_stock(self):
        return self.total_purchased - self.total_consumed

    @property
    def is_low_stock(self):
        return self.current_stock <= self.low_stock_threshold

    def __str__(self):
        return self.name


class MaterialPurchase(models.Model):
    """A raw-material purchase at a point-in-time price. Also the sklad kassa's
    money outflow — `method` says which wallet it left (naqd/karta/bank)."""

    material = models.ForeignKey(
        RawMaterial, on_delete=models.PROTECT, related_name="purchases", verbose_name="Xomashyo"
    )
    date = models.DateField("Sana", default=timezone.localdate)
    quantity_kg = models.DecimalField("Miqdor (kg)", max_digits=12, decimal_places=3)
    price_per_kg = models.DecimalField("Narx (1 kg, so'm)", max_digits=14, decimal_places=2)
    supplier = models.CharField("Yetkazib beruvchi", max_length=200, blank=True)
    method = models.CharField(
        "To'lov usuli", max_length=8, choices=Payment.Method.choices, default=Payment.Method.CASH
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="material_purchases", verbose_name="Kim kiritdi",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Xomashyo xaridi"
        verbose_name_plural = "Xomashyo xaridlari"

    @property
    def total(self):
        return self.quantity_kg * self.price_per_kg

    def __str__(self):
        return f"{self.material.name}: {self.quantity_kg} kg × {self.price_per_kg} ({self.date})"


class ProductionRunItem(models.Model):
    quantity_kg = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name="usages")
    # Fully defined in Task 3.

    class Meta:
        app_label = "manufacturing"
