"""One-shot go-live data setup — safe to keep in the deploy start command.

Wipes every crm_* table (clients, products, sales, payments, returns, production
records) — but NEVER user accounts — then loads the real go-live state:

  1. the 7 priced products              (seed_client_products)
  2. the reconciled opening debts + advances from data/sverka.xlsx (АКТ СВЕРКА:
     ОСТАТКА + ОЛДИ), dated by ОЛДИ, deadline = ОЛДИ + 14 kun     (import_debts_from_sverka)
  3. the seller's opening production debt (ishlab chiqarishga qarz)

Everything is assigned to the seller found by e-mail (SELLER_EMAIL) — on production
that user already exists; his username may differ, so we match on the address.

Runs ONCE. The guard is that seller's opening_production_debt already being set, so a
redeploy is a plain no-op and can never wipe data users entered afterwards. If the
seller isn't found (e.g. locally before he's created), the command does nothing.
"""

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from crm.models import (
    AuditLog,
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
)

# Everything is booked to the seller with this e-mail (production account).
SELLER_EMAIL = "komola@test.com"
PRODUCTION_DEBT = "975956588"      # ishlab chiqarishga ochilish qarzi
DEADLINE_DAYS = 14                 # to'lov muddati = ОЛДИ + 14 kun
SVERKA_FILE = Path(settings.BASE_DIR) / "data" / "sverka.xlsx"

# crm_* models to empty, in FK-safe order (children before parents). User accounts
# and django_* tables are deliberately left untouched.
_CLEAR_ORDER = (
    Return, Payment, SaleItem, Sale, Expense,
    ProductionReceiptItem, ProductionReceipt, ProductionRemittance,
    ProfitPayout, StockEntry, AuditLog, Client, Product,
)


class Command(BaseCommand):
    help = (
        "Bir martalik go-live: eski crm ma'lumotini tozalab (userlar qoladi), "
        "mahsulot + qarz + avans + ishlab chiqarish qarzini komola'ga yuklaydi."
    )

    def handle(self, *args, **opt):
        seller = User.objects.filter(email__iexact=SELLER_EMAIL).first()
        if seller is None:
            self.stdout.write(f"'{SELLER_EMAIL}' topilmadi — o'tkazib yuborildi (no-op).")
            return
        if seller.opening_production_debt and seller.opening_production_debt > 0:
            self.stdout.write("Go-live allaqachon o'rnatilgan — o'tkazib yuborildi (no-op).")
            return
        if not SVERKA_FILE.exists():
            self.stdout.write(self.style.WARNING(f"Fayl topilmadi: {SVERKA_FILE} — to'xtatildi."))
            return

        with transaction.atomic():
            self._clear_crm()
            call_command("seed_client_products")
            call_command(
                "import_debts_from_sverka",
                file=str(SVERKA_FILE),
                seller_email=SELLER_EMAIL,
                production_debt=PRODUCTION_DEBT,
                deadline_days=DEADLINE_DAYS,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Go-live ma'lumotlari o'rnatildi → {seller.username} ({SELLER_EMAIL})."
        ))

    def _clear_crm(self):
        """Empty every crm_* table; user accounts are never touched."""
        for model in _CLEAR_ORDER:
            model.objects.all().delete()
        self.stdout.write("Eski crm ma'lumotlari tozalandi (userlar qoldi).")
