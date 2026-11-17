from django.urls import path

from contributions import views

urlpatterns = [
    path("start/", views.start, name="contribution-start"),
    path("staff/image/<uuid:image_id>/", views.staff_image, name="staff-image"),
    path(
        "staff/image/<uuid:image_id>/<str:variant>/", views.staff_image, name="staff-image-variant"
    ),
    path("<str:token>/", views.edit, name="contribution"),
    path("<str:token>/photos/", views.upload, name="contribution-upload"),
    path("<str:token>/photos/<uuid:image_id>/", views.draft_image, name="contribution-image"),
    path(
        "<str:token>/photos/<uuid:image_id>/delete/",
        views.delete_image,
        name="contribution-image-delete",
    ),
    path("<str:token>/ocr.json", views.ocr_status, name="contribution-ocr-status"),
    path("<str:token>/ocr/<int:run_id>/retry/", views.ocr_retry, name="contribution-ocr-retry"),
]
