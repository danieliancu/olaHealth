"""What the product page needs for the personal layer: the product's real facts and the
restriction states derived from the source.

The result goes to the page as JSON (``json_script``, CSP-safe) and is consumed by
static/js/ola/product.js, which only selects by the preferences stored in the browser.
"""

from __future__ import annotations

from typing import Any

from catalog import presenters
from catalog.nutrition import BASIS_LABELS
from myola.facts import ProductFacts
from myola.restrictions import all_statuses


def facts_from_product(product, snapshots) -> ProductFacts:
    """A catalogue product. Labels are not recorded yet, so only the source's ingredient
    analysis and allergen sections are used (no claim is ever inferred from absence)."""
    from core.assets import image_url

    analysis = next(
        (
            s
            for s in snapshots
            if s.indicator == "ingredients_analysis"
            and s.origin == "off_computed"
            and s.data_status == "available"
            and not s.invalidated_by_local_change
        ),
        None,
    )
    data = product.current_revision.data
    return ProductFacts(
        id=str(product.pk),
        name=data.get("name") or product.name or "",
        data=data,
        labels=[],
        analysis=list((analysis.value or {}).get("tags") or []) if analysis else [],
        brand=data.get("brand") or "",
        quantity=data.get("quantity") or "",
        image=image_url(product.front_image_url or ""),
        url=f"/p/{product.pk}/",
    )


def chat_facts(p: ProductFacts) -> dict[str, Any]:
    basis, values = p.per_100()
    return {
        "basis": basis,
        "basis_label": BASIS_LABELS.get(basis, "") if basis else "",
        "values": {
            k: {
                "value": str(v.value),
                "unit": v.unit,
                "display": presenters.format_value(v.value, v.unit),
            }
            for k, v in values.items()
        },
    }


def ola_payload(p: ProductFacts, *, nutriscore: str = "", nova: int | None = None) -> dict:
    return {
        "product": {
            "id": p.id,
            "name": p.name,
            "brand": p.brand,
            "quantity": p.quantity,
            "image": p.image,
            "url": p.url,
        },
        "restrictions": all_statuses(p),
        "facts": chat_facts(p),
        "nutriscore": nutriscore,
        "nova": nova,
    }
