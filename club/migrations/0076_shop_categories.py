from django.db import migrations, models
import django.db.models.deletion


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    Category = apps.get_model('club', 'ShopCategory')
    Item = apps.get_model('club', 'ShopItem')
    Team = apps.get_model('club', 'Team')
    groups = [('装扮', ['title', 'badge', 'theme', 'frame', 'cosmetic']), ('实物', ['physical']), ('核销', ['redeem_code']), ('道具', ['general', 'consumable', 'makeup_ticket'])]
    for team_id in Team.objects.using(alias).values_list('id', flat=True):
        for position, (name, kinds) in enumerate(groups):
            category = Category.objects.using(alias).create(team_id=team_id, name=name, position=position)
            Item.objects.using(alias).filter(team_id=team_id, item_type__in=kinds).update(category_id=category.pk)


class Migration(migrations.Migration):
    dependencies = [('club', '0075_extension_category_order')]
    operations = [
        migrations.CreateModel(name='ShopCategory', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=30)),
            ('position', models.PositiveIntegerField(default=0)),
            ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shop_categories', to='club.team')),
        ], options={'ordering': ['position', 'id'], 'constraints': [models.UniqueConstraint(fields=('team', 'name'), name='unique_shop_category_name')]}),
        migrations.AddField(model_name='shopitem', name='category', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='items', to='club.shopcategory')),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
