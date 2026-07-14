# Generated for Task 5: replace StockTransfer stub with the full model.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0019_employee_expense_employee_attendance'),
        ('manufacturing', '0003_stocktransfer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(
            name='StockTransfer',
        ),
        migrations.CreateModel(
            name='StockTransfer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=django.utils.timezone.localdate, verbose_name='Sana')),
                ('quantity_kg', models.DecimalField(decimal_places=3, max_digits=12, verbose_name='Miqdor (kg)')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='Izoh')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers_made', to=settings.AUTH_USER_MODEL, verbose_name='Kim topshirdi')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers', to='crm.product', verbose_name='Mahsulot')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers_in', to=settings.AUTH_USER_MODEL, verbose_name='Sotuvchi')),
            ],
            options={
                'verbose_name': 'Omborga topshiruv',
                'verbose_name_plural': 'Omborga topshiruvlar',
                'ordering': ['-date', '-created_at'],
            },
        ),
    ]
