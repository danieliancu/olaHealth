"""Open Food Facts sometimes sends HTML entities in text (Biscuits &quot;lotus&quot;).
The importer now decodes them; this fixes products and revisions already stored."""

import html

from django.db import migrations

TEXT_FIELDS = ("name", "brand", "quantity", "search_text")


def _clean(value):
    if isinstance(value, str):
        return html.unescape(value) if "&" in value else value
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def unescape(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductRevision = apps.get_model("catalog", "ProductRevision")
    for p in Product.objects.filter(
        models_q(TEXT_FIELDS)
    ).iterator():
        for f in TEXT_FIELDS:
            setattr(p, f, _clean(getattr(p, f)))
        p.save(update_fields=list(TEXT_FIELDS))
    for r in ProductRevision.objects.only("id", "data", "off_data").iterator():
        data, off = _clean(r.data), _clean(r.off_data)
        if data != r.data or off != r.off_data:
            ProductRevision.objects.filter(pk=r.pk).update(data=data, off_data=off)


def models_q(fields):
    from django.db.models import Q

    q = Q()
    for f in fields:
        q |= Q(**{f"{f}__contains": "&"})
    return q


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_taxonomies_remove_demo")]
    operations = [migrations.RunPython(unescape, migrations.RunPython.noop)]
