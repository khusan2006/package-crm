from django.db import migrations, models

# The two live sellers' firms, as the owner named them on 2026-09-30.
FIRMS = {"kamola": "Rise service", "umida": "Polimer111"}


def set_firms(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for username, firm in FIRMS.items():
        User.objects.filter(username__iexact=username).update(firm_name=firm)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_opening_production_debt'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='firm_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='Firma nomi (yuk xati)'),
        ),
        migrations.RunPython(set_firms, migrations.RunPython.noop),
    ]
