from django.db import migrations, models

# The one seller the owner left «Xodimlar oyligi» open to on 2026-10-06. Every other
# seller starts without it; the admin switches it per account in Foydalanuvchilar.
PAYROLL_SELLERS = ["komola@test.com"]


def grant_payroll(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for email in PAYROLL_SELLERS:
        User.objects.filter(email__iexact=email).update(payroll_access=True)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_firm_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='payroll_access',
            field=models.BooleanField(default=False, verbose_name="Xodimlar oyligini ko'radi"),
        ),
        migrations.RunPython(grant_payroll, migrations.RunPython.noop),
    ]
