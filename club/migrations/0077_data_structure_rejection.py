from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0076_data_structure_blog")]

    operations = [
        migrations.RenameField(
            model_name="datastructureimplementation",
            old_name="is_hidden",
            new_name="is_rejected",
        ),
        migrations.AddField(
            model_name="datastructureimplementation",
            name="rejection_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
