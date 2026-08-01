"""One-shot, VERSIONED setup: bring an environment to the state we built locally.

Two jobs, both idempotent:

  1. PAYROLL — seeds the four salaried staff (Xodimlar). They are ordinary data with
     no natural home in a migration, and they exist only where someone typed them in,
     so a deploy needs a way to put them on a fresh environment.

  2. OWNERSHIP — hands every crm_* record to the seller identified by SELLER_EMAIL.
     Everything the firm sells runs through one seller today; anything filed under
     another account (a payment the admin keyed in, say) lands in the WRONG till and
     quietly skews that seller's kassa and production debt. This sweeps such rows back.

Runs ONCE per VERSION — an AuditLog marker records it, exactly like `golive_load`, so
leaving it in a deploy pipeline is safe: a same-version rerun is a no-op. Bump VERSION
(or pass --force) to run it again.

User accounts are never created, renamed or given passwords: the seller must already
exist with that e-mail, otherwise the command refuses and changes nothing.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from crm.models import (
    AuditLog,
    Client,
    Employee,
    Expense,
    Payment,
    ProductionReceipt,
    ProductionRemittance,
    ProfitPayout,
    Return,
    Sale,
    StockEntry,
)

SELLER_EMAIL = "komola@test.com"

# Bump to run again on the next deploy. Unchanged -> no-op after the first success.
VERSION = "2026-08-01-payroll-owner-1"
MARKER_TYPE = "APPLY_LOCAL_STATE"

# Monthly wages, as agreed. Matched on name, so re-running only corrects the figure.
PAYROLL = [
    ("Косимов Рахматжон", Decimal("2000000")),
    ("Мансуров Шерзод", Decimal("8000000")),
    ("Курбонов Саидкарим", Decimal("6000000")),
    ("Тошхужайува Комола", Decimal("26000000")),
]

# (model, field) pairs that point at whoever owns / entered the record.
OWNED = [
    (Sale, "sales_rep"),
    (Client, "owner"),
    (Payment, "created_by"),
    (Expense, "created_by"),
    (Return, "created_by"),
    (StockEntry, "created_by"),
    (ProductionRemittance, "seller"),
    (ProductionRemittance, "created_by"),
    (ProfitPayout, "seller"),
    (ProfitPayout, "created_by"),
    (ProductionReceipt, "seller"),
    (ProductionReceipt, "created_by"),
]


class Command(BaseCommand):
    help = (
        "Bir martalik: xodimlarni kiritadi va barcha yozuvlarni "
        f"'{SELLER_EMAIL}' sotuvchisiga biriktiradi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Nima o'zgarishini ko'rsatadi, hech narsa saqlamaydi.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Shu versiya allaqachon qo'llangan bo'lsa ham qayta bajaradi.",
        )

    def handle(self, *args, **opt):
        seller = User.objects.filter(email__iexact=SELLER_EMAIL).first()
        if seller is None:
            self.stdout.write(self.style.ERROR(
                f"'{SELLER_EMAIL}' egasi topilmadi — hech narsa o'zgartirilmadi."
            ))
            return

        summary = f"apply_local_state v{VERSION}"
        if not opt["force"] and AuditLog.objects.filter(
            target_type=MARKER_TYPE, summary=summary
        ).exists():
            self.stdout.write(f"v{VERSION} allaqachon qo'llangan — o'tkazib yuborildi.")
            return

        self.stdout.write(f"Sotuvchi: {seller} ({SELLER_EMAIL})")
        with transaction.atomic():
            added, fixed = self._payroll()
            moved = self._ownership(seller)

            self.stdout.write("")
            self.stdout.write(f"  xodim qo'shildi   : {added}")
            self.stdout.write(f"  oyligi to'g'rilandi: {fixed}")
            self.stdout.write(f"  yozuv ko'chirildi : {moved}")

            if opt["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    "\nDRY RUN — hech narsa saqlanmadi."))
                return
            AuditLog.record(seller, AuditLog.Action.UPDATE, MARKER_TYPE, None, summary)

        self.stdout.write(self.style.SUCCESS(f"\nv{VERSION} qo'llandi."))

    def _payroll(self):
        added = fixed = 0
        for name, salary in PAYROLL:
            employee, created = Employee.objects.get_or_create(
                name=name, defaults={"salary": salary}
            )
            if created:
                added += 1
                self.stdout.write(f"  + xodim: {name} — {salary:,.0f} so'm")
            elif employee.salary != salary:
                self.stdout.write(
                    f"  ~ oylik: {name} {employee.salary:,.0f} -> {salary:,.0f}"
                )
                employee.salary = salary
                employee.save(update_fields=["salary"])
                fixed += 1
        return added, fixed

    def _ownership(self, seller):
        moved = 0
        for model, field in OWNED:
            stray = model.objects.exclude(**{field: seller})
            n = stray.count()
            if not n:
                continue
            self.stdout.write(
                f"  ~ {model.__name__}.{field}: {n} ta -> {seller}"
            )
            stray.update(**{field: seller})
            moved += n
        return moved
