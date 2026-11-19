from django.urls import path

from myola import views

urlpatterns = [
    path("start/", views.start, name="start"),
    path("progress/", views.progress, name="progress"),
]
