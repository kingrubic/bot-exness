from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0010_alter_tradehistory_close_reason'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderSourceTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticket', models.CharField(db_index=True, max_length=64, unique=True)),
                ('source', models.CharField(choices=[('BOT', 'Bot (Tự Động)'), ('USER', 'User (Người Dùng)')], default='USER', max_length=10)),
                ('magic', models.IntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Nguồn lệnh (Bot/User)',
            },
        ),
    ]
