from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0078_data_structure_order")]

    operations = [
        migrations.AddField(
            model_name="datastructurecollection",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
