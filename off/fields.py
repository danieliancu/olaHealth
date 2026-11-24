"""The part of an Open Food Facts product object kept with each revision (``off_payload``).

A dump line carries the whole product (about 24 KB on average, up to ~80 KB); only what
the app reads again is kept, so a re-mapping from the stored payload (``remap_off``) gives
exactly the result of the original import. The readers of the stored payload:

* ``off.mapping.map_product`` and ``off.basis`` (product data, per-100 basis);
* ``off.images.image_urls`` (photos; ``images`` trimmed to the selected photos' revisions);
* ``assessments.rules.derive_from_off`` (indicators);
* ``assessments.score`` (``nutriscore`` per version: the calculation category flags);
* ``catalog.services.importer`` (``code``, ``last_modified_t``);
* ``catalog.services.catalog_import.verify`` (``same_score_as_api``);
* ``catalog/management/commands/remap_off.py`` (all of the above).

Start from the API field list (``off.client.PRODUCT_FIELDS``), which is what an API import
already stores, plus ``images``.
"""

from __future__ import annotations

from typing import Any

from off.client import PRODUCT_FIELDS
from off.images import trim_images
from off.mapping import trim_nutrition

# ``images`` (dump photos) and ``nutrition`` (the current schema's nutrition values, empty
# ``nutriments``) are not requested from the API, which sends direct URLs and ``nutriments``.
STORED_FIELDS: tuple[str, ...] = (*PRODUCT_FIELDS.split(","), "images", "nutrition")
_TRIMMERS = {"images": trim_images, "nutrition": trim_nutrition}


def trim_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The stored part of ``payload`` (a new dict; the input is not changed)."""
    out = {key: payload[key] for key in STORED_FIELDS if key in payload}
    for key, trim in _TRIMMERS.items():
        if key in out:
            trimmed = trim(out[key])
            if trimmed is None:
                del out[key]
            else:
                out[key] = trimmed
    return out
