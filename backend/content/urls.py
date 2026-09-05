"""Маршруты модуля информационного наполнения."""

from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("", views.article_list, name="article_list"),
    path("feedback/", views.feedback, name="feedback"),
    path("feedback/sent/", views.feedback_sent, name="feedback_sent"),
    path("<slug:slug>/", views.article_detail, name="article_detail"),
]
