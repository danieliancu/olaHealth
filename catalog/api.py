"""JSON endpoints (reusable later by native apps). Same services as the HTML views."""

from __future__ import annotations

import uuid

from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
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
from core.coordination import check_request_limit, rate_limited

HTTP_FOR_ERROR = {
    "invalid_code": status.HTTP_400_BAD_REQUEST,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
    "upstream_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_response": status.HTTP_502_BAD_GATEWAY,
}

_CODE = inline_serializer(
    "LookupCode",
    {
        "original": serializers.CharField(),
        "format": serializers.CharField(),
        "kind": serializers.CharField(),
        "gtin14": serializers.CharField(allow_null=True),
        "off_code": serializers.CharField(allow_null=True),
    },
)
_LOOKUP = inline_serializer(
    "LookupResult",
    {
        "status": serializers.ChoiceField(["found", "not_found", "pending", "error"]),
        "error": serializers.CharField(allow_null=True),
        "message": serializers.CharField(),
        "code": _CODE,
        "product": ProductSummarySerializer(allow_null=True),
        "source": serializers.CharField(required=False),
        "synced_at": serializers.DateTimeField(required=False),
        "stale": serializers.BooleanField(),
        "refresh_scheduled": serializers.BooleanField(),
    },
)
_ERROR = inline_serializer(
    "Error",
    {
        "status": serializers.CharField(),
        "error": serializers.CharField(),
        "message": serializers.CharField(),
    },
)


@extend_schema(
    summary="Look up a barcode",
    description=(
        "Local catalogue first, then Open Food Facts. 202 means the product is being fetched: "
        "retry after `Retry-After` seconds."
    ),
    parameters=[
        OpenApiParameter("code", OpenApiTypes.STR, required=True, description="Barcode digits"),
        OpenApiParameter(
            "format",
            OpenApiTypes.STR,
            description="Barcode symbology as read by the scanner (EAN_13, UPC_A, ...) or "
            "`manual`. Not a response format.",
        ),
    ],
    responses={200: _LOOKUP, 202: _LOOKUP, 404: _LOOKUP, 400: _LOOKUP, 429: _ERROR, 503: _LOOKUP},
)
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


@method_decorator(rate_limited("api_read"), name="get")
class ProductListAPI(ListAPIView):
    serializer_class = ProductSummarySerializer

    def get_queryset(self):
        form = SearchForm(self.request.query_params or None)
        params = SearchParams(**form.cleaned_data) if form.is_valid() else SearchParams()
        return search_products(params)


@method_decorator(rate_limited("api_read"), name="get")
class ProductDetailAPI(RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    queryset = (
        Product.objects.filter(current_revision__isnull=False)
        .select_related("current_revision")
        .prefetch_related("images", "identifiers", "current_revision__assessments")
    )


@extend_schema(
    summary="Compare up to 3 products",
    parameters=[
        OpenApiParameter(
            "ids", OpenApiTypes.STR, required=True, description="Comma-separated product ids"
        )
    ],
    responses={
        200: inline_serializer(
            "Comparison",
            {
                "products": ProductSummarySerializer(many=True),
                "warnings": serializers.ListField(child=serializers.CharField()),
                "nutriscore": serializers.JSONField(),
                "nutriscore_note": serializers.CharField(allow_blank=True),
                "nova": serializers.JSONField(),
                "groups": serializers.JSONField(),
                "differences": serializers.JSONField(),
            },
        ),
        400: inline_serializer("CompareError", {"error": serializers.CharField()}),
        429: _ERROR,
    },
)
@api_view(["GET"])
@rate_limited("compare")
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


@extend_schema(
    summary="Search-as-you-type suggestions",
    parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="At least 2 characters")],
    responses={
        200: inline_serializer("Suggestions", {"results": ProductSummarySerializer(many=True)}),
        429: _ERROR,
    },
)
@api_view(["GET"])
@rate_limited("suggest")
def suggest_api(request: Request) -> Response:
    """Search-as-you-type over the local catalogue (never calls Open Food Facts)."""
    q = str(request.query_params.get("q", "")).strip()[:100]
    if len(q) < 2:
        return Response({"results": []})
    products = list(search_products(SearchParams(q=q))[:6])
    return Response({"results": ProductSummarySerializer(products, many=True).data})
