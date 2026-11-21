from django.urls import path

from myola import views_mytrack as v
from myola import views_notifications as n

urlpatterns = [
    path("ask-foodo/", v.my_ola, name="ask-foodo"),
    # Notes
    path("ask-foodo/notes/", v.note_create, name="notes-create"),
    path("ask-foodo/notes/search/", v.notes_search, name="notes-search"),
    path("ask-foodo/notes/<int:pk>/", v.note_item, name="note-item"),
    path("ask-foodo/notes/<int:pk>/edit/", v.note_edit, name="note-edit"),
    path("ask-foodo/notes/<int:pk>/delete/", v.note_delete, name="note-delete"),
    path("ask-foodo/notes/in-replies/", v.notes_in_replies, name="notes-in-replies"),
    # Memories
    path("ask-foodo/memories/", v.memory_add, name="memory-add"),
    path("ask-foodo/memories/confirm/", v.memory_confirm, name="memory-confirm"),
    path("ask-foodo/memories/<int:pk>/edit/", v.memory_edit, name="memory-edit"),
    path("ask-foodo/memories/<int:pk>/toggle/", v.memory_toggle, name="memory-toggle"),
    path("ask-foodo/memories/<int:pk>/delete/", v.memory_delete, name="memory-delete"),
    # Saved lists and data
    path("ask-foodo/saved/remove/", v.saved_remove, name="saved-remove"),
    path("ask-foodo/scans/clear/", v.scans_clear, name="scans-clear"),
    # Ask Foodo
    path("ask-foodo/chat/", v.chat_view, name="chat"),
    path("ask-foodo/chat/send/", v.chat_send, name="chat-send"),
    path("ask-foodo/chat/clear/", v.chat_clear, name="chat-clear"),
    path("ask-foodo/chat/rate/<int:pk>/", v.chat_rate, name="chat-rate"),
    # Notifications
    path("notifications/", n.notifications, name="notifications"),
    path("notifications/<int:pk>/open/", n.notification_open, name="notification-open"),
    path("notifications/<int:pk>/read/", n.notification_read, name="notification-read"),
    path("notifications/read-all/", n.notifications_read_all, name="notifications-read-all"),
]
