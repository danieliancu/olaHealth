from django.urls import path

from myola import views_mytrack as v

urlpatterns = [
    path("my-ola/", v.my_ola, name="my-ola"),
    # Notes
    path("my-ola/notes/", v.note_create, name="notes-create"),
    path("my-ola/notes/search/", v.notes_search, name="notes-search"),
    path("my-ola/notes/<int:pk>/", v.note_item, name="note-item"),
    path("my-ola/notes/<int:pk>/edit/", v.note_edit, name="note-edit"),
    path("my-ola/notes/<int:pk>/delete/", v.note_delete, name="note-delete"),
    path("my-ola/notes/in-replies/", v.notes_in_replies, name="notes-in-replies"),
    # Memories
    path("my-ola/memories/", v.memory_add, name="memory-add"),
    path("my-ola/memories/confirm/", v.memory_confirm, name="memory-confirm"),
    path("my-ola/memories/<int:pk>/edit/", v.memory_edit, name="memory-edit"),
    path("my-ola/memories/<int:pk>/toggle/", v.memory_toggle, name="memory-toggle"),
    path("my-ola/memories/<int:pk>/delete/", v.memory_delete, name="memory-delete"),
    # Saved lists and data
    path("my-ola/saved/remove/", v.saved_remove, name="saved-remove"),
    path("my-ola/scans/clear/", v.scans_clear, name="scans-clear"),
    # Ask Foodo
    path("my-ola/chat/", v.chat_view, name="chat"),
    path("my-ola/chat/send/", v.chat_send, name="chat-send"),
    path("my-ola/chat/clear/", v.chat_clear, name="chat-clear"),
]
