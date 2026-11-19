from django.urls import path

from myola import views

urlpatterns = [
    path("start/", views.start, name="start"),
    path("today/", views.today, name="today"),
    path("today/add/<uuid:pk>/", views.add_food, name="add-food"),
    path("progress/", views.progress, name="progress"),
    path("my-ola/", views.my_ola, name="my-ola"),
]
