from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club', '0032_shop_fulfillment')]
    operations = [
        migrations.AddField(
            model_name='shopitem', name='purchase_limit',
            field=models.PositiveIntegerField(default=0, verbose_name='单用户累计限购数量'),
        ),
    ]
