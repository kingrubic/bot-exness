from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_walletaccount_default_lot_size_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='walletaccount',
            name='min_take_profit_usd',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('1.00'),
                max_digits=12,
                verbose_name='Số Tiền Min Chốt Lời (USD)',
            ),
        ),
    ]
