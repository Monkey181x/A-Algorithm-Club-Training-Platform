import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('club', '0031_submission_code_part')]
    operations = [
        migrations.AddField(model_name='shopitem', name='card_source', field=models.CharField(default='inventory', max_length=16)),
        migrations.AddField(model_name='shopitem', name='encrypted_card_api', field=models.TextField(blank=True)),
        migrations.CreateModel(name='ShopFulfillment', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('sequence', models.PositiveIntegerField()),
            ('item_name', models.CharField(max_length=100)),
            ('status', models.CharField(default='ready', max_length=16)),
            ('request_key', models.UUIDField(default=uuid.uuid4, unique=True)),
            ('encrypted_code', models.TextField(blank=True)),
            ('redeemed_at', models.DateTimeField(blank=True, null=True)),
            ('delivered_at', models.DateTimeField(blank=True, null=True)),
            ('delivered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ('purchase', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fulfillments', to='club.purchase')),
        ], options={'constraints': [models.UniqueConstraint(fields=('purchase', 'sequence'), name='unique_shop_purchase_unit')]}),
        migrations.CreateModel(name='ShopCode', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('encrypted_value', models.TextField()),
            ('fingerprint', models.CharField(max_length=64)),
            ('consumed_at', models.DateTimeField(blank=True, null=True)),
            ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='card_codes', to='club.shopitem')),
            ('reserved_for', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reserved_code', to='club.shopfulfillment')),
        ], options={'constraints': [models.UniqueConstraint(fields=('item', 'fingerprint'), name='unique_shop_code_fingerprint')]}),
    ]
