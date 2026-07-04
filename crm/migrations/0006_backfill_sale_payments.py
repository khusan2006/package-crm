from django.db import migrations


def backfill(apps, schema_editor):
    """Record a full payment for every existing non-debt sale, so the To'lov
    ledger reflects money already taken at the point of sale."""
    Sale = apps.get_model("crm", "Sale")
    Payment = apps.get_model("crm", "Payment")
    to_create = []
    for sale in Sale.objects.filter(is_debt=False).iterator():
        to_create.append(
            Payment(
                date=sale.date,
                amount=sale.weight * sale.price,
                method="cash",
                kind="sale",
                sale_id=sale.pk,
                created_by_id=sale.sales_rep_id,
            )
        )
    Payment.objects.bulk_create(to_create)


def unbackfill(apps, schema_editor):
    Payment = apps.get_model("crm", "Payment")
    Payment.objects.filter(kind="sale").delete()


class Migration(migrations.Migration):
    dependencies = [("crm", "0005_payment")]
    operations = [migrations.RunPython(backfill, unbackfill)]
