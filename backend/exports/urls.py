"""Маршруты подсистемы выгрузок."""

from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("create/", views.create, name="create"),
]
