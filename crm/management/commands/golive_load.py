"""One-shot, VERSIONED production go-live loader — the SIMPLE (debts-only) state.

This is the single script the deploy runs to put production into the exact state we
built locally:

  1. wipes EVERY crm_* table (clients, products, sales, payments, returns, production
     records) but NEVER user/seller accounts, and zeroes any carried production debt;
  2. seeds the 7 priced catalogue products (for ongoing sales — no kassa impact);
  3. loads the net opening balances from data/sverka.xlsx (АКТ СВЕРКА) onto the seller
     found by e-mail (SELLER_EMAIL):
        * negative ОСТАТКА -> an opening-balance debt (is_opening Sale, no line items),
          so it shows purely as a receivable and never touches the kassa ledger;
        * positive ОСТАТКА -> an is_opening ADVANCE_IN credit: it carries the client's
          prepaid balance forward WITHOUT counting as kassa cash income;
        * production debt = PRODUCTION_DEBT (975 956 588) on opening_production_debt.
     Result: the Qarzlar section holds every debtor, the kassa transaction list stays
     empty, and the kassa shows only the production debt.

Everything is booked to the seller found by e-mail; on production that account already
exists (his username may differ, so we match on the address). Users are never created
or touched.

Runs ONCE per GOLIVE_VERSION (an AuditLog marker records it): safe to keep in the deploy
start command — a same-version redeploy is a plain no-op and will NOT wipe data users
entered afterwards. To force a fresh wipe+reload, bump GOLIVE_VERSION (or pass --force).
"""

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
PRODUCTION_DEBT = "975956588"        # bugungi haqiqiy ishlab chiqarish qarzi
FALLBACK_DATE = "2026-07-05"         # ОЛДИ sanasi yo'q qarzdorlar uchun (median)
SVERKA_FILE = Path(settings.BASE_DIR) / "data" / "sverka.xlsx"

# Bump this to force a fresh wipe+reload on the NEXT deploy. Unchanged -> no-op after
# the first successful run.
GOLIVE_VERSION = "2026-07-29-simple-1"
MARKER_TYPE = "GOLIVE_LOAD"          # AuditLog.target_type used as the run marker

# crm_* models to empty, children before parents. User accounts are never touched.
_CLEAR_ORDER = (
    Return, Payment, SaleItem, Sale, Expense,
    ProductionReceiptItem, ProductionReceipt, ProductionRemittance,
    ProfitPayout, StockEntry, AuditLog, Client, Product,
)


class Command(BaseCommand):
    help = (
        "Bir martalik (versiyalangan) go-live: prod crm ma'lumotini tozalab (userlar "
        "qoladi), sverka.xlsx dan qarzlar + mijozlar + ishlab chiqarish qarzini yuklaydi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Marker bo'lsa ham majburiy tozalab qayta yuklaydi.",
        )

    def handle(self, *args, **opt):
        # The seller must already exist (production). Locally, before he is created,
        # this is a no-op so the deploy start command never fails.
        seller = User.objects.filter(email__iexact=SELLER_EMAIL).first()
        if seller is None:
            self.stdout.write(
                f"golive_load: '{SELLER_EMAIL}' topilmadi — o'tkazib yuborildi.")
            return

        summary = f"golive_load v{GOLIVE_VERSION}"
        already = AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists()
        if already and not opt["force"]:
            self.stdout.write(
                f"golive_load: v{GOLIVE_VERSION} allaqachon qo'llangan — "
                "o'tkazib yuborildi.")
            return

        if not SVERKA_FILE.exists():
            self.stdout.write(self.style.WARNING(
                f"golive_load: fayl topilmadi ({SVERKA_FILE}) — to'xtatildi."))
            return

        with transaction.atomic():
            # 1. wipe every crm_* table (keep users) and zero any carried production debt
            for model in _CLEAR_ORDER:
                model.objects.all().delete()
            User.objects.exclude(opening_production_debt=0).update(
                opening_production_debt=0)
            self.stdout.write("Eski crm ma'lumotlari tozalandi (userlar qoldi).")

            # 2. clean catalogue products (ongoing sales; no kassa impact)
            call_command("seed_client_products")

            # 3. debts + advances + production debt from the reconciled sheet
            call_command(
                "import_debts_from_sverka",
                file=str(SVERKA_FILE),
                seller_email=SELLER_EMAIL,
                production_debt=PRODUCTION_DEBT,
                fallback_date=FALLBACK_DATE,
            )

            # 4. stamp the version so the next same-version deploy is a no-op
            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        self.stdout.write(self.style.SUCCESS(
            f"golive_load: v{GOLIVE_VERSION} qo'llandi → {seller.username}."))
