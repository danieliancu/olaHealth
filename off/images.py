"""Open Food Facts photo URLs, from the API's direct URLs or from the ``images`` metadata.

The product API returns ``image_front_url``, ``image_ingredients_url`` and
``image_nutrition_url``. The JSONL dump does not: it only carries the ``images`` object, in
one of two shapes (both seen in the dump, both resolve to the same file name):

* current (``schema_version`` ≥ 1003): ``images.selected.<kind>.<lc> = {imgid, rev, ...}``
  next to ``images.uploaded.<imgid>``;
* older: ``images.<kind>_<lc> = {imgid, rev, ...}`` next to ``images.<imgid>``.

A selected photo is served as
``https://images.openfoodfacts.org/images/products/<path>/<kind>_<lc>.<rev>.400.jpg``, the same
URL the API returns (``<path>`` from :func:`product_path`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

KINDS = ("front", "ingredients", "nutrition")
IMAGE_BASE = "https://images.openfoodfacts.org/images/products"
DISPLAY_SIZE = "400"


def product_path(code: str) -> str:
    """Folder of a product's photos: digits split 3/3/3/rest when at least 9 long.

    Codes shorter than 13 digits are left-padded with zeros first, as OFF stores them
    (an 8-digit EAN-8 ``12345678`` lives under ``000/001/234/5678``).
    """
    code = str(code or "").strip()
    if code.isdigit() and len(code) < 13:
        code = code.zfill(13)
    if code.isdigit() and len(code) >= 9:
        return f"{code[:3]}/{code[3:6]}/{code[6:9]}/{code[9:]}"
    return code


def _rev(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and int(value) > 0:
        return int(value)
    return None


def _languages(product: dict[str, Any], available: list[str]) -> list[str]:
    preferred = [str(product.get("lc") or ""), str(product.get("lang") or ""), "en"]
    order = [lc for lc in dict.fromkeys(preferred) if lc in available]
    return order + sorted(lc for lc in available if lc not in order)


def selected_revisions(product: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """kind -> (lc, rev) of the selected photo, from either metadata shape."""
    images = product.get("images")
    if not isinstance(images, dict):
        return {}
    found: dict[str, tuple[str, int]] = {}
    selected = images.get("selected") if isinstance(images.get("selected"), dict) else {}
    for kind in KINDS:
        candidates: dict[str, int] = {}
        per_lang = selected.get(kind) if isinstance(selected.get(kind), dict) else {}
        for lc, meta in per_lang.items():
            rev = _rev(meta.get("rev")) if isinstance(meta, dict) else None
            if rev is not None and isinstance(lc, str) and lc:
                candidates[lc] = rev
        for key, meta in images.items():
            if isinstance(key, str) and key.startswith(f"{kind}_") and isinstance(meta, dict):
                lc = key[len(kind) + 1 :]
                rev = _rev(meta.get("rev"))
                if rev is not None and lc and lc not in candidates:
                    candidates[lc] = rev
        if candidates:
            lc = _languages(product, list(candidates))[0]
            found[kind] = (lc, candidates[lc])
    return found


def built_urls(product: dict[str, Any]) -> dict[str, str]:
    code = str(product.get("code") or "")
    if not code:
        return {}
    path = product_path(code)
    return {
        kind: f"{IMAGE_BASE}/{path}/{kind}_{lc}.{rev}.{DISPLAY_SIZE}.jpg"
        for kind, (lc, rev) in selected_revisions(product).items()
    }


def has_image_information(product: dict[str, Any]) -> bool:
    """False when the payload says nothing about photos (no URL field, no ``images``)."""
    return "images" in product or any(f"image_{kind}_url" in product for kind in KINDS)


def image_urls(product: dict[str, Any], allowed_hosts: list[str]) -> dict[str, str] | None:
    """kind -> https URL on an allow-listed host; ``None`` when the payload has no photo
    information at all (so existing photos must be left as they are).

    Direct URLs (API) win; missing kinds are built from the ``images`` metadata (dump).
    """
    if not has_image_information(product):
        return None

    def ok(url: Any) -> bool:
        if not isinstance(url, str) or not url:
            return False
        parts = urlsplit(url)
        return parts.scheme == "https" and parts.hostname in allowed_hosts

    result = {kind: url for kind, url in built_urls(product).items() if ok(url)}
    for kind in KINDS:
        direct = product.get(f"image_{kind}_url")
        if ok(direct):
            result[kind] = direct
    return result


def trim_images(images: Any) -> dict[str, Any] | None:
    """Only what :func:`selected_revisions` reads: selected photos' ``imgid`` and ``rev``."""
    if not isinstance(images, dict):
        return None
    out: dict[str, Any] = {}
    selected = images.get("selected")
    if isinstance(selected, dict):
        kept = {}
        for kind in KINDS:
            per_lang = selected.get(kind)
            if isinstance(per_lang, dict):
                langs = {
                    lc: {k: meta[k] for k in ("imgid", "rev") if k in meta}
                    for lc, meta in per_lang.items()
                    if isinstance(meta, dict)
                }
                if langs:
                    kept[kind] = langs
        if kept:
            out["selected"] = kept
    for key, meta in images.items():
        if (
            isinstance(key, str)
            and isinstance(meta, dict)
            and any(key.startswith(f"{kind}_") for kind in KINDS)
        ):
            out[key] = {k: meta[k] for k in ("imgid", "rev") if k in meta}
    return out
