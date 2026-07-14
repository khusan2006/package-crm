from django.db import migrations

from manufacturing.migrations_helpers import apply_cutover


def forwards(apps, schema_editor):
    apply_cutover(apps)


def backwards(apps, schema_editor):
    StockEntry = apps.get_model("crm", "StockEntry")
    SellerStockEntry = apps.get_model("manufacturing", "SellerStockEntry")
    StockEntry.objects.filter(note="Cutover moslash").delete()
    SellerStockEntry.objects.filter(note="Cutover moslash").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("manufacturing", "0005_sellerstockentry"),
        ("crm", "0019_employee_expense_employee_attendance"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
