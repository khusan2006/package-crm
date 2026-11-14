from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0067_loaditem_money_car"),
    ]

    operations = [
        migrations.AddField(
            model_name="load",
            name="money_car_off",
            field=models.BooleanField(default=False, verbose_name="Pul olish mashinasisiz"),
        ),
    ]
