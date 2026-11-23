from django.db import migrations, models
from django.db.models import F


def keep_existing_order(apps, schema_editor):
    collections = apps.get_model("club", "DataStructureCollection").objects.using(schema_editor.connection.alias)
    collections.update(position=F("pk"))


class Migration(migrations.Migration):
    dependencies = [("club", "0077_data_structure_rejection")]

    operations = [
        migrations.AddField(
            model_name="datastructurecollection",
            name="position",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(keep_existing_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="datastructurecollection",
            options={"ordering": ["position", "-id"]},
        ),
    ]
