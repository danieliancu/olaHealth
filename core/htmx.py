"""htmx request handling shared by every view.

* History restore (Back/Forward with ``historyCacheSize: 0``): htmx re-fetches the page with
  ``HX-Request`` *and* ``HX-History-Restore-Request``. Views must answer with the full page, so
  the htmx marker is removed before any view sees it.
* Session expired during an htmx action: a redirect to the login page would be swapped into a
  fragment. It becomes ``HX-Redirect`` so the browser loads the login page as a whole page.
* Responses that differ for htmx and normal requests carry ``Vary: HX-Request``.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import resolve_url
from django.utils.cache import patch_vary_headers


def is_htmx(request) -> bool:
    """True for htmx requests that expect a fragment (never for history restores)."""
    return request.headers.get("HX-Request") == "true" and not request.headers.get(
        "HX-History-Restore-Request"
    )


class HtmxMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # request.headers is a cached property: after removing the marker from META the cached
        # copy is dropped too, so every view sees a normal (full page) request.
        if request.META.get("HTTP_HX_HISTORY_RESTORE_REQUEST"):
            request.META.pop("HTTP_HX_REQUEST", None)
            request.META.pop("HTTP_HX_TARGET", None)
            request.__dict__.pop("headers", None)  # earlier middleware may have cached them
        htmx = request.META.get("HTTP_HX_REQUEST") == "true"
        response = self.get_response(request)
        patch_vary_headers(response, ("HX-Request",))
        if htmx and response.status_code in (301, 302) and self._to_login(response):
            redirect = HttpResponse(status=204)
            redirect["HX-Redirect"] = response["Location"]
            return redirect
        return response

    @staticmethod
    def _to_login(response) -> bool:
        login = resolve_url(settings.LOGIN_URL)
        return response.get("Location", "").split("?")[0] == login
