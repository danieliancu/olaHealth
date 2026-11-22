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
    path(
        "products/<uuid:pk>/photos/<uuid:image_id>/toggle/",
        views.product_image_toggle,
        name="product-image-toggle",
    ),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/delete/", views.user_delete, name="user-delete"),
    re_path(
        r"^users/(?P<pk>[0-9]+)/(?P<action>activate|deactivate|resend)/$",
        views.user_action,
        name="user-action",
    ),
    path("settings/", views.site_settings, name="settings"),
    path("settings/ai-models/", views.ai_models, name="ai-models"),
    path("announcements/", views.announcements, name="announcements"),
    path("learn/", views.learn_articles, name="learn"),
    path("learn/new/", views.learn_article_edit, name="learn-new"),
    path("learn/<int:pk>/", views.learn_article_edit, name="learn-article"),
    path("learn/<int:pk>/delete/", views.learn_article_delete, name="learn-delete"),
    path("learn/<int:pk>/products/", views.learn_products_search, name="learn-products"),
    path("learn/<int:pk>/products/add/", views.learn_products_add, name="learn-products-add"),
    path(
        "learn/<int:pk>/products/<uuid:product_pk>/remove/",
        views.learn_products_remove,
        name="learn-products-remove",
    ),
    path("learn/topics/", views.learn_topics, name="learn-topics"),
    path("learn/cover/", views.learn_covers, name="learn-covers"),
    path("learn/cover/preview/", views.learn_cover_preview, name="learn-cover-preview"),
    path("learn/topics/<int:pk>/", views.learn_topics, name="learn-topic"),
    path("ai-answers/", views.ai_answers, name="ai-answers"),
    path("audit/", views.audit, name="audit"),
]
