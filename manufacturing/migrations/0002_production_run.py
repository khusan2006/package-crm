# Generated for Task 3: replace ProductionRunItem stub, add ProductionRun.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0019_employee_expense_employee_attendance'),
        ('manufacturing', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(
            name='ProductionRunItem',
        ),
        migrations.CreateModel(
            name='ProductionRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=django.utils.timezone.localdate, verbose_name='Sana')),
                ('output_kg', models.DecimalField(decimal_places=3, max_digits=12, verbose_name='Ishlab chiqarildi (kg)')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='Izoh')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='production_runs', to=settings.AUTH_USER_MODEL, verbose_name='Kim kiritdi')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='production_runs', to='crm.product', verbose_name='Mahsulot')),
            ],
            options={
                'verbose_name': 'Ishlab chiqarish',
                'verbose_name_plural': 'Ishlab chiqarishlar',
                'ordering': ['-date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ProductionRunItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_kg', models.DecimalField(decimal_places=3, max_digits=12, verbose_name='Miqdor (kg)')),
                ('unit_cost', models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Tannarx (1 kg, so'm)")),
                ('material', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='usages', to='manufacturing.rawmaterial', verbose_name='Xomashyo')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='manufacturing.productionrun', verbose_name='Ishlab chiqarish')),
            ],
            options={
                'verbose_name': 'Ishlab chiqarish xomashyosi',
                'verbose_name_plural': 'Ishlab chiqarish xomashyolari',
            },
        ),
    ]
