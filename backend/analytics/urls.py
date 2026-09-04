"""Маршруты аналитического модуля."""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.index, name="index"),
    path("sensitivity/", views.sensitivity, name="sensitivity"),
    path("typology/", views.typology, name="typology"),
    path("spatial/", views.spatial_analysis, name="spatial"),
    path("layers/accessibility/", views.layer_accessibility, name="layer_accessibility"),
    path("forecast/", views.forecast, name="forecast"),
    path("compare/", views.compare, name="compare"),
    path("scenario/", views.scenario, name="scenario"),
    path("siting/", views.site_selection, name="siting"),
    path("corridor/", views.corridor_analysis, name="corridor"),
]
