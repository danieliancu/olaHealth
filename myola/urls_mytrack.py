from django.urls import path

from myola import views

urlpatterns = [
    path("my-ola/", views.my_ola, name="my-ola"),
]
