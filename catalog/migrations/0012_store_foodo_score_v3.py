"""Store the Foodo Score (score.v3, x/100) of current revisions and its product value.

Only derived columns are written (``ProductRevision.foodo_score`` via ``bulk_update`` and
``Product.foodo_value``): revision data, corrections and history are untouched. Revisions it
does not reach are computed in memory when read, and the hourly job stores them.
"""

from django.db import migrations


def store(apps, schema_editor):
    from assessments.score import foodo_value, for_revision, needs_recompute

    Product = apps.get_model("catalog", "Product")
    ProductRevision = apps.get_model("catalog", "ProductRevision")
    products = Product.objects.filter(current_revision__isnull=False).only(
        "pk", "current_revision_id", "foodo_value"
    )
    for start in range(0, products.count(), 500):
        chunk = list(products.order_by("pk")[start : start + 500])
        revisions = {
            r.pk: r
            for r in ProductRevision.objects.filter(
                pk__in=[p.current_revision_id for p in chunk]
            ).prefetch_related("assessments")
        }
        changed = []
        for revision in revisions.values():
            if needs_recompute(revision.foodo_score):
                revision.foodo_score = for_revision(revision)
                changed.append(revision)
        ProductRevision.objects.bulk_update(changed, ["foodo_score"])
        for p in chunk:
            p.foodo_value = foodo_value(revisions[p.current_revision_id].foodo_score)
        Product.objects.bulk_update(chunk, ["foodo_value"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0011_product_foodo_value"),
        ("assessments", "0002_initial"),
    ]
    operations = [migrations.RunPython(store, migrations.RunPython.noop)]
