from django.db import migrations, models

# The seller whose yuk xati the owner wanted under her name alone on 2026-10-17.
# Her firm still heads her «Oylik hisobot»; every other seller keeps it on both.
NAME_ONLY_SELLERS = ["umida"]


def hide_firm(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for username in NAME_ONLY_SELLERS:
        User.objects.filter(username__iexact=username).update(firm_on_yuk_xati=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_user_payroll_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='firm_on_yuk_xati',
            field=models.BooleanField(default=True, verbose_name='Yuk xatida firma nomi chiqsin'),
        ),
        migrations.RunPython(hide_firm, migrations.RunPython.noop),
    ]
