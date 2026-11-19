from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from catalog import api

urlpatterns = [
    path("lookup/", api.lookup_api, name="api-lookup"),
    path("products/", api.ProductListAPI.as_view(), name="api-products"),
    path("suggest/", api.suggest_api, name="api-suggest"),
    path("products/<uuid:pk>/", api.ProductDetailAPI.as_view(), name="api-product"),
    path("compare/", api.compare_api, name="api-compare"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerSplitView.as_view(url_name="api-schema"), name="api-docs"),
]
