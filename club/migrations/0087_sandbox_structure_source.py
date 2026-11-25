from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0086_team_data_structures_enabled")]

    operations = [
        migrations.AddField(
            "datastructurecollection", "sandbox_source_id",
            models.PositiveBigIntegerField(null=True, editable=False),
        ),
        migrations.AddConstraint(
            "datastructurecollection",
            models.UniqueConstraint(fields=["team", "sandbox_source_id"], name="unique_team_sandbox_structure"),
        ),
    ]
