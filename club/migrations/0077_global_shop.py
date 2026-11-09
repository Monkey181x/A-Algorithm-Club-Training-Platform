from django.db import migrations, models
import django.db.models.deletion as deletion
from django.conf import settings


def migrate_data(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name in ['Purchase', 'UserInventory']:
        Model = apps.get_model('club', model_name)
        for row in Model.objects.using(alias).select_related('member').all().iterator():
            row.user_id = row.member.user_id
            row.save(using=alias, update_fields=['user'])
    Category = apps.get_model('club', 'ShopCategory')
    Item = apps.get_model('club', 'ShopItem')
    seen = {}
    for category in Category.objects.using(alias).order_by('position', 'id'):
        if category.name in seen:
            Item.objects.using(alias).filter(category_id=category.pk).update(category_id=seen[category.name])
            category.delete(using=alias)
        else:
            seen[category.name] = category.pk
            category.position = len(seen) - 1
            category.save(using=alias, update_fields=['position'])


class Migration(migrations.Migration):
    dependencies = [('club', '0076_shop_categories'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name='shopcategory', name='team', field=models.ForeignKey(to='club.team', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='shop_categories')),
        migrations.AlterField(model_name='shopitem', name='team', field=models.ForeignKey(to='club.team', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='shop_items')),
        migrations.AlterField(model_name='purchase', name='team', field=models.ForeignKey(to='club.team', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='purchases')),
        migrations.AlterField(model_name='userinventory', name='team', field=models.ForeignKey(to='club.team', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='user_inventories')),
        migrations.AlterField(model_name='purchase', name='member', field=models.ForeignKey(to='club.teammembership', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='purchases')),
        migrations.AddField(model_name='purchase', name='user', field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, on_delete=deletion.CASCADE)),
        migrations.AlterField(model_name='userinventory', name='member', field=models.ForeignKey(to='club.teammembership', null=True, blank=True, on_delete=deletion.SET_NULL, related_name='inventory')),
        migrations.AddField(model_name='userinventory', name='user', field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, on_delete=deletion.CASCADE)),
        migrations.RunPython(migrate_data, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='shopcategory', name='unique_shop_category_name'),
        migrations.AddConstraint(model_name='shopcategory', constraint=models.UniqueConstraint(fields=['name'], name='unique_shop_category_name')),
    ]
