from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0019_cover_desktop_only")]

    operations = [
        migrations.RenameField("learnarticle", "featured", "editors_pick"),
        migrations.AlterField(
            model_name="learnarticle",
            name="editors_pick",
            field=models.BooleanField(
                default=False,
                verbose_name="Editor’s pick",
                help_text="Shown in “Editor’s picks” on the Magazine cover (the newest 3).",
            ),
        ),
    ]
