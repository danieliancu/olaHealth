"""JSON endpoints (reusable later by native apps). Same services as the HTML views."""

from __future__ import annotations

import uuid

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from catalog.forms import SearchForm
from catalog.models import Product
from catalog.serializers import ProductDetailSerializer, ProductSummarySerializer
from catalog.services.lookup import lookup
from catalog.services.search import SearchParams, search_products
from catalog.views import ERROR_MESSAGES, _country
from core.coordination import check_request_limit

HTTP_FOR_ERROR = {
    "invalid_code": status.HTTP_400_BAD_REQUEST,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
    "upstream_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_response": status.HTTP_502_BAD_GATEWAY,
}


@api_view(["GET"])
def lookup_api(request: Request) -> Response:
    code = str(request.query_params.get("code", ""))[:32]
    fmt = str(request.query_params.get("format", "manual"))[:16]
    decision = check_request_limit(request._request, "lookup")
    if not decision.allowed:
        return Response(
            {"status": "error", "error": "rate_limited", "message": ERROR_MESSAGES["rate_limited"]},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(decision.retry_after)},
        )
    result = lookup(code, fmt, country=_country(request._request))
    body: dict = {
        "status": result.status,
        "error": result.error or None,
        "message": result.message or ERROR_MESSAGES.get(result.error or result.status, ""),
        "code": None,
        "product": None,
        "stale": result.stale,
        "refresh_scheduled": result.refresh_scheduled,
    }
    if result.code:
        body["code"] = {
            "original": result.code.raw,
            "format": result.code.detected_format,
            "kind": result.code.kind,
            "gtin14": result.code.gtin14,
            "off_code": result.code.off_code,
        }
    headers = {}
    if result.retry_after:
        headers["Retry-After"] = str(result.retry_after)
    if result.status == "found":
        body["product"] = ProductSummarySerializer(result.product).data
        body["source"] = result.source
        if result.product.off_synced_at:
            body["synced_at"] = result.product.off_synced_at.isoformat()
        return Response(body)
    if result.status == "not_found":
        return Response(body, status=status.HTTP_404_NOT_FOUND)
    if result.status == "pending":
        return Response(body, status=status.HTTP_202_ACCEPTED, headers=headers)
    return Response(body, status=HTTP_FOR_ERROR[result.error], headers=headers)


class ProductListAPI(ListAPIView):
    serializer_class = ProductSummarySerializer

    def get_queryset(self):
        form = SearchForm(self.request.query_params or None)
        params = SearchParams(**form.cleaned_data) if form.is_valid() else SearchParams()
        return search_products(params)


class ProductDetailAPI(RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    queryset = (
        Product.objects.filter(current_revision__isnull=False)
        .select_related("current_revision")
        .prefetch_related("images", "identifiers", "current_revision__assessments")
    )


@api_view(["GET"])
def compare_api(request: Request) -> Response:
    from assessments.services.compare import ComparisonError, compare

    ids = []
    for value in str(request.query_params.get("ids", "")).split(","):
        try:
            ids.append(uuid.UUID(value.strip()))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))[:3]
    found = {
        p.pk: p
        for p in Product.objects.select_related("current_revision").filter(
            pk__in=ids, current_revision__isnull=False
        )
    }
    products = [found[i] for i in ids if i in found]
    try:
        c = compare(products)
    except ComparisonError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        {
            "products": ProductSummarySerializer(products, many=True).data,
            "warnings": c.warnings,
            "nutriscore": c.nutriscore,
            "nutriscore_note": c.nutriscore_note,
            "nova": c.nova,
            "groups": [
                {
                    "basis": g.basis,
                    "preparation": g.preparation,
                    "comparable": g.comparable,
                    "missing_for": g.missing_for,
                    "rows": [
                        {
                            "nutrient": r.nutrient,
                            "values": [
                                {
                                    "value": str(cell.value) if cell.value is not None else None,
                                    "state": cell.state,
                                }
                                for cell in r.cells
                            ],
                        }
                        for r in g.rows
                    ],
                }
                for g in c.groups
            ],
            "differences": c.differences,
        }
    )
