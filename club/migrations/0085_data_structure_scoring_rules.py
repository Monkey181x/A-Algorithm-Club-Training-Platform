from django.db import migrations, models


def backfill_training_submissions(apps, schema_editor):
    implementation = apps.get_model("club", "DataStructureImplementation")
    # Before configurable scoring, every credited row counted as two submissions.
    implementation.objects.using(schema_editor.connection.alias).filter(
        training_week__isnull=False, training_points__gt=0,
    ).update(training_submissions=2)


class Migration(migrations.Migration):
    dependencies = [("club", "0084_legacy_poker_refunds")]

    operations = [
        migrations.AddField("semester", "data_structure_valid_submissions", models.PositiveIntegerField(default=2)),
        migrations.AddField("semester", "data_structure_code_points", models.PositiveIntegerField(default=2)),
        migrations.AddField("semester", "data_structure_blog_points", models.PositiveIntegerField(default=2)),
        migrations.AlterField("datastructureimplementation", "training_points", models.PositiveIntegerField(default=0)),
        migrations.AddField("datastructureimplementation", "training_submissions", models.PositiveIntegerField(default=0)),
        migrations.AddField("datastructureimplementation", "training_rule_submissions", models.PositiveIntegerField(default=2)),
        migrations.AddField("datastructureimplementation", "training_rule_code_points", models.PositiveIntegerField(default=2)),
        migrations.AddField("datastructureimplementation", "training_rule_blog_points", models.PositiveIntegerField(default=2)),
        migrations.RunPython(backfill_training_submissions, migrations.RunPython.noop),
    ]
