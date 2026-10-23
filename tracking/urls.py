from django.urls import path

from . import views

urlpatterns = [
    path("events/", views.event_list, name="event_list"),
    path("events/new/", views.event_create, name="event_create"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/edit/", views.event_edit, name="event_edit"),
    path("events/<int:pk>/restart/", views.event_restart, name="event_restart"),
    path("events/<int:pk>/snapshot/", views.staff_snapshot, name="staff_snapshot"),
    path("events/<int:pk>/open/", views.event_open, name="event_open"),
    path("events/<int:pk>/close/", views.event_close, name="event_close"),
    path("events/<int:pk>/rotate-link/", views.event_rotate_link, name="event_rotate_link"),
    path("events/<int:pk>/share-telegram/", views.event_share_telegram, name="event_share_telegram"),
    path(
        "events/<int:pk>/runners/<uuid:public_id>/name/",
        views.participant_rename,
        name="participant_rename",
    ),
    path(
        "events/<int:pk>/runners/<uuid:public_id>/laps/",
        views.participant_add_lap,
        name="participant_add_lap",
    ),
    path("my/", views.runner_dashboard, name="runner_dashboard"),
    path("my/join/", views.runner_join, name="runner_join"),
    path("my/events/<uuid:public_id>/leave/", views.runner_leave, name="runner_leave"),
    path("my/events/<uuid:public_id>/track/", views.runner_track, name="runner_track"),
    path("join/<str:token>/", views.join_event, name="join_event"),
    path("e/<str:token>/", views.spectator_detail, name="spectator_detail"),
    path("e/<str:token>/snapshot/", views.spectator_snapshot, name="spectator_snapshot"),
    path("telegram/webhook/", views.telegram_webhook, name="telegram_webhook"),
]
