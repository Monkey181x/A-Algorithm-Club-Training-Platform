from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0085_data_structure_scoring_rules")]

    operations = [
        migrations.AddField("team", "data_structures_enabled", models.BooleanField(default=True)),
    ]
