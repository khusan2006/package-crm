from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0066_link_old_stops_to_their_sales"),
    ]

    operations = [
        migrations.AddField(
            model_name="loaditem",
            name="money_car",
            field=models.BooleanField(default=False, verbose_name="Pul olish mashinasida"),
        ),
    ]
