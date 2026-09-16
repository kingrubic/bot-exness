from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_alter_walletaccount_balance_db_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='walletaccount',
            name='max_stop_loss_usd',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('1.00'),
                max_digits=12,
                verbose_name='Số Tiền Max Cắt Lỗ (USD)',
            ),
        ),
    ]
