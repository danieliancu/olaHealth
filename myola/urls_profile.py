from django.urls import path

from myola import views_profile as views

urlpatterns = [
    path("start/", views.start, name="start"),
    path("my-ola/profile/", views.profile_edit, name="profile-edit"),
    path("my-ola/profile/clear-health/", views.profile_clear_health, name="profile-clear-health"),
    path("my-ola/settings/", views.settings_edit, name="settings-edit"),
    path("progress/", views.progress, name="progress"),
    path("progress/weight/", views.weight_save, name="progress-weight"),
    path("progress/weight/<int:pk>/delete/", views.weight_delete, name="progress-weight-delete"),
]
