from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club", "0075_data_structure_collections")]

    operations = [
        migrations.AddField(
            model_name="datastructureimplementation",
            name="blog",
            field=models.TextField(blank=True, null=True),
        ),
    ]
