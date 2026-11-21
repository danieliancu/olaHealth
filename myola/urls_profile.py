from django.urls import path

from myola import views_profile as views

urlpatterns = [
    path("start/", views.start, name="start"),
    path("ask-foodo/profile/", views.profile_edit, name="profile-edit"),
    path(
        "ask-foodo/profile/clear-health/", views.profile_clear_health, name="profile-clear-health"
    ),
    path("ask-foodo/settings/", views.settings_edit, name="settings-edit"),
    path("ask-foodo/settings/targets/", views.targets_calculate, name="targets-calculate"),
    path("ask-foodo/settings/targets/save/", views.targets_save, name="targets-save"),
    path("progress/", views.progress, name="progress"),
    path("progress/weight/", views.weight_save, name="progress-weight"),
    path("progress/weight/<int:pk>/delete/", views.weight_delete, name="progress-weight-delete"),
]
