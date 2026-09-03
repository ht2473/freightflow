"""Интерактивная карта и обслуживающие её слои GeoJSON.

Страница карты отдаёт только каркас, а данные загружаются асинхронно
отдельными слоями. Такое разделение позволяет включать и выключать слои без
перезагрузки страницы и ограничивать объём передаваемых данных видимой
областью экрана.
"""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET
from geo import to_feature_collection
from geo.queries import in_bbox

from .. import selectors
from ..choices import IncidentType, RoadClass, RouteType, congestion_state
from ..models import (
    CargoRoute,
    District,
    InfrastructureObject,
    InfrastructureType,
    RoadSegment,
    TrafficIncident,
)
from .base import choice_param, int_param, page_context


def map_settings() -> dict:
    """Собрать настройки карты для передачи в клиентский сценарий.

    Словарь формируется здесь, а не в разметке. Подстановка чисел в шаблон
    проходит через локализацию: при русском языке координата ``55.7522``
    выводится как ``55,7522``, и встроенный в страницу JSON перестаёт быть
    валидным. Значения, собранные на стороне Python и переданные через
    ``json_script``, сериализуются средствами Python и локализации
    не подчиняются.
    """
    return {
        "center": [settings.MAP_DEFAULT_CENTER[0], settings.MAP_DEFAULT_CENTER[1]],
        "zoom": settings.MAP_DEFAULT_ZOOM,
        "tileUrl": settings.MAP_TILE_URL,
        "tileUrlDark": settings.MAP_TILE_URL_DARK,
        "attribution": settings.MAP_ATTRIBUTION,
        "maxFeatures": settings.MAP_MAX_FEATURES,
        "urls": {
            "objects": reverse("core:layer_objects"),
            "roads": reverse("core:layer_roads"),
            "routes": reverse("core:layer_routes"),
            "incidents": reverse("core:layer_incidents"),
            "districts": reverse("core:layer_districts"),
            "nearby": reverse("core:layer_nearby"),
        },
    }


def map_page(request):
    """Страница интерактивной карты логистической инфраструктуры."""
    context = page_context(
        request,
        title=_("Карта логистической инфраструктуры"),
        lead=_(
            "Пространственное распределение складских мощностей, магистралей "
            "и дорожных событий на территории Москвы."
        ),
        active="map",
        crumbs=[(_("Карта"),)],
        districts=District.objects.all(),
        types=InfrastructureType.objects.all(),
        road_classes=RoadClass.choices,
        route_types=RouteType.choices,
        incident_types=IncidentType.choices,
        summary=selectors.dashboard_summary(),
        max_features=settings.MAP_MAX_FEATURES,
        map_settings=map_settings(),
    )
    return render(request, "pages/map.html", context)


def _parse_bbox(request) -> list[float] | None:
    """Разобрать параметр ``bbox=minLon,minLat,maxLon,maxLat``."""
    raw = request.GET.get("bbox")
    if not raw:
        return None
    try:
        values = [float(part) for part in raw.split(",")]
    except ValueError:
        return None
    return values if len(values) == 4 else None


@require_GET
def layer_objects(request) -> JsonResponse:
    """Слой объектов инфраструктуры в формате GeoJSON."""
    queryset = InfrastructureObject.objects.with_refs().located()

    district_id = int_param(request, "district")
    type_id = int_param(request, "type")
    term = (request.GET.get("q") or "").strip()
    queryset = queryset.in_district(district_id).of_type(type_id).search(term)

    bbox = _parse_bbox(request)
    rows = in_bbox(queryset, bbox) if bbox else queryset
    # Ограничение накладывается до материализации: вызов list() над полной
    # выборкой создавал бы объекты модели для всех записей реестра, тогда
    # как на слой попадает лишь их часть. На выборке ORM срез превращается
    # в LIMIT, на списке (результат отбора по прямоугольнику на SQLite) —
    # в обычный срез.
    rows = list(rows[: settings.MAP_MAX_FEATURES])

    payload = to_feature_collection(
        rows,
        lambda obj: {
            "id": obj.id,
            "name": obj.name,
            "type": obj.type.name,
            "type_code": obj.type.code,
            "district": obj.district.short_name,
            "address": obj.address or "",
            "capacity": float(obj.capacity_tons) if obj.capacity_tons else None,
            "area": float(obj.area_sq_m) if obj.area_sq_m else None,
            "hours": obj.operating_hours or "",
            "url": obj.get_absolute_url(),
        },
    )
    return JsonResponse(payload)


@require_GET
def layer_roads(request) -> JsonResponse:
    """Слой участков дорожной сети с текущей загруженностью."""
    queryset = RoadSegment.objects.select_related("district").exclude(geom__isnull=True)

    road_class = choice_param(request, "class", RoadClass.values)
    if road_class:
        queryset = queryset.filter(road_class=road_class)

    conditions = {c.road_id: c for c in selectors.latest_conditions()}
    rows = list(queryset)

    def properties(road: RoadSegment) -> dict:
        condition = conditions.get(road.id)
        level = condition.congestion_level if condition else None
        code, label, tone = congestion_state(level)
        return {
            "id": road.id,
            "name": road.name,
            "road_class": road.get_road_class_display(),
            "lanes": road.lanes,
            "length": float(road.length_km) if road.length_km else None,
            "speed_limit": road.speed_limit_kmh,
            "congestion": level,
            "state": code,
            "state_label": label,
            "tone": tone,
            "speed": float(condition.avg_speed_kmh)
            if condition and condition.avg_speed_kmh
            else None,
            "url": road.get_absolute_url(),
        }

    return JsonResponse(to_feature_collection(rows, properties))


@require_GET
def layer_routes(request) -> JsonResponse:
    """Слой грузовых маршрутов."""
    queryset = CargoRoute.objects.exclude(geom__isnull=True)

    route_type = choice_param(request, "type", RouteType.values)
    if route_type:
        queryset = queryset.filter(route_type=route_type)

    rows = list(queryset[: settings.MAP_MAX_FEATURES])
    payload = to_feature_collection(
        rows,
        lambda route: {
            "id": route.id,
            "name": route.name,
            "route_type": route.route_type,
            "route_type_label": route.get_route_type_display(),
            "distance": float(route.distance_km) if route.distance_km else None,
            "trucks": route.truck_count_day,
            "url": route.get_absolute_url(),
        },
        # Маршруты передаются в упрощённом виде: детализация ломаной за
        # пределами видимого масштаба не влияет на восприятие коридора.
        simplify_every=2,
    )
    return JsonResponse(payload)


@require_GET
def layer_incidents(request) -> JsonResponse:
    """Слой дорожных инцидентов."""
    queryset = TrafficIncident.objects.with_refs().exclude(geom__isnull=True)

    incident_type = choice_param(request, "type", IncidentType.values)
    if incident_type:
        queryset = queryset.filter(incident_type=incident_type)
    if request.GET.get("state") == "open":
        queryset = queryset.filter(resolved_at__isnull=True)
    if request.GET.get("cargo") == "1":
        queryset = queryset.filter(affects_cargo=True)

    rows = list(queryset.order_by("-reported_at")[: settings.MAP_MAX_FEATURES])
    payload = to_feature_collection(
        rows,
        lambda incident: {
            "id": incident.id,
            "type": incident.incident_type,
            "type_label": incident.get_incident_type_display(),
            "severity": incident.severity,
            "severity_label": incident.severity_state[0],
            "tone": incident.severity_state[1],
            "road": incident.road.name if incident.road_id else "",
            "description": incident.description or "",
            "reported_at": incident.reported_at.isoformat(),
            "is_open": incident.is_open,
            "affects_cargo": incident.affects_cargo,
            "url": incident.get_absolute_url(),
        },
    )
    return JsonResponse(payload)


@require_GET
def layer_districts(request) -> JsonResponse:
    """Слой центров округов с агрегированными показателями.

    Границы округов в исходном наборе данных отсутствуют, поэтому округа
    отображаются метками в условных центрах с числовыми характеристиками.
    """
    features = []
    for profile in selectors.district_profiles():
        district = profile["district"]
        center = district.map_center
        if not center:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [center[0], center[1]]},
                "properties": {
                    "id": district.id,
                    "name": district.name,
                    "short_name": district.short_name,
                    "objects": profile["object_count"],
                    "capacity": profile["capacity_tons"],
                    "volume": profile["volume_tons"],
                    "congestion": profile["congestion"],
                    "tone": profile["congestion_tone"],
                    "url": district.get_absolute_url(),
                },
            }
        )
    return JsonResponse({"type": "FeatureCollection", "features": features, "count": len(features)})


@require_GET
def nearby_objects(request) -> JsonResponse:
    """Поиск объектов инфраструктуры вблизи произвольной точки.

    Используется инструментом карты «что рядом»: пользователь указывает точку,
    система возвращает ближайшие объекты с расстоянием до каждого.
    """
    from geo import nearest

    try:
        lon = float(request.GET.get("lon"))
        lat = float(request.GET.get("lat"))
    except (TypeError, ValueError):
        return JsonResponse({"error": _("Не указаны координаты точки")}, status=400)

    radius = min(float(request.GET.get("radius", 3)), 25.0)
    limit = min(int_param(request, "limit", 15) or 15, 50)

    queryset = InfrastructureObject.objects.with_refs().located()
    results = nearest(queryset, lon, lat, radius, limit)

    return JsonResponse(
        {
            "origin": {"lon": lon, "lat": lat},
            "radius_km": radius,
            "count": len(results),
            "results": [
                {
                    "id": obj.id,
                    "name": obj.name,
                    "type": obj.type.name,
                    "district": obj.district.short_name,
                    "address": obj.address or "",
                    "capacity": float(obj.capacity_tons) if obj.capacity_tons else None,
                    "distance_km": round(distance, 2),
                    "lon": obj.geom.lon,
                    "lat": obj.geom.lat,
                    "url": obj.get_absolute_url(),
                }
                for obj, distance in results
            ],
        }
    )
