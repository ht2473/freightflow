"""Маршруты аналитического модуля."""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.index, name="index"),
    path("typology/", views.typology, name="typology"),
    path("forecast/", views.forecast, name="forecast"),
    path("compare/", views.compare, name="compare"),
    path("scenario/", views.scenario, name="scenario"),
]
