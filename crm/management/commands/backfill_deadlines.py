"""Give every debt a payment deadline of sale date + N days (default 14).

Idempotent safety net: it only touches sales whose `debt_deadline` is still empty, so
it fixes rows imported before deadlines were set (the first detailed go-live import left
ordinary sales without one) and is a plain no-op afterwards. Safe to keep in the deploy
start command — it never overwrites a deadline a user or the importer already set.
"""

import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Sale

DEADLINE_DAYS = 14


class Command(BaseCommand):
    help = "Muddati yo'q sotuvlarga to'lov muddati = sana + 14 kun qo'yadi (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=DEADLINE_DAYS,
                            help=f"To'lov muddati = sana + shu kun (default: {DEADLINE_DAYS})")
        parser.add_argument("--dry-run", action="store_true",
                            help="Faqat nechta sotuv o'zgarishini ko'rsatadi, yozmaydi.")

    def handle(self, *args, **opt):
        days = opt["days"]
        qs = Sale.objects.filter(debt_deadline__isnull=True)
        n = qs.count()
        if not n:
            self.stdout.write("Muddatsiz sotuv yo'q — hech narsa o'zgartirilmadi.")
            return
        if opt["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"DRY-RUN: {n} ta sotuvga sana + {days} kun muddat qo'yilardi."))
            return
        with transaction.atomic():
            updated = 0
            for sale in qs.only("id", "date").iterator():
                sale.debt_deadline = sale.date + datetime.timedelta(days=days)
                sale.save(update_fields=["debt_deadline"])
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f"{updated} ta sotuvga to'lov muddati (sana + {days} kun) qo'yildi."))
