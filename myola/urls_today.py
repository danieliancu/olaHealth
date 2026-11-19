from django.urls import path

from myola import views

urlpatterns = [
    path("today/", views.today, name="today"),
    path("today/add/<uuid:pk>/", views.add_food, name="add-food"),
]
