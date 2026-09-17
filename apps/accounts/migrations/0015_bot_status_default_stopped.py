from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_optional_tp_sl_and_trail'),
    ]

    operations = [
        migrations.AlterField(
            model_name='walletaccount',
            name='bot_status',
            field=models.CharField(
                choices=[
                    ('RUNNING', 'Đang Hoạt Động (Auto Trade)'),
                    ('PAUSED', 'Tạm Dừng'),
                    ('STOPPED', 'Đã Dừng Hẳn'),
                ],
                default='STOPPED',
                max_length=20,
                verbose_name='Trạng Thái Bot',
            ),
        ),
    ]
