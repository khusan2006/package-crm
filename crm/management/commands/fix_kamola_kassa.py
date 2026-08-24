"""One-shot, VERSIONED correction of Kamola's till for 23.07 – 05.08.2026.

Twelve days ran negative in the kassa. The money was never lost — three of the four
causes are a date or a typo, and the fourth is cash the seller already held on the day
the CRM went live. Each step below is checked against the three paper/Excel books
(ХОЗМАГЛАР ТЎЛОВ, РАСХОД.xlsx, topshiruv ro'yxati) before it is written.

  1. Boshlang'ich naqd qoldiq (1 980 000, 23.07). The seller had that cash in hand when
     the CRM started from zero, and handed it in on 26.07 together with that day's
     takings — so the CRM saw her hand over more than she had collected. Booked as a
     negative remittance (production handing cash back) plus an equal-and-opposite
     ProductionAdjustment, so the till rises and the production debt does NOT move.

  2. Topshiruv #16 (02.08): 8 231 850 -> 10 211 850. The form refused the real figure
     ("kassada yetarli pul yo'q") because of cause 1, so a smaller number was typed.
     10 211 850 is what the audit log shows the row was created with, and it is exactly
     that day's kirim - rasxod. Stage 4 later puts it on the paper book's 10 213 000.

  3+4. ШОКИР АЛГОРИТИМ. Excel says he paid 3 050 000 on 24.07 and 1 065 000 on 20.08.
     The CRM has 3 030 400 (dated 10.08) and 1 084 600 — same total, wrong dates, and
     19 600 sitting on the wrong row because his opening debt was 19 600 smaller than
     what he actually handed over. So: move #4226 to 23.07 (the CRM books 24.07 takings
     on 23.07), record the 19 600 surplus as an advance deposit on the same day, spend
     that advance on the 10.08 sale, and put #4227 back to 1 065 000. No sale price is
     touched and the client still ends fully settled with zero advance.

  5+6. Курбонов Саидкарим's wage. Entered as one 2 788 000 expense dated 31.07; the
     money actually left the till as 2 000 000 on 17.08 and 788 000 on 18.08, which is
     what РАСХОД.xlsx and the man himself say. The wage was for June, so his August pay
     would otherwise be docked for it — hence the opening balance.

A second, separately-versioned stage cleans up 21.08, which is not a negative day but is
still wrong in the same way:

  7. Two fabricated "СУВ" expenses of 39 200 (written on 23.08, dated 21.08) are deleted.
     РАСХОД.xlsx has no such spending; they exist only because the till showed money the
     books could not explain.

  8. Topshiruv #35 (21.08): 13 962 208 -> 14 001 408. The form refused the real figure,
     so a smaller one was typed — the same reflex as cause 2.

Two later stages take their figures from the books rather than from arithmetic — where a
book states what production was handed, the book wins:

  9. Production's OWN book gives 48 340 056 for 19–20.08 and 41 177 296 for 21.08; both
     handovers are set to those. It has NO entry for the 14 001 408, which reads as
     "never handed over" — but that is a claim about missing money, so the row is left
     alone until the owner confirms, and 21.08 shows −22 033 in the meantime.

 10. Topshiruv #16 (02.08) goes to the paper book's 10 213 000, and the opening cash
     rises with it to 1 981 150 — see the constants for why they move together.

Each stage runs ONCE per its own version (an AuditLog marker records it), so this command
is safe to run again: an applied stage is a no-op. Every step verifies the row is still
in the expected state first: the seller keeps editing these same records, so a silent
overwrite would be worse than a refusal. If anything has moved, nothing is written and
the mismatch is printed.
"""

from datetime import date
from datetime import date as dt_date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from accounts.models import User
from crm.models import (
    PAYMENT_NET,
    AuditLog,
    Client,
    Employee,
    Expense,
    Payment,
    ProductionAdjustment,
    ProductionRemittance,
    Sale,
    seller_cash_on_hand,
    seller_production_debt,
)

SELLER_EMAIL = "komola@test.com"
MARKER_TYPE = "KASSA_FIX"
FIX_VERSION = "2026-08-23-minus-1"          # 1-bosqich: minus kunlar
FIX_VERSION_2108 = "2026-08-23-2108-1"      # 2-bosqich: 21.08 tozalash
FIX_VERSION_BOOK = "2026-08-23-prodbook-1"  # 3-bosqich: ishlab chiqarish daftari
FIX_VERSION_0208 = "2026-08-23-daftar0208-1"  # 4-bosqich: 02.08 daftar raqami

# 02.08 topshirug'i daftardagi raqamga keltiriladi. Boshlang'ich naqd qoldiq —
# yagona kuzatilmagan, hisoblab topilgan kattalik: topshiruv 1 150 ga ko'p bo'lsa,
# demak u kunni shuncha ko'p pul bilan boshlagan. Ikkisi birga o'zgaradi, aks holda
# 02.08 dan 05.08 gacha kassa −1 150 ga tushadi.
REMITTANCE_16_BOOK = (16, Decimal("10211850"), Decimal("10213000"))
OPENING_CASH_BOOK = Decimal("1981150")

# Ishlab chiqarishning O'Z daftaridagi raqamlar (2026-08-23 da tasdiqlangan). Ular
# pulni qabul qilgan tomon, shuning uchun topshiruv summasida oxirgi so'z ularniki.
PROD_BOOK = (
    (32, dt_date(2026, 8, 20), Decimal("48299313"), Decimal("48340056")),
    (34, dt_date(2026, 8, 21), Decimal("41156806"), Decimal("41177296")),
)

# 23.08 da yozilgan, РАСХОД daftarida yo'q — kassani nolga tushirish uchun kiritilgan.
FAKE_EXPENSES = (137, 138)
FAKE_EXPENSE_AMOUNT = Decimal("39200")
FAKE_EXPENSE_DATE = date(2026, 8, 21)
REMITTANCE_35 = (35, Decimal("13962208"), Decimal("14001408"))

OPENING_CASH = Decimal("1980000")       # 23.07 da qo'lda bo'lgan naqd
OPENING_DATE = date(2026, 7, 23)

REMITTANCE_16 = (16, Decimal("8231850"), Decimal("10211850"))

SHOKIR_OPENING_PAY = 4226               # ochilish qarzini yopgan to'lov
SHOKIR_OPENING_AMOUNT = Decimal("3030400")
SHOKIR_OPENING_FROM = date(2026, 8, 10)
SHOKIR_OPENING_TO = date(2026, 7, 23)
SHOKIR_SURPLUS = Decimal("19600")       # qarzidan ortiq to'lagani -> avans
SHOKIR_SALE_PAY = 4227
SHOKIR_SALE_PAY_FROM = Decimal("1084600")
SHOKIR_SALE_PAY_TO = Decimal("1065000")
SHOKIR_SALE = 3612
SHOKIR_SALE_DATE = date(2026, 8, 10)

SALARY_EXPENSE = 128
SALARY_TOTAL = Decimal("2788000")
SALARY_OLD_DATE = date(2026, 7, 31)
SALARY_FIRST = (date(2026, 8, 17), Decimal("2000000"))
SALARY_SECOND = (date(2026, 8, 18), Decimal("788000"))
SALARY_EMPLOYEE = 7                     # Курбонов Саидкарим


def _money(value):
    return f"{Decimal(value):,.0f}".replace(",", " ")


class Command(BaseCommand):
    help = "Kamola kassasidagi 23.07–05.08 minus kunlarini tuzatadi (bir marta)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Shu versiya allaqachon qo'llangan bo'lsa ham qayta ishga tushirish.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Hech narsa yozmasdan, faqat oldingi/keyingi holatni ko'rsatish.",
        )

    # ------------------------------------------------------------------ helpers

    def _daily_balance(self, seller):
        """Kun oxiridagi naqd qoldiq — kassa sahifasi ko'rsatadigan raqam."""
        inc = dict(
            Payment.objects.filter(created_by=seller).till_income()
            .values_list("date").annotate(v=Sum(PAYMENT_NET))
        )
        out = dict(
            Payment.objects.filter(created_by=seller).till_outflow()
            .values_list("date").annotate(v=Sum("amount"))
        )
        exp = dict(
            Expense.objects.filter(created_by=seller)
            .values_list("date").annotate(v=Sum("amount"))
        )
        rem = dict(
            ProductionRemittance.objects.filter(seller=seller)
            .values_list("date").annotate(v=Sum("amount"))
        )
        rows, running = [], Decimal("0")
        for day in sorted(set(inc) | set(out) | set(exp) | set(rem)):
            running += (
                inc.get(day, 0) - out.get(day, 0) - exp.get(day, 0) - rem.get(day, 0)
            )
            rows.append((day, running))
        return rows

    def _report(self, seller, title):
        # Bank komissiyasi 4% dan tiyinlar chiqadi, topshiruv esa butun so'mda
        # yoziladi — shuning uchun bir tiyinlik qoldiq minus hisoblanmaydi.
        rows = self._daily_balance(seller)
        minus = [(d, b) for d, b in rows if b < Decimal("-1")]
        self.stdout.write(f"\n{title}")
        self.stdout.write(f"  minus kunlar: {len(minus)}")
        for day, bal in minus:
            self.stdout.write(f"    {day:%d.%m}  {_money(bal):>16}")
        if rows:
            self.stdout.write(f"  oxirgi qoldiq ({rows[-1][0]:%d.%m}): {_money(rows[-1][1])}")
        self.stdout.write(f"  kassadagi pul: {_money(seller_cash_on_hand(seller))}")
        self.stdout.write(f"  ishlab chiqarish qarzi: {_money(seller_production_debt(seller))}")
        return len(minus)

    def _expect(self, problems, label, actual, expected):
        if actual != expected:
            problems.append(f"{label}: kutilgan {expected!r}, bazada {actual!r}")

    # ------------------------------------------------------- 2-bosqich: 21.08

    def _stage_2108(self, seller, opt):
        """Soxta «СУВ» chiqimlarini o'chiradi va 21.08 topshirug'ini tiklaydi.

        Minus kunlarga tegmaydi, shuning uchun alohida versiyalangan: 1-bosqich
        qo'llangan bo'lsa ham bu mustaqil ishlaydi."""
        summary = f"21.08 tozalash v{FIX_VERSION_2108}"
        if not opt["force"] and AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists():
            self.stdout.write(
                f"\n2-bosqich (v{FIX_VERSION_2108}) allaqachon qo'llangan — "
                "o'tkazib yuborildi."
            )
            return

        problems = []
        fakes = list(
            Expense.objects.filter(pk__in=FAKE_EXPENSES, created_by=seller)
        )
        if len(fakes) != len(FAKE_EXPENSES):
            problems.append(
                f"Soxta chiqimlar topilmadi: {FAKE_EXPENSES} dan {len(fakes)} tasi bor"
            )
        for row in fakes:
            self._expect(problems, f"Chiqim #{row.pk} summasi",
                         row.amount, FAKE_EXPENSE_AMOUNT)
            self._expect(problems, f"Chiqim #{row.pk} sanasi",
                         row.date, FAKE_EXPENSE_DATE)

        rem_pk, rem_old, rem_new = REMITTANCE_35
        remittance = ProductionRemittance.objects.filter(
            pk=rem_pk, seller=seller
        ).first()
        if remittance is None:
            problems.append(f"Topshiruv #{rem_pk} topilmadi")
        else:
            self._expect(problems, f"Topshiruv #{rem_pk} summasi",
                         remittance.amount, rem_old)

        if problems:
            self.stdout.write(self.style.ERROR(
                "\n2-bosqich: yozuvlar kutilgan holatda emas — tegilmadi:"))
            for line in problems:
                self.stdout.write(f"  - {line}")
            raise CommandError("2-bosqich to'xtatildi.")

        if opt["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "\n2-bosqich --dry-run: tekshirildi, yozilmadi."))
            return

        with transaction.atomic():
            for row in fakes:
                AuditLog.record(
                    seller, AuditLog.Action.DELETE, "Chiqim", row.pk,
                    f"Soxta «СУВ» chiqimi o'chirildi — {_money(row.amount)} so'm "
                    f"({row.date:%d.%m})",
                )
                row.delete()

            remittance.amount = rem_new
            remittance.save(update_fields=["amount"])
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "Topshiruv", remittance.pk,
                f"Topshiruv {_money(rem_old)} -> {_money(rem_new)} so'm (21.08)",
            )
            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        self.stdout.write(self.style.SUCCESS(
            f"\n2-bosqich qo'llandi: {len(fakes)} ta soxta chiqim o'chirildi "
            f"({_money(FAKE_EXPENSE_AMOUNT * len(fakes))} so'm), "
            f"topshiruv #{rem_pk} -> {_money(rem_new)} so'm."))

    # ------------------------------ 3-bosqich: ishlab chiqarish daftari bo'yicha

    def _stage_prod_book(self, seller, opt):
        """Aligns the two handovers whose figure production's own book states.

        Production counted the cash, so where its book and the CRM disagree the book
        wins. The 14 001 408 handover is deliberately NOT touched here: it is absent
        from production's book, which reads as "never handed over" — but that is a
        claim about missing money, so it waits for the owner's word rather than being
        silently deleted."""
        summary = f"ishlab chiqarish daftari bo'yicha tuzatish v{FIX_VERSION_BOOK}"
        if not opt["force"] and AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists():
            self.stdout.write(
                f"\n3-bosqich (v{FIX_VERSION_BOOK}) allaqachon qo'llangan — "
                "o'tkazib yuborildi."
            )
            return

        problems, rows = [], []
        for pk, on_date, old, new in PROD_BOOK:
            row = ProductionRemittance.objects.filter(pk=pk, seller=seller).first()
            if row is None:
                problems.append(f"Topshiruv #{pk} topilmadi")
                continue
            self._expect(problems, f"Topshiruv #{pk} sanasi", row.date, on_date)
            self._expect(problems, f"Topshiruv #{pk} summasi", row.amount, old)
            rows.append((row, old, new))

        if problems:
            self.stdout.write(self.style.ERROR(
                "\n3-bosqich: yozuvlar kutilgan holatda emas — tegilmadi:"))
            for line in problems:
                self.stdout.write(f"  - {line}")
            raise CommandError("3-bosqich to'xtatildi.")

        if opt["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "\n3-bosqich --dry-run: tekshirildi, yozilmadi."))
            return

        with transaction.atomic():
            for row, old, new in rows:
                row.amount = new
                row.save(update_fields=["amount"])
                AuditLog.record(
                    seller, AuditLog.Action.UPDATE, "Topshiruv", row.pk,
                    f"Ishlab chiqarish daftari bo'yicha: {_money(old)} -> "
                    f"{_money(new)} so'm ({row.date:%d.%m})",
                )
            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        total = sum(new - old for _, old, new in rows)
        self.stdout.write(self.style.SUCCESS(
            f"\n3-bosqich qo'llandi: {len(rows)} ta topshiruv daftar raqamiga "
            f"keltirildi (+{_money(total)} so'm)."))

    # ------------------------------------- 4-bosqich: 02.08 daftar raqami bo'yicha

    def _stage_0208(self, seller, opt):
        """Puts the 02.08 handover on the figure the paper book states.

        The book is what production was handed; the seller keyed a smaller number
        because the till would not carry the real one. Raising it by 1 150 also raises
        the opening cash by 1 150 — that figure was never observed, it was solved for,
        and a bigger handover on 02.08 means she started the month with that much more
        in hand. Changed apart, the till runs 1 150 short from 02.08 to 05.08."""
        summary = f"02.08 daftar raqami v{FIX_VERSION_0208}"
        if not opt["force"] and AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists():
            self.stdout.write(
                f"\n4-bosqich (v{FIX_VERSION_0208}) allaqachon qo'llangan — "
                "o'tkazib yuborildi."
            )
            return

        problems = []
        rem_pk, rem_old, rem_new = REMITTANCE_16_BOOK
        remittance = ProductionRemittance.objects.filter(
            pk=rem_pk, seller=seller
        ).first()
        if remittance is None:
            problems.append(f"Topshiruv #{rem_pk} topilmadi")
        else:
            self._expect(problems, f"Topshiruv #{rem_pk} summasi",
                         remittance.amount, rem_old)

        # 1-bosqich yaratgan juftlik — pk emas, mazmuni bo'yicha topiladi.
        opening = ProductionRemittance.objects.filter(
            seller=seller, date=OPENING_DATE, amount=-OPENING_CASH
        ).first()
        adjustment = ProductionAdjustment.objects.filter(
            seller=seller, date=OPENING_DATE, amount=-OPENING_CASH
        ).first()
        if opening is None:
            problems.append(
                f"Boshlang'ich naqd qatori topilmadi ({_money(-OPENING_CASH)})")
        if adjustment is None:
            problems.append(
                f"Boshlang'ich naqd tuzatishi topilmadi ({_money(-OPENING_CASH)})")

        if problems:
            self.stdout.write(self.style.ERROR(
                "\n4-bosqich: yozuvlar kutilgan holatda emas — tegilmadi:"))
            for line in problems:
                self.stdout.write(f"  - {line}")
            raise CommandError("4-bosqich to'xtatildi.")

        if opt["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "\n4-bosqich --dry-run: tekshirildi, yozilmadi."))
            return

        with transaction.atomic():
            remittance.amount = rem_new
            remittance.save(update_fields=["amount"])
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "Topshiruv", remittance.pk,
                f"Daftar raqami bo'yicha: {_money(rem_old)} -> {_money(rem_new)} "
                f"so'm (02.08)",
            )
            for row, label in ((opening, "Topshiruv"), (adjustment, "Tuzatish")):
                row.amount = -OPENING_CASH_BOOK
                row.save(update_fields=["amount"])
                AuditLog.record(
                    seller, AuditLog.Action.UPDATE, label, row.pk,
                    f"Boshlang'ich naqd qoldiq {_money(OPENING_CASH)} -> "
                    f"{_money(OPENING_CASH_BOOK)} so'm (23.07)",
                )
            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        self.stdout.write(self.style.SUCCESS(
            f"\n4-bosqich qo'llandi: 02.08 topshirug'i {_money(rem_new)} so'm, "
            f"boshlang'ich naqd {_money(OPENING_CASH_BOOK)} so'm."))

    # ------------------------------------------------------------------- handle

    def handle(self, *args, **opt):
        try:
            seller = User.objects.get(email=SELLER_EMAIL)
        except User.DoesNotExist:
            raise CommandError(f"Sotuvchi topilmadi: {SELLER_EMAIL}")

        summary = f"kassa minus tuzatishi v{FIX_VERSION}"
        do_minus = opt["force"] or not AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists()

        before = self._report(seller, "OLDIN:")
        if not do_minus:
            self.stdout.write(
                f"\n1-bosqich (v{FIX_VERSION}) allaqachon qo'llangan — o'tkazib yuborildi."
            )
            self._stage_2108(seller, opt)
            self._stage_prod_book(seller, opt)
            self._stage_0208(seller, opt)
            self._report(seller, "KEYIN:")
            return

        # --- har bir yozuv kutilgan holatdami? --------------------------------
        problems = []

        rem_pk, rem_old, rem_new = REMITTANCE_16
        remittance = ProductionRemittance.objects.filter(pk=rem_pk, seller=seller).first()
        if remittance is None:
            problems.append(f"Topshiruv #{rem_pk} topilmadi")
        else:
            self._expect(problems, f"Topshiruv #{rem_pk} summasi", remittance.amount, rem_old)

        opening_pay = Payment.objects.filter(pk=SHOKIR_OPENING_PAY).first()
        if opening_pay is None:
            problems.append(f"To'lov #{SHOKIR_OPENING_PAY} topilmadi")
        else:
            self._expect(problems, f"To'lov #{SHOKIR_OPENING_PAY} sanasi",
                         opening_pay.date, SHOKIR_OPENING_FROM)
            self._expect(problems, f"To'lov #{SHOKIR_OPENING_PAY} summasi",
                         opening_pay.amount, SHOKIR_OPENING_AMOUNT)

        sale_pay = Payment.objects.filter(pk=SHOKIR_SALE_PAY).first()
        if sale_pay is None:
            problems.append(f"To'lov #{SHOKIR_SALE_PAY} topilmadi")
        else:
            self._expect(problems, f"To'lov #{SHOKIR_SALE_PAY} summasi",
                         sale_pay.amount, SHOKIR_SALE_PAY_FROM)

        sale = Sale.objects.filter(pk=SHOKIR_SALE).first()
        if sale is None:
            problems.append(f"Sotuv #{SHOKIR_SALE} topilmadi")

        salary = Expense.objects.filter(pk=SALARY_EXPENSE, created_by=seller).first()
        if salary is None:
            problems.append(f"Chiqim #{SALARY_EXPENSE} topilmadi")
        else:
            self._expect(problems, f"Chiqim #{SALARY_EXPENSE} sanasi",
                         salary.date, SALARY_OLD_DATE)
            self._expect(problems, f"Chiqim #{SALARY_EXPENSE} summasi",
                         salary.amount, SALARY_TOTAL)

        employee = Employee.objects.filter(pk=SALARY_EMPLOYEE).first()
        if employee is None:
            problems.append(f"Xodim #{SALARY_EMPLOYEE} topilmadi")

        if problems:
            self.stdout.write(self.style.ERROR(
                "\nYozuvlar kutilgan holatda emas — hech narsa o'zgartirilmadi:"))
            for line in problems:
                self.stdout.write(f"  - {line}")
            raise CommandError("Tuzatish to'xtatildi.")

        if opt["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "\n--dry-run: yozuvlar tekshirildi, hech narsa yozilmadi."))
            return

        # --- tuzatish ---------------------------------------------------------
        with transaction.atomic():
            # 1. Boshlang'ich naqd qoldiq: kassa oshadi, qarz o'zgarmaydi.
            refund = ProductionRemittance.objects.create(
                date=OPENING_DATE, seller=seller, amount=-OPENING_CASH,
                created_by=seller,
                note="Boshlang'ich naqd qoldiq — CRM ishga tushganda qo'lda bo'lgan pul",
            )
            ProductionAdjustment.objects.create(
                date=OPENING_DATE, seller=seller, amount=-OPENING_CASH,
                reason=ProductionAdjustment.Reason.OTHER, created_by=seller,
                note="Boshlang'ich naqd qoldiq: ishlab chiqarish qarzi o'zgarmasligi uchun",
            )
            AuditLog.record(
                seller, AuditLog.Action.CREATE, "Topshiruv", refund.pk,
                f"Boshlang'ich naqd qoldiq {_money(OPENING_CASH)} so'm ({OPENING_DATE:%d.%m})",
            )

            # 2. 02.08 topshirug'i to'liq summaga qaytariladi.
            remittance.amount = rem_new
            remittance.save(update_fields=["amount"])
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "Topshiruv", remittance.pk,
                f"Topshiruv {_money(rem_old)} -> {_money(rem_new)} so'm (02.08)",
            )

            # 3. ШОКИР ochilish to'lovi o'z kuniga qaytadi.
            opening_pay.date = SHOKIR_OPENING_TO
            opening_pay.save(update_fields=["date"])
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "To'lov", opening_pay.pk,
                f"ШОКИР to'lovi sanasi {SHOKIR_OPENING_FROM:%d.%m} -> {SHOKIR_OPENING_TO:%d.%m}",
            )

            # 4. Qarzidan ortiq to'lagani avans bo'lib kiradi va o'sha sotuvga sarflanadi.
            client = Client.objects.get(pk=opening_pay.sale.client_id)
            deposit = Payment.objects.create(
                date=SHOKIR_OPENING_TO, client=client, amount=SHOKIR_SURPLUS,
                kind=Payment.Kind.ADVANCE_IN, created_by=seller,
                note="Qarzidan ortiq to'langan qism — avansga",
            )
            Payment.objects.create(
                date=SHOKIR_SALE_DATE, client=client, sale=sale, amount=SHOKIR_SURPLUS,
                kind=Payment.Kind.ADVANCE_USED, created_by=seller,
                note="Avansdan yechildi",
            )
            sale_pay.amount = SHOKIR_SALE_PAY_TO
            sale_pay.save(update_fields=["amount"])
            AuditLog.record(
                seller, AuditLog.Action.CREATE, "To'lov", deposit.pk,
                f"ШОКИР avansi {_money(SHOKIR_SURPLUS)} so'm ({SHOKIR_OPENING_TO:%d.%m})",
            )
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "To'lov", sale_pay.pk,
                f"ШОКИР to'lovi {_money(SHOKIR_SALE_PAY_FROM)} -> "
                f"{_money(SHOKIR_SALE_PAY_TO)} so'm (20.08)",
            )

            # 5. Саидкарим oyligi haqiqiy berilgan kunlarga bo'linadi.
            first_date, first_amount = SALARY_FIRST
            second_date, second_amount = SALARY_SECOND
            salary.date = first_date
            salary.amount = first_amount
            salary.amount_original = first_amount
            salary.note = "Аванс Саидкарим (июн ойлиги)"
            salary.save(update_fields=["date", "amount", "amount_original", "note"])
            second = Expense.objects.create(
                date=second_date, amount=second_amount, amount_original=second_amount,
                category=salary.category, method=salary.method,
                employee=salary.employee, counts_against_salary=True,
                created_by=seller, note="Ойлик Саидкарим (июн ойлиги)",
            )
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "Chiqim", salary.pk,
                f"Саидкарим oyligi {SALARY_OLD_DATE:%d.%m} {_money(SALARY_TOTAL)} -> "
                f"{first_date:%d.%m} {_money(first_amount)}",
            )
            AuditLog.record(
                seller, AuditLog.Action.CREATE, "Chiqim", second.pk,
                f"Саидкарим oyligi {second_date:%d.%m} {_money(second_amount)} so'm",
            )

            # 6. Oylik iyunniki edi — avgust oyligidan ushlanib qolmasligi uchun.
            employee.opening_balance = SALARY_TOTAL
            employee.save(update_fields=["opening_balance"])
            AuditLog.record(
                seller, AuditLog.Action.UPDATE, "Xodim", employee.pk,
                f"{employee.name} boshlang'ich qoldiq 0 -> {_money(SALARY_TOTAL)} so'm",
            )

            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        self._stage_2108(seller, opt)
        self._stage_prod_book(seller, opt)
        self._stage_0208(seller, opt)
        after = self._report(seller, "KEYIN:")
        self.stdout.write("")
        if after == 0:
            self.stdout.write(self.style.SUCCESS(
                f"fix_kamola_kassa: v{FIX_VERSION} qo'llandi — "
                f"minus kunlar {before} -> 0."))
        else:
            self.stdout.write(self.style.WARNING(
                f"fix_kamola_kassa: v{FIX_VERSION} qo'llandi, lekin hali "
                f"{after} ta minus kun qoldi."))
