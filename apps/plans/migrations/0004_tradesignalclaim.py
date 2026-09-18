from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_bot_status_default_stopped'),
        ('plans', '0003_alter_tradingplan_entry_price_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeSignalClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol', models.CharField(max_length=30)),
                ('direction', models.CharField(choices=[('BUY', 'Mua (BUY / Long)'), ('SELL', 'Bán (SELL / Short)')], max_length=10)),
                ('candle_time', models.BigIntegerField(help_text='Unix timestamp của nến tín hiệu đã đóng.')),
                ('status', models.CharField(choices=[('CLAIMED', 'Đã giữ quyền xử lý'), ('EXECUTED', 'Đã gửi thành công'), ('FAILED', 'Gửi thất bại / kết quả chưa chắc chắn')], default='CLAIMED', max_length=12)),
                ('order_ticket', models.CharField(blank=True, default='', max_length=64)),
                ('error', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trade_signal_claims', to='accounts.walletaccount')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['wallet', 'symbol', '-candle_time'], name='plans_trade_wallet__a2a773_idx')],
                'constraints': [models.UniqueConstraint(fields=('wallet', 'symbol', 'candle_time'), name='unique_bot_signal_per_wallet_symbol_candle')],
            },
        ),
    ]
