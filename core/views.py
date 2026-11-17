from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process answers. No dependency or version details."""
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: the database answers a trivial query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
