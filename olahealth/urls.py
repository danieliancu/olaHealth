from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from catalog import views as catalog_views
from contributions import views as contribution_views
from core import learn
from core import views as core_views
from core.sitemaps import SITEMAPS

admin.site.site_header = "AskFoodo moderation"
admin.site.site_title = "AskFoodo admin"

urlpatterns = [
    path("", catalog_views.home, name="home"),
    path("scan/", catalog_views.scan, name="scan"),
    path("lookup/", catalog_views.lookup_view, name="lookup"),
    path("scan/photo/", catalog_views.scan_photo, name="scan-photo"),
    path("p/<uuid:pk>/", catalog_views.product_detail, name="product"),
    path("p/<uuid:pk>/delete/", catalog_views.product_delete, name="product-delete"),
    path(
        "p/<uuid:pk>/alternatives/",
        catalog_views.product_alternatives,
        name="product-alternatives",
    ),
    path("p/<uuid:pk>/mark/<str:kind>/", catalog_views.product_mark, name="product-mark"),
    path("p/<uuid:pk>/note/", catalog_views.product_note, name="product-note"),
    path("products/", catalog_views.product_list, name="products"),
    path("products/suggest/", catalog_views.suggest_html, name="suggest-html"),
    path("compare/", catalog_views.compare, name="compare"),
    path("compare/add/<uuid:pk>/", catalog_views.compare_add, name="compare-add"),
    path("compare/remove/<uuid:pk>/", catalog_views.compare_remove, name="compare-remove"),
    path("compare/clear/", catalog_views.compare_clear, name="compare-clear"),
    path("saved/", catalog_views.saved, name="saved"),
    path("saved/clear/<str:what>/", catalog_views.saved_clear, name="saved-clear"),
    path("sources/", catalog_views.sources, name="sources"),
    path("sources/export/", catalog_views.export_download, name="export"),
    path("images/<uuid:pk>/", contribution_views.public_image, name="public-image"),
    path("images/off/<uuid:pk>/", catalog_views.off_image, name="off-image"),
    path("contribute/", include("contributions.urls")),
    path("accounts/", include("allauth.urls")),
    # Old addresses (/my-ola/...) keep working: permanent redirect to /ask-foodo/...
    re_path(
        r"^my-ola/(?P<rest>.*)$",
        RedirectView.as_view(url="/ask-foodo/%(rest)s", permanent=True, query_string=True),
    ),
    path("", include("myola.urls")),
    path("api/v1/", include("catalog.api_urls")),
    path("staff/", include("staff.urls")),
    path("admin/", admin.site.urls),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("learn/", learn.learn_index, name="learn"),
    path("learn/topic/<slug:key>/", learn.learn_topic, name="learn-topic"),
    path("learn/<slug:slug>/", learn.learn_article, name="learn-article"),
    path("learn/<slug:slug>/image/", learn.learn_image, name="learn-image"),
    path("privacy/", core_views.privacy, name="privacy"),
    path("terms/", core_views.terms, name="terms"),
    path("healthz", core_views.healthz, name="healthz"),
    path("readyz", core_views.readyz, name="readyz"),
]
