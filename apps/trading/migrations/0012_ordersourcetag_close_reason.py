from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0011_ordersourcetag'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordersourcetag',
            name='close_reason',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='Lý do đóng đã ghi nhận'),
        ),
    ]
