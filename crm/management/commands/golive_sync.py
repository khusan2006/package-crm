"""One-shot, VERSIONED go-live sync for production — safe to keep in the deploy start
command.

What it does, in one self-contained step:
  1. wipes every crm_* table (clients, products, sales, payments, returns, production
     records) but NEVER user/seller accounts, and zeroes any carried production debt;
  2. reloads the FULL detailed state from data/sverka.xlsx via `import_hozmag`
     (clients + sales + payments + opening debts + advances), so every client balance
     reconciles to the АКТ СВЕРКА sheet, and the seller's production debt lands on
     PRODUCTION_DEBT (975 956 588);
  3. fills payment deadlines (ОЛДИ + 14 kun) via `backfill_deadlines` /
     `redate_opening_debts`.

Everything is booked to the seller found by e-mail (SELLER_EMAIL); on production that
account already exists (his username may differ, so we match on the address).

WHY a new command instead of the old `setup_go_live`: that one used
`import_hozmag --once`, which skips forever once ANY go-live has run — so a redeploy
could never clear leftover/old data, and stale + new data stayed mixed. This one is
guarded by GOLIVE_VERSION: it runs once PER VERSION and stamps an AuditLog marker.
A same-version redeploy is a plain no-op (it will NOT wipe data users entered
afterwards). To force a fresh wipe+reload, bump GOLIVE_VERSION (or pass --force).
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from accounts.models import User
from crm.models import AuditLog

# Everything is booked to the seller with this e-mail (production account).
SELLER_EMAIL = "komola@test.com"
PRODUCTION_DEBT = "975956588"       # bugungi haqiqiy ishlab chiqarish qarzi

# Bump this string to force a fresh wipe+reload on the NEXT deploy. As long as it is
# unchanged, the command is a no-op after the first successful run.
GOLIVE_VERSION = "2026-07-29-1"
MARKER_TYPE = "GOLIVE_SYNC"          # AuditLog.target_type used as the run marker


class Command(BaseCommand):
    help = (
        "Bir martalik (versiyalangan) go-live sync: prod crm ma'lumotini tozalab "
        "(userlar qoladi), sverka.xlsx dan to'liq qayta yuklaydi. Har deploy'da xavfsiz."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Marker bo'lsa ham majburiy tozalab qayta yuklaydi.",
        )

    def handle(self, *args, **opt):
        # The seller must already exist (production). Locally, before he is created,
        # this is simply a no-op so the deploy start command never fails.
        seller = User.objects.filter(email__iexact=SELLER_EMAIL).first()
        if seller is None:
            self.stdout.write(
                f"golive_sync: '{SELLER_EMAIL}' topilmadi — o'tkazib yuborildi.")
            return

        summary = f"golive_sync v{GOLIVE_VERSION}"
        already = AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists()
        if already and not opt["force"]:
            self.stdout.write(
                f"golive_sync: v{GOLIVE_VERSION} allaqachon qo'llangan — "
                "o'tkazib yuborildi.")
            return

        # Full forced reload. import_hozmag wipes every crm_* table (keeps users) and
        # rebuilds clients / sales / payments / opening debts + production debt from the
        # workbook, reconciled against АКТ СВЕРКА.
        call_command(
            "import_hozmag",
            seller_email=SELLER_EMAIL,
            production_debt=PRODUCTION_DEBT,
        )
        # Idempotent deadline fixes (ОЛДИ + 14 kun).
        call_command("backfill_deadlines")
        call_command("redate_opening_debts")

        # Stamp the version AFTER the wipe (import_hozmag clears AuditLog too), so the
        # marker survives and the next same-version deploy is a no-op.
        AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)
        self.stdout.write(self.style.SUCCESS(
            f"golive_sync: v{GOLIVE_VERSION} qo'llandi → {seller.username}."))
