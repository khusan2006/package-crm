"""Seed the payroll (Xodimlar) with the staff the business actually pays.

Idempotent — matches on name, so it is safe to re-run and safe to run on production
after it has run locally. An existing employee's wage is only touched when
--update-salary is passed, so a raise entered by hand in the UI is not silently
reverted by a later re-run.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Employee

# (name, monthly wage in so'm). Names are kept exactly as the owner wrote them.
STAFF = [
    ("Косимов Рахматжон", "2000000"),
    ("Мансуров Шерзод", "8000000"),
    ("Курбонов Саидкарим", "6000000"),
    ("Тошхужайува Комола", "26000000"),
]


class Command(BaseCommand):
    help = "Xodimlar ro'yxatini oylik maoshi bilan qo'shadi (takroran ishlatsa bo'ladi)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--update-salary",
            action="store_true",
            help="Allaqachon bor xodimning oyligini ro'yxatdagi summaga tenglashtiradi.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        update_salary = options["update_salary"]
        created = existing = updated = 0
        for name, salary in STAFF:
            amount = Decimal(salary)
            obj, was_created = Employee.objects.get_or_create(
                name=name,
                defaults={"salary": amount, "is_active": True},
            )
            if was_created:
                created += 1
                self.stdout.write(f"  + {name} — {amount:,.0f} so'm".replace(",", " "))
            elif update_salary and obj.salary != amount:
                obj.salary = amount
                obj.save(update_fields=["salary"])
                updated += 1
                self.stdout.write(f"  ~ {name} — {amount:,.0f} so'm".replace(",", " "))
            else:
                existing += 1
        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {created} ta yangi xodim qo'shildi, {updated} ta oyligi yangilandi, "
            f"{existing} ta o'zgarishsiz qoldi (jami {Employee.objects.count()})."
        ))
