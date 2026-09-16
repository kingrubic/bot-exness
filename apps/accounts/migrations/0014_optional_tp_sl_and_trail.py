from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_walletaccount_max_stop_loss_usd'),
    ]

    operations = [
        migrations.AlterField(
            model_name='walletaccount',
            name='min_take_profit_usd',
            field=models.DecimalField(
                blank=True, decimal_places=2, default=None, max_digits=12,
                null=True, verbose_name='Số Tiền Min Chốt Lời (USD)',
            ),
        ),
        migrations.AlterField(
            model_name='walletaccount',
            name='max_stop_loss_usd',
            field=models.DecimalField(
                blank=True, decimal_places=2, default=None, max_digits=12,
                null=True, verbose_name='Số Tiền Max Cắt Lỗ (USD)',
            ),
        ),
        migrations.AddField(
            model_name='walletaccount',
            name='trail_sl_enabled',
            field=models.BooleanField(default=False, verbose_name='Tự động dời SL khi lãi đạt'),
        ),
        migrations.AddField(
            model_name='walletaccount',
            name='trail_sl_lock_usd',
            field=models.DecimalField(
                blank=True, decimal_places=2, default=None, max_digits=12,
                null=True, verbose_name='Khoá lãi khi dời SL (USD)',
            ),
        ),
    ]
