from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club','0038_platform_solved_baselines')]
    operations = [migrations.AddField(model_name='teammembership',name='member_title',
        field=models.JSONField(default=dict,blank=True))]
