from django.db import migrations, models


def seed_order(apps, schema_editor):
    Category = apps.get_model('club', 'ExtensionCategory')
    entries = list(Category.objects.using(schema_editor.connection.alias).all())
    order = {'library': 0, 'tools': 1, 'games': 2}
    entries.sort(key=lambda c: (order.get(c.key, 3), c.name))
    for position, entry in enumerate(entries):
        entry.position = position
        entry.save(using=schema_editor.connection.alias, update_fields=['position'])


class Migration(migrations.Migration):
    dependencies = [('club', '0074_dynamic_extension_categories')]
    operations = [
        migrations.AddField(model_name='extensioncategory', name='position', field=models.PositiveIntegerField(default=0)),
        migrations.RunPython(seed_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(name='extensioncategory', options={'ordering': ['position', 'key']}),
    ]
