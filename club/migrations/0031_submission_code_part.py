from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club', '0030_remove_team_join_password')]
    operations = [migrations.AlterField(model_name='submissionpart', name='kind',
        field=models.CharField(max_length=10, choices=[('proof', '通过截图'),
            ('logic', '写题逻辑'), ('blog', 'Blog'), ('code', '代码')]))]
