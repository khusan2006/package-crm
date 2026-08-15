"""Give every existing worker a dated wage, starting from the month this ships.

Wages used to be a single figure with no history, so there is nothing to reconstruct:
the honest reading is "as of now, they earn this". Each worker therefore gets one rate
row at their `start_month` — which 0037 set to the current month for existing records,
precisely so that switching on the carry-over does not invent months of unpaid wages
for people who have been paid all along. Earlier months (and any balance carried in)
are entered by hand, per worker, on the payroll page.
"""

from django.db import migrations


def seed_rates(apps, schema_editor):
    Employee = apps.get_model("crm", "Employee")
    SalaryRate = apps.get_model("crm", "SalaryRate")
    SalaryRate.objects.bulk_create([
        SalaryRate(
            employee=e,
            effective_from=e.start_month.replace(day=1),
            amount=e.salary,
        )
        for e in Employee.objects.all()
    ])


def drop_rates(apps, schema_editor):
    apps.get_model("crm", "SalaryRate").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0037_employee_end_month_employee_opening_balance_and_more"),
    ]

    operations = [migrations.RunPython(seed_rates, drop_rates)]
