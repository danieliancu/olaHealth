from django.urls import path

from myola import views_today as v

urlpatterns = [
    path("today/", v.today, name="today"),
    path("today/entries/", v.entry_add, name="today-entry-add"),
    path("today/entries/<uuid:pk>/", v.entry, name="today-entry"),
    path("today/entries/<uuid:pk>/preview/", v.entry_preview, name="today-entry-preview"),
    path("today/entries/<uuid:pk>/delete/", v.entry_delete, name="today-entry-delete"),
    path("today/undo/", v.undo, name="today-undo"),
    path("today/water/", v.water_add, name="today-water-add"),
    path("today/water/<int:pk>/delete/", v.water_delete, name="today-water-delete"),
    path("today/activity/", v.activity_add, name="today-activity-add"),
    path("today/activity/<int:pk>/delete/", v.activity_delete, name="today-activity-delete"),
    path("today/add/<uuid:pk>/", v.add_food, name="add-food"),
    path("today/add/<uuid:pk>/preview/", v.add_food_preview, name="add-food-preview"),
]
