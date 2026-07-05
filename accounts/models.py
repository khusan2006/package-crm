from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Menejer"
        SALES = "sales", "Sotuvchi"

    role = models.CharField("Rol", max_length=10, choices=Role.choices, default=Role.SALES)
    phone = models.CharField("Telefon", max_length=30, blank=True)

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
