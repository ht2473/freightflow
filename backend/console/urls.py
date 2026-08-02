"""Маршруты панели администратора системы."""

from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/action/", views.user_action, name="user_action"),
    path("references/", views.references, name="references"),
    path("feedback/", views.feedback, name="feedback"),
    path("feedback/<int:pk>/", views.feedback_detail, name="feedback_detail"),
    path("content/", views.content, name="content"),
    path("content/<int:pk>/action/", views.content_action, name="content_action"),
    path("quality/", views.quality, name="quality"),
    path("etl/", views.etl, name="etl"),
    path("cache/flush/", views.cache_flush, name="cache_flush"),
    path("audit/", views.audit, name="audit"),
    path("system/", views.system, name="system"),
]
