"""Re-date opening-balance debts to each client's ОЛДИ (last-took-goods) date + N days.

The first detailed go-live import dated every opening debt on one fixed day. This reads
the ОЛДИ column from the АКТ СВЕРКА sheet and re-dates each opening Sale (and its
deadline = ОЛДИ + days) so the debt ages from when the client actually last took goods.

Idempotent and file-driven: a Sale already on its ОЛДИ date is left untouched, so it is
safe to keep in the deploy start command — it corrects rows once and is a no-op after.
Clients with no usable ОЛДИ fall back to FALLBACK_OLDI.
"""

import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Sale
from crm.management.commands.import_hozmag import (
    DEADLINE_DAYS,
    DEFAULT_FILE,
    FALLBACK_OLDI,
    oldi_map,
)


class Command(BaseCommand):
    help = "Ochilish qarzlarini har mijozning ОЛДИ sanasi + 14 kunga o'tkazadi (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=DEFAULT_FILE,
                            help=f"ХОЗМАГЛАР .xlsx yo'li (default: {DEFAULT_FILE})")
        parser.add_argument("--days", type=int, default=DEADLINE_DAYS,
                            help=f"To'lov muddati = ОЛДИ + shu kun (default: {DEADLINE_DAYS})")
        parser.add_argument("--dry-run", action="store_true",
                            help="Faqat nechta o'zgarishini ko'rsatadi, yozmaydi.")

    def handle(self, *args, **opt):
        import openpyxl
        from pathlib import Path

        openings = Sale.objects.filter(is_opening=True)
        if not openings.exists():
            self.stdout.write("Ochilish qarzi yo'q — o'tkazib yuborildi.")
            return
        if not Path(opt["file"]).exists():
            self.stdout.write(self.style.WARNING(
                f"Fayl topilmadi ({opt['file']}) — o'tkazib yuborildi."))
            return

        wb = openpyxl.load_workbook(opt["file"], read_only=True, data_only=True)
        if "АКТ СВЕРКА" not in wb.sheetnames:
            self.stdout.write(self.style.WARNING("'АКТ СВЕРКА' varag'i yo'q — o'tkazib yuborildi."))
            return
        omap = oldi_map(wb["АКТ СВЕРКА"], datetime.date.today())
        days = opt["days"]

        updated = from_oldi = from_fallback = 0
        with transaction.atomic():
            for sale in openings.select_related("client"):
                d = omap.get(sale.client.name.upper())
                if d is None:
                    d = FALLBACK_OLDI
                    tally_fallback = True
                else:
                    tally_fallback = False
                deadline = d + datetime.timedelta(days=days)
                if sale.date != d or sale.debt_deadline != deadline:
                    sale.date = d
                    sale.debt_deadline = deadline
                    if not opt["dry_run"]:
                        sale.save(update_fields=["date", "debt_deadline"])
                    updated += 1
                    if tally_fallback:
                        from_fallback += 1
                    else:
                        from_oldi += 1
            if opt["dry_run"]:
                transaction.set_rollback(True)

        head = "DRY-RUN" if opt["dry_run"] else "Bajarildi"
        self.stdout.write(self.style.SUCCESS(
            f"{head}: {updated} ta ochilish qarzi o'tkazildi "
            f"(ОЛДИ'dan: {from_oldi}, fallback: {from_fallback})."))
