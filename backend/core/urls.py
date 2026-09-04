"""Маршруты публичной части системы.

Адреса построены по принципу «раздел → сущность → идентификатор», понятны
человеку и устойчивы к изменениям: карточки адресуются числовым ключом,
списки — единым префиксом раздела.
"""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    # Информационные страницы.
    path("", views.home, name="home"),
    path("map/", views.map_page, name="map"),
    path("methodology/", views.methodology, name="methodology"),
    path("api-docs/", views.api_docs, name="api_docs"),
    path("help/", views.help_page, name="help"),
    path("about/", views.about, name="about"),
    path("sitemap/", views.sitemap_page, name="sitemap"),
    # Инфраструктура.
    path("objects/", views.object_list, name="object_list"),
    path("objects/<int:pk>/", views.object_detail, name="object_detail"),
    path("districts/", views.district_list, name="district_list"),
    path("districts/<int:pk>/", views.district_detail, name="district_detail"),
    path("types/", views.type_list, name="type_list"),
    # Дорожная сеть.
    path("roads/", views.road_list, name="road_list"),
    path("roads/<int:pk>/", views.road_detail, name="road_detail"),
    path("traffic/", views.traffic, name="traffic"),
    path("incidents/", views.incident_list, name="incident_list"),
    path("incidents/<int:pk>/", views.incident_detail, name="incident_detail"),
    # Грузопотоки.
    path("flows/", views.flow_overview, name="flow_overview"),
    path("routes/", views.route_list, name="route_list"),
    path("routes/<int:pk>/", views.route_detail, name="route_detail"),
    path("cargo/", views.cargo_list, name="cargo_list"),
    # Данные.
    path("sources/", views.source_list, name="source_list"),
    path("sources/<int:pk>/", views.source_detail, name="source_detail"),
    path("etl-log/", views.etl_log, name="etl_log"),
    # Векторные тайлы карты и описание их источника. Вынесены под общий
    # префикс, чтобы их можно было отдельно кешировать на обратном прокси.
    path("tiles/tiles.json", views.tilejson, name="map_tilejson"),
    path("tiles/<int:z>/<int:x>/<int:y>.pbf", views.vector_tile, name="map_tile"),
    # Слои карты (GeoJSON).
    path("layers/objects/", views.layer_objects, name="layer_objects"),
    path("layers/roads/", views.layer_roads, name="layer_roads"),
    path("layers/routes/", views.layer_routes, name="layer_routes"),
    path("layers/incidents/", views.layer_incidents, name="layer_incidents"),
    path("layers/districts/", views.layer_districts, name="layer_districts"),
    path("layers/nearby/", views.nearby_objects, name="layer_nearby"),
]
