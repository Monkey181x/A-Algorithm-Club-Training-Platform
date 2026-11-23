from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("club", "0079_data_structure_collection_version")]

    operations = [
        migrations.AddField(
            model_name="datastructureimplementation",
            name="training_week",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="data_structure_submissions", to="club.week",
            ),
        ),
        migrations.AddField(
            model_name="datastructureimplementation",
            name="training_points",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
