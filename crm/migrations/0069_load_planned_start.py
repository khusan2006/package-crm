from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0068_load_money_car_off"),
    ]

    operations = [
        migrations.AddField(
            model_name="load",
            name="planned_start",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Rejadagi jo'nash vaqti"),
        ),
    ]
