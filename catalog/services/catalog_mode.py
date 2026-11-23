"""Which product catalogue is used: the per-product API mode, or the local OFF catalogue.

* ``api`` (current mode): products are fetched one by one from the Open Food Facts API and
  kept locally; alternatives also search OFF.
* ``local``: an imported OFF dump has been **verified**. Lookups already use local data first;
  in addition, alternatives come from the local catalogue only and products last written by
  the import are not refreshed periodically through the API (only when viewed, within the
  API limits). A product missing locally is still fetched from the API and saved.

The local mode starts automatically only after a verified import, never after a running,
failed or rejected one, and the operator can force the API mode (staff Settings →
Product catalogue → API only). See docs/off-local-catalogue.md.
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["api", "local"]


def latest_verified_import():
    from catalog.models import CatalogImport

    return (
        CatalogImport.objects.filter(status=CatalogImport.Status.VERIFIED)
        .order_by("-verified_at", "-pk")
        .first()
    )


def forced_api() -> bool:
    from staff.models import SiteSettings

    return SiteSettings.load().catalog_mode == "api"


def local_catalogue_active() -> bool:
    return not forced_api() and latest_verified_import() is not None


def current_mode() -> Mode:
    return "local" if local_catalogue_active() else "api"


def describe() -> dict:
    """For the staff dashboard: mode, why, and the latest imports."""
    from catalog.models import CatalogImport

    verified = latest_verified_import()
    forced = forced_api()
    if forced:
        reason = "Forced by the operator (Settings → Product catalogue → API only)."
    elif verified is None:
        reason = "No verified catalogue import yet."
    else:
        reason = f"Import #{verified.pk} verified on {verified.verified_at:%Y-%m-%d %H:%M}."
    return {
        "mode": "local" if (verified is not None and not forced) else "api",
        "reason": reason,
        "verified": verified,
        "latest": CatalogImport.objects.order_by("-started_at", "-pk").first(),
    }
