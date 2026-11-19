from django.urls import include, path

from myola import views, views_account

# Each area has its own URL module and views module (see myola/views_*.py).
urlpatterns = [
    path("", include("myola.urls_today")),
    path("", include("myola.urls_profile")),
    path("", include("myola.urls_mytrack")),
    path("my-ola/delete-account/", views.delete_account, name="delete-account"),
    path("my-ola/download-my-data/", views_account.download_my_data, name="download-my-data"),
]
