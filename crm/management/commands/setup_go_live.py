"""One-shot go-live data setup — safe to keep in the deploy start command.

Loads the FULL detailed go-live state by delegating to `import_hozmag`, which:

  1. wipes every crm_* table (clients, products, sales, payments, returns, production
     records) — but NEVER user accounts — and zeroes any carried production debt;
  2. seeds the 7 priced catalogue products (razmer/mikron aware) for ongoing sales;
  3. imports every sale (СОТУВ), payment (ТЎЛОВ) and opening balance (БОШЛАҒИЧ САЛЬДО)
     from data/sverka.xlsx, so client debt = opening + sales − payments and the
     production debt = the tannarx of everything sold;
  4. records the cash already handed to production so the net production debt lands on
     PRODUCTION_DEBT (the real current figure the owner supplied).

Everything is booked to the seller found by e-mail (SELLER_EMAIL) — on production that
user already exists; his username may differ, so we match on the address.

Runs ONCE: `import_hozmag --once` skips silently once the go-live remittance exists, so a
redeploy is a plain no-op and can never wipe data users entered afterwards. If the seller
or file isn't there yet (e.g. locally before he's created), the command does nothing.
"""

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

# Everything is booked to the seller with this e-mail (production account).
SELLER_EMAIL = "komola@test.com"
PRODUCTION_DEBT = "975956588"      # bugungi kundagi haqiqiy ishlab chiqarish qarzi
SVERKA_FILE = Path(settings.BASE_DIR) / "data" / "sverka.xlsx"


class Command(BaseCommand):
    help = (
        "Bir martalik go-live: eski crm ma'lumotini tozalab (userlar qoladi), "
        "batafsil sotuv+to'lov+qarz va ishlab chiqarish qarzini komola'ga yuklaydi."
    )

    def handle(self, *args, **opt):
        call_command(
            "import_hozmag",
            once=True,
            seller_email=SELLER_EMAIL,
            production_debt=PRODUCTION_DEBT,
            file=str(SVERKA_FILE),
        )
        # Idempotent: fills a +14 kun deadline on any sale still missing one — fixes data
        # from an earlier import that left ordinary sales without a deadline, no-op after.
        call_command("backfill_deadlines")
        # Idempotent: re-dates opening debts to each client's ОЛДИ + 14 kun (an earlier
        # import used one fixed date). File-driven, no-op once they're already on ОЛДИ.
        call_command("redate_opening_debts")
