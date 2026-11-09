from django.db import migrations, models


def seed(apps, schema_editor):
    alias = schema_editor.connection.alias
    Category = apps.get_model('club', 'ExtensionCategory')
    Builtin = apps.get_model('club', 'BuiltinExtensionCategory')
    for key, name in [('library', '学习资料'), ('tools', '工具'), ('games', '小游戏')]:
        Category.objects.using(alias).get_or_create(key=key, defaults={'name': name})
    Builtin.objects.using(alias).get_or_create(key='poker', defaults={'category': 'games'})


class Migration(migrations.Migration):
    dependencies = [('club', '0073_extension_tools_category')]
    operations = [
        migrations.CreateModel(name='ExtensionCategory', fields=[
            ('key', models.CharField(max_length=16, primary_key=True, serialize=False)),
            ('name', models.CharField(max_length=30, unique=True)),
        ], options={'ordering': ['key']}),
        migrations.RunPython(seed, migrations.RunPython.noop),
        migrations.AlterField(model_name='extensionresource', name='category', field=models.CharField(max_length=16)),
        migrations.AlterField(model_name='builtinextensioncategory', name='category', field=models.CharField(max_length=16)),
    ]
