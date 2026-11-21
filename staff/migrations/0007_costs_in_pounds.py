"""Costs in pounds: the dollar budget becomes a pound budget (converted at 0.75), plus the
exchange rate used to convert what providers bill in dollars."""

from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def _to_pounds(apps, schema_editor):
    SiteSettings = apps.get_model("staff", "SiteSettings")
    for s in SiteSettings.objects.all():
        s.ai_monthly_budget_gbp = (s.ai_monthly_budget_gbp * Decimal("0.75")).quantize(
            Decimal("0.01")
        )
        s.save(update_fields=["ai_monthly_budget_gbp"])


def _to_dollars(apps, schema_editor):
    SiteSettings = apps.get_model("staff", "SiteSettings")
    for s in SiteSettings.objects.all():
        s.ai_monthly_budget_gbp = (s.ai_monthly_budget_gbp / s.usd_to_gbp).quantize(
            Decimal("0.01")
        )
        s.save(update_fields=["ai_monthly_budget_gbp"])


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0006_ai_send_ingredients"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="usd_to_gbp",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("0.7500"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0.0001"))],
            ),
        ),
        migrations.RenameField(
            model_name="sitesettings",
            old_name="ai_monthly_budget_usd",
            new_name="ai_monthly_budget_gbp",
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="ai_monthly_budget_gbp",
            field=models.DecimalField(decimal_places=2, default=Decimal("15.00"), max_digits=8),
        ),
        migrations.RunPython(_to_pounds, _to_dollars),
    ]
