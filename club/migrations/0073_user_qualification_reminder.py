from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0072_contest_task_run")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="qualification_reminder_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
