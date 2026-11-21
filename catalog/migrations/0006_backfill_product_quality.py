"""Fill Product.quality_issues / listable for products imported before they existed."""

from django.db import migrations


def backfill(apps, schema_editor):
    from catalog import quality

    Product = apps.get_model("catalog", "Product")
    ProductImage = apps.get_model("catalog", "ProductImage")
    with_front = set(
        ProductImage.objects.filter(is_active=True, kind="front").values_list(
            "product_id", flat=True
        )
    )
    batch = []
    products = Product.objects.filter(current_revision__isnull=False).select_related(
        "current_revision"
    )
    for p in products.iterator(chunk_size=500):
        found = quality.issues(p.current_revision.data or {}, has_front_image=p.pk in with_front)
        p.quality_issues, p.listable = found, quality.listable(found)
        batch.append(p)
        if len(batch) >= 500:
            Product.objects.bulk_update(batch, ["quality_issues", "listable"])
            batch = []
    if batch:
        Product.objects.bulk_update(batch, ["quality_issues", "listable"])


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_product_quality")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
