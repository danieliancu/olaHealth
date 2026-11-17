from django.urls import path

from catalog import api

urlpatterns = [
    path("lookup/", api.lookup_api, name="api-lookup"),
    path("products/", api.ProductListAPI.as_view(), name="api-products"),
    path("products/<uuid:pk>/", api.ProductDetailAPI.as_view(), name="api-product"),
    path("compare/", api.compare_api, name="api-compare"),
]
