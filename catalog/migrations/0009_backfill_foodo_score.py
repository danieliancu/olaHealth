"""Store the Foodo Score (score.v2) of current revisions published before it existed.

Revisions are immutable through ``save()``; ``bulk_update`` writes only this derived column.
Revisions it does not reach are computed in memory when read (``score_for_revision``).
"""

from django.db import migrations


def backfill(apps, schema_editor):
    from assessments.score import VERSION, foodo_score

    Product = apps.get_model("catalog", "Product")
    ProductRevision = apps.get_model("catalog", "ProductRevision")
    current = Product.objects.filter(current_revision__isnull=False).values("current_revision_id")
    revisions = (
        ProductRevision.objects.filter(pk__in=current)
        .exclude(foodo_score__version=VERSION)
        .prefetch_related("assessments")
    )
    batch = []
    for revision in revisions.iterator(chunk_size=500):
        revision.foodo_score = foodo_score(revision.assessments.all())
        batch.append(revision)
        if len(batch) >= 500:
            ProductRevision.objects.bulk_update(batch, ["foodo_score"])
            batch = []
    if batch:
        ProductRevision.objects.bulk_update(batch, ["foodo_score"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0008_revision_foodo_score"),
        ("assessments", "0002_initial"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
