"""Маршруты REST API версии 1."""

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("districts", views.DistrictViewSet, basename="district")
router.register("types", views.InfrastructureTypeViewSet, basename="type")
router.register("cargo-categories", views.CargoCategoryViewSet, basename="cargo-category")
router.register("sources", views.DataSourceViewSet, basename="source")
router.register("objects", views.InfrastructureObjectViewSet, basename="object")
router.register("roads", views.RoadSegmentViewSet, basename="road")
router.register("incidents", views.TrafficIncidentViewSet, basename="incident")
router.register("routes", views.CargoRouteViewSet, basename="route")
router.register("flows", views.FreightFlowViewSet, basename="flow")
router.register("etl-runs", views.EtlRunViewSet, basename="etl-run")

urlpatterns = [
    path("", include(router.urls)),
    # Функциональные конечные точки.
    path("traffic/current/", views.current_traffic, name="current-traffic"),
    path("analytics/load-index/", views.load_index, name="load-index"),
    path("analytics/forecast/", views.forecast, name="forecast"),
    path("analytics/typology/", views.typology, name="typology"),
    # Доступ по токену: опознание владельца и формирование отчётов.
    path("me/", views.whoami, name="whoami"),
    path("exports/<str:dataset>.<str:fmt>", views.export, name="export"),
    # Машиночитаемая спецификация и интерактивная документация.
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="v1:schema"), name="swagger"),
    path("redoc/", SpectacularRedocView.as_view(url_name="v1:schema"), name="redoc"),
]
