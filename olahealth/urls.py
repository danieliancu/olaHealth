from django.contrib import admin
from django.urls import include, path

from catalog import views as catalog_views
from contributions import views as contribution_views
from core import views as core_views

admin.site.site_header = "FoodoTrack moderation"
admin.site.site_title = "FoodoTrack admin"

urlpatterns = [
    path("", catalog_views.home, name="home"),
    path("scan/", catalog_views.scan, name="scan"),
    path("lookup/", catalog_views.lookup_view, name="lookup"),
    path("scan/photo/", catalog_views.scan_photo, name="scan-photo"),
    path("p/<uuid:pk>/", catalog_views.product_detail, name="product"),
    path("products/", catalog_views.product_list, name="products"),
    path("compare/", catalog_views.compare, name="compare"),
    path("saved/", catalog_views.saved, name="saved"),
    path("sources/", catalog_views.sources, name="sources"),
    path("sources/export/", catalog_views.export_download, name="export"),
    path("images/<uuid:pk>/", contribution_views.public_image, name="public-image"),
    path("images/off/<uuid:pk>/", catalog_views.off_image, name="off-image"),
    path("contribute/", include("contributions.urls")),
    path("", include("myola.urls")),
    path("api/v1/", include("catalog.api_urls")),
    path("admin/", admin.site.urls),
    path("healthz", core_views.healthz, name="healthz"),
    path("readyz", core_views.readyz, name="readyz"),
]
