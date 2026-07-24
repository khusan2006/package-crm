"""Import the pre-CRM opening balances from the client's reconciled ОСТ КОЧА sheet.

Each row is a client and their net balance as of go-live:
  * negative  -> the client owes us  -> an opening-balance Sale (is_opening=True,
                 opening_amount=abs). No line items, so it never touches any
                 revenue/profit/sold-kg report — it shows up purely as a receivable
                 and is paid down by ordinary debt payments.
  * positive  -> we hold their money  -> an ADVANCE_IN payment (client advance credit).

Idempotent: a client that already has an opening Sale (or an imported advance) is
skipped, so re-running never doubles a balance. Run with --dry-run first to see the
totals without writing anything.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Payment, Sale

DEFAULT_FILE = r"C:\Users\User\Downloads\Telegram Desktop\ОСТ КОЧА.xlsx"
ADVANCE_NOTE = "Ochilish avansi (import)"

# Names to leave out of this run (decided per-client). Matched on a normalised
# substring so the trailing phone number / spacing doesn't matter.
SKIP_SUBSTRINGS = ["ФОМЛАЙН"]  # ОТАБЕК ФОМЛАЙН МАТРАС — confirmed with client later.


def _norm(s):
    return " ".join(str(s).split()) if s is not None else ""


class Command(BaseCommand):
    help = "ОСТ КОЧА faylidan eski qarz/avans qoldiqlarini import qiladi."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=DEFAULT_FILE, help="ОСТ КОЧА .xlsx yo'li")
        parser.add_argument("--seller", default="sotuvchi1",
                            help="Egaси/sotuvchi username (default: sotuvchi1)")
        parser.add_argument("--date", default=None,
                            help="Kesish sanasi ISO (YYYY-MM-DD). Default: bugun.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Hech narsa yozmaydi, faqat xulosani ko'rsatadi.")

    def handle(self, *args, **opt):
        import openpyxl

        try:
            seller = User.objects.get(username=opt["seller"])
        except User.DoesNotExist:
            raise CommandError(f"Sotuvchi topilmadi: {opt['seller']}")

        cutoff = timezone.localdate()
        if opt["date"]:
            from datetime import date
            cutoff = date.fromisoformat(opt["date"])

        wb = openpyxl.load_workbook(opt["file"], read_only=True, data_only=True)
        ws = wb.active

        rows = []
        for r in ws.iter_rows(values_only=True):
            name = _norm(r[0])
            val = r[1] if len(r) > 1 else None
            if not name or not isinstance(val, (int, float)):
                continue
            rows.append((name, Decimal(str(val))))

        dry = opt["dry_run"]
        created_debt = created_adv = skipped_existing = skipped_named = 0
        debt_total = adv_total = Decimal("0")

        # Wrap the whole run so a mid-way failure leaves the DB untouched.
        with transaction.atomic():
            for name, val in rows:
                if any(s in name.upper() for s in SKIP_SUBSTRINGS):
                    skipped_named += 1
                    self.stdout.write(f"  ⏭  chetda qoldirildi: {name}")
                    continue

                client = Client.find_duplicate(seller, name) or Client.objects.filter(
                    name__iexact=name
                ).first()
                if client is None and not dry:
                    client = Client.objects.create(name=name, owner=seller)

                if val < 0:  # client owes us -> opening debt
                    amount = -val
                    exists = client is not None and Sale.objects.filter(
                        client=client, is_opening=True
                    ).exists()
                    if exists:
                        skipped_existing += 1
                        continue
                    created_debt += 1
                    debt_total += amount
                    if not dry:
                        Sale.objects.create(
                            client=client, sales_rep=seller, date=cutoff,
                            is_opening=True, opening_amount=amount,
                        )
                elif val > 0:  # we hold their money -> advance credit
                    exists = client is not None and Payment.objects.filter(
                        client=client, kind=Payment.Kind.ADVANCE_IN, note=ADVANCE_NOTE
                    ).exists()
                    if exists:
                        skipped_existing += 1
                        continue
                    created_adv += 1
                    adv_total += val
                    if not dry:
                        Payment.objects.create(
                            client=client, created_by=seller, date=cutoff,
                            amount=val, kind=Payment.Kind.ADVANCE_IN, note=ADVANCE_NOTE,
                        )

            if dry:
                transaction.set_rollback(True)

        head = "DRY-RUN (hech narsa yozilmadi)" if dry else "IMPORT bajarildi"
        self.stdout.write(self.style.SUCCESS(f"\n=== {head} ==="))
        self.stdout.write(f"Kesish sanasi: {cutoff} | Sotuvchi: {seller.username}")
        self.stdout.write(f"Qarz (opening) yozuvlari: {created_debt} ta = {debt_total:,.0f} so'm")
        self.stdout.write(f"Avans yozuvlari:          {created_adv} ta = {adv_total:,.0f} so'm")
        self.stdout.write(f"Chetda qoldirilgan (nom): {skipped_named} ta")
        self.stdout.write(f"Allaqachon bor (o'tkazib): {skipped_existing} ta")
