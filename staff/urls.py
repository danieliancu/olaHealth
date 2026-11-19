from django.urls import path, re_path

from staff import views

app_name = "staff"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("moderation/", views.contributions, name="contributions"),
    path("moderation/<uuid:pk>/", views.contribution_detail, name="contribution"),
    path("flags/", views.flags, name="flags"),
    path("flags/<int:pk>/", views.flag_detail, name="flag"),
    path("products/", views.products, name="products"),
    path("products/<uuid:pk>/", views.product_detail, name="product"),
    path("products/<uuid:pk>/refresh/", views.product_refresh, name="product-refresh"),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/delete/", views.user_delete, name="user-delete"),
    re_path(
        r"^users/(?P<pk>[0-9]+)/(?P<action>activate|deactivate|resend)/$",
        views.user_action,
        name="user-action",
    ),
    path("settings/", views.site_settings, name="settings"),
    path("audit/", views.audit, name="audit"),
]
