from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Menejer"
        SALES = "sales", "Sotuvchi"

    role = models.CharField("Rol", max_length=10, choices=Role.choices, default=Role.SALES)
    phone = models.CharField("Telefon", max_length=30, blank=True)
    # A carried-over pre-CRM debt this seller owes production at go-live: the tannarx of
    # goods taken before the CRM existed and not yet remitted. It behaves exactly like
    # real production debt — it lifts `seller_production_debt` from day one and is whittled
    # down by ProductionRemittances — but, having no SaleItems behind it, never touches any
    # revenue/profit/sold-kg figure. Mirrors the client-side opening debt (Sale.is_opening).
    opening_production_debt = models.DecimalField(
        "Ochilish ishlab chiqarish qarzi (so'm)", max_digits=18, decimal_places=2, default=0
    )

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    @property
    def is_manager_role(self):
        return self.role == self.Role.MANAGER

    @property
    def can_see_all_records(self):
        """Admins and managers see every client/order; sales see only their own."""
        return self.role in (self.Role.ADMIN, self.Role.MANAGER)

    def __str__(self):
        return self.get_full_name() or self.username
