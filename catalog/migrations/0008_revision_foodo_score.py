from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0007_image_hidden_by_staff")]

    operations = [
        migrations.AddField(
            model_name="productrevision",
            name="foodo_score",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
