from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club', '0072_contest_task_run')]

    operations = [
        migrations.AlterField(
            model_name='extensionresource', name='category',
            field=models.CharField(choices=[('library', '学习资料'), ('tools', '工具'), ('games', '小游戏')], default='library', max_length=16),
        ),
        migrations.AlterField(
            model_name='builtinextensioncategory', name='category',
            field=models.CharField(choices=[('library', '学习资料'), ('tools', '工具'), ('games', '小游戏')], default='games', max_length=16),
        ),
    ]
