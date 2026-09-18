"""КАРЗДОРЛИК ro'yxatidagi qarzdorlarni sotuvchiga boshlang'ich qarz qilib kiritadi.

Sotuvchining daftaridan olingan ikki ustunli varaq: МИЖОЗ | ОСТАТКА (manfiy = mijoz
qarzdor). Har bir qator — CRM'gacha qolib ketgan eski qarz: qaysi tovar olingani
noma'lum, shuning uchun u boshlang'ich qarz bo'lib yoziladi (is_opening Sale, qatorsiz)
— savdo, foyda va sotilgan kg hisobotlariga tegmaydi, faqat qarz bo'lib ko'rinadi.
Xuddi ilovadagi "Qarzdor mijoz kiritish" oynasi yozadigan narsa.

**Faqat YANGI mijozlar kiritiladi.** Sotuvchida allaqachon bor mijozning qarzi
ro'yxatdagidan farq qilsa ham, buyruq unga tegmaydi — faqat farqni ko'rsatadi. Sababi:
bor mijozning qarzi ortida haqiqiy cheklar va to'lovlar turadi, ularning ustiga daftar
summasini yozish qarzni ikki karra qilib yuboradi. Bor mijozni tuzatish — ilovadagi
"Boshlang'ich qarz" oynasining ishi.

Shuning uchun buyruqni qayta ishlatish xavfsiz: ikkinchi marta ishlatilganda bir ham
yangi mijoz qolmaydi va hech narsa yozilmaydi.

    python manage.py import_karzdorlik --seller umida --dry-run
    python manage.py import_karzdorlik --seller umida
"""

import datetime
import os
import re
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from crm.models import AuditLog, Client, Sale

from .import_opening_debts import format_phone, split_name_phone

# Daftarda raqam ismga yopishib ham yoziladi ("ШАХЗОД ЧИЛОНЗОР94 007 35 33"), shuning
# uchun bo'sh joy talab qilmaydigan ikkinchi urinish: oxiridagi raqamlar qatori, oldida
# raqam bo'lmagan belgi turgan joydan boshlanadi. "ЮНУСОБОД 15 КВ 99 828 42 00" kabi
# nomdagi raqam esa qatorning oxirida turmagani uchun ilinmaydi.
_GLUED_PHONE = re.compile(r"(?<=\D)(\d[\d\s]*\d)\s*$")

DEFAULT_FILE = os.path.join(
    os.path.expanduser("~"), "Downloads", "Telegram Desktop", "КАРЗДОРЛИК.xlsx"
)
# Sotuvchining boshqa ochilish qarzlari shu kunga yozilgan; bir partiya bo'lib tursin.
DEFAULT_DATE = "2026-08-08"
DEFAULT_DAYS = 14


def read_rows(path):
    """[(xom nom, summa)] — manfiy ОСТАТКА qarz deb olinadi, musbati chetda qoladi."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for raw_name, *rest in ws.iter_rows(values_only=True):
        name = " ".join(str(raw_name).split()) if raw_name else ""
        value = rest[0] if rest else None
        if not name or not isinstance(value, (int, float)):
            continue
        rows.append((name, Decimal(str(value))))
    return rows


def parse_name_phone(raw):
    """(nom, telefon) — avval umumiy qoida, u raqamni topmasa yopishgan raqam qoidasi."""
    name, phone = split_name_phone(raw)
    if phone:
        return name, phone
    # Umumiy qoida bo'sh joyni talab qiladi, shuning uchun yopishgan raqamning faqat
    # oxirgi bo'lagini kesib, nomda "…ЧИЛОНЗОР94" qoldirib ketadi — xom qatorning
    # o'zidan qaytadan qidiriladi.
    match = _GLUED_PHONE.search(raw.strip())
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        head = raw[: match.start()].strip()
        if 7 <= len(digits) <= 12 and head:
            return head, format_phone(match.group(1))
    return name, ""


def find_client(seller, name, phone):
    """Sotuvchining shu nomli (yoki shu raqamli) mijozi — topilmasa None."""
    match = Client.find_duplicate(seller, name)
    if match:
        return match
    digits = "".join(ch for ch in phone if ch.isdigit())[-9:]
    if len(digits) == 9:
        for client in Client.objects.filter(owner=seller).exclude(phone=""):
            if "".join(ch for ch in client.phone if ch.isdigit()).endswith(digits):
                return client
    return None


class Command(BaseCommand):
    help = "КАРЗДОРЛИК varaqidagi yangi qarzdorlarni boshlang'ich qarz qilib kiritadi."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=DEFAULT_FILE, help="КАРЗДОРЛИК .xlsx yo'li")
        parser.add_argument("--seller", required=True, help="Sotuvchi username yoki email")
        parser.add_argument("--date", default=DEFAULT_DATE, help="Qarz sanasi (YYYY-MM-DD)")
        parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Qarz muddati (kun)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Hech narsa yozmaydi, faqat nima bo'lishini ko'rsatadi.")

    def handle(self, *args, **opt):
        seller = (
            User.objects.filter(username=opt["seller"]).first()
            or User.objects.filter(email__iexact=opt["seller"]).first()
        )
        if seller is None:
            raise CommandError(f"Sotuvchi topilmadi: {opt['seller']}")

        debt_date = datetime.date.fromisoformat(opt["date"])
        deadline = debt_date + datetime.timedelta(days=opt["days"])
        dry = opt["dry_run"]

        created, existing, skipped = [], [], []
        with transaction.atomic():
            for raw, value in read_rows(opt["file"]):
                if value >= 0:  # avans yoki nol — bu buyruqning ishi emas
                    skipped.append((raw, value))
                    continue
                amount = -value
                name, phone = parse_name_phone(raw)
                client = find_client(seller, name, phone)
                if client is not None:
                    debt = sum(s.debt_remaining for s in Sale.objects.filter(client=client))
                    existing.append((client, amount, debt))
                    continue
                if not dry:
                    client = Client.objects.create(name=name, phone=phone, owner=seller)
                    sale = Sale.objects.create(
                        client=client, sales_rep=seller, date=debt_date,
                        debt_deadline=deadline, debt_term_days=opt["days"],
                        is_opening=True, opening_amount=amount,
                    )
                    AuditLog.record(
                        None, AuditLog.Action.CREATE, "Sotuv", sale.pk,
                        f"Qarzdorlik ro'yxatidan kiritildi: {name} — "
                        f"boshlang'ich qarz {amount:,.0f} so'm ({seller})",
                    )
                created.append((name, phone, amount))

            if dry:
                transaction.set_rollback(True)

        write = self.stdout.write
        write("")
        write(f"Sotuvchi: {seller} ({seller.email})   sana: {debt_date}, muddat: {opt['days']} kun")
        write("")
        write(f"YANGI QARZDORLAR — {len(created)} ta{' (dry-run: yozilmadi)' if dry else ''}")
        for name, phone, amount in created:
            write(f"  + {name:42} {phone:17} {amount:>14,.0f}")
        write(f"  jami: {sum(a for _, _, a in created):,.0f} so'm")
        write("")
        write(f"BOR MIJOZLAR — {len(existing)} ta (tegilmadi)")
        for client, amount, debt in existing:
            mark = "=" if amount == debt else "!"
            write(f"  {mark} {client.name:42} ro'yxatda {amount:>14,.0f} | CRM {debt:>14,.0f}")
        if skipped:
            write("")
            write(f"CHETDA QOLDI (manfiy emas) — {len(skipped)} ta")
            for raw, value in skipped:
                write(f"  ? {raw:42} {value:>14,.0f}")
