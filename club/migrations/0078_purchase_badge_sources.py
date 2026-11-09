from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club', '0077_global_shop')]
    operations = [
        migrations.RemoveConstraint(model_name='purchasebadgegrant', name='unique_user_purchase_badge'),
        migrations.AddConstraint(model_name='purchasebadgegrant', constraint=models.UniqueConstraint(
            fields=['purchase', 'key'], name='unique_purchase_badge')),
    ]
