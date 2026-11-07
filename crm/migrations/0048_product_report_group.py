from django.db import migrations, models


def group_for(name):
    """The monthly-report line an existing product belongs to, as the firm keeps it on
    paper: every bag is КОП, ОҚ ЯЛТИРОҚ is counted with ОҚ, КЎК with НОВВОТ."""
    upper = name.upper()
    if "КОП" in upper or "ҚОП" in upper:
        return "КОП"
    if upper.startswith("ОҚ"):
        return "ОҚ"
    if upper.startswith("НОВВОТ") or upper.startswith("КЎК"):
        return "НОВВОТ"
    if upper.startswith("ҚОРА"):
        return "ҚОРА"
    return ""


def set_groups(apps, schema_editor):
    Product = apps.get_model("crm", "Product")
    for product in Product.objects.all():
        group = group_for(product.name)
        if group:
            product.report_group = group
            product.save(update_fields=["report_group"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0047_employee_work_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="report_group",
            field=models.CharField(
                blank=True,
                help_text="Oylik hisobotda shu nom ostida qo'shib ko'rsatiladi (masalan: ОҚ, "
                          "НОВВОТ, КОП). Bo'sh qolsa — mahsulot o'z nomi bilan chiqadi.",
                max_length=60,
                verbose_name="Hisobot guruhi",
            ),
        ),
        migrations.RunPython(set_groups, migrations.RunPython.noop),
    ]
