from django.urls import include, path

from myola import views

# Each area has its own URL module and views module (see myola/views_*.py).
urlpatterns = [
    path("", include("myola.urls_today")),
    path("", include("myola.urls_profile")),
    path("", include("myola.urls_mytrack")),
    path("my-ola/delete-account/", views.delete_account, name="delete-account"),
]
