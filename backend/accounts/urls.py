"""Маршруты личного кабинета."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("profile/", views.profile, name="profile"),
    path("favorites/", views.favorites, name="favorites"),
    path("favorites/toggle/", views.favorite_toggle, name="favorite_toggle"),
    path("views/", views.saved_views, name="saved_views"),
    path("views/<int:pk>/action/", views.saved_view_action, name="saved_view_action"),
    path("comparisons/", views.comparisons, name="comparisons"),
    path("exports/", views.exports, name="exports"),
    path("exports/<int:pk>/download/", views.export_download, name="export_download"),
    path("subscriptions/", views.subscriptions, name="subscriptions"),
    path("subscriptions/<int:pk>/delete/", views.subscription_delete, name="subscription_delete"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/open/", views.notification_open, name="notification_open"),
    path("notifications/read/", views.notifications_read, name="notifications_read"),
    path("activity/", views.activity, name="activity"),
    path("api-access/", views.api_access, name="api_access"),
]
