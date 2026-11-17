from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class SecurityHeadersMiddleware:
    """Content-Security-Policy and Permissions-Policy for the public pages.

    No inline scripts are used by our templates; WebAssembly is needed by the barcode
    decoder fallback. Images may come from the allow-listed Open Food Facts hosts.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        img_hosts = " ".join(f"https://{h}" for h in settings.OFF_IMAGE_HOSTS)
        self.csp = (
            "default-src 'self'; "
            f"img-src 'self' data: blob: {img_hosts}; "
            "script-src 'self' 'wasm-unsafe-eval'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "media-src 'self' blob:; "
            "worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.headers.setdefault("Content-Security-Policy", self.csp)
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), geolocation=(), microphone=()"
        )
        return response
