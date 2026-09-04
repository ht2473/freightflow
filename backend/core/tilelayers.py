"""Состав векторных слоёв карты.

Реестр в одном месте: и тайл, и его описание (TileJSON), и клиентский стиль
читают один перечень, поэтому разойтись не могут — слой, отданный сервером,
всегда описан и всегда нарисован.

За каждым слоем закреплён наименьший масштаб, начиная с которого он попадает
в тайл. Ограничение содержательное, а не техническое: реестр из тысячи
складов на обзорном масштабе города превращается в сплошное пятно, из
которого ничего не прочитать, а дорожные события различимы только тогда,
когда видна улица.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from django.conf import settings
from geo import TileFeature
from geo.queries import in_bbox

from . import selectors
from .choices import congestion_state
from .models import (
    CargoRoute,
    District,
    InfrastructureObject,
    RestrictionZone,
    RoadSegment,
    TrafficIncident,
)


def _number(value) -> float | None:
    """Привести десятичную величину к числу, сохранив «не измерено»."""
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class TileLayer:
    """Слой тайла: имя, масштабы существования и способ наполнения.

    Атрибуты:
        name: имя слоя внутри тайла — по нему клиент находит его в стиле;
        title: название слоя для интерфейса;
        min_zoom: наименьший масштаб, на котором слой имеет смысл;
        fields: состав свойств объекта с пояснениями — попадает в TileJSON;
        build: сбор объектов слоя по прямоугольнику тайла.
    """

    name: str
    title: str
    min_zoom: int
    fields: dict[str, str]
    build: Callable[[Sequence[float]], list[TileFeature]]


# ---------------------------------------------------------------------------
#  Наполнение слоёв
# ---------------------------------------------------------------------------


def _districts(bounds: Sequence[float]) -> list[TileFeature]:
    """Округа с показателями: профиль территории и индекс нагрузки.

    Расчётное ядро подключается внутри функции: реестр слоёв описывает
    состав карты и не должен зависеть от аналитики на уровне импорта.
    """
    from analytics.services import load_index

    profiles = {row["district"].id: row for row in selectors.district_profiles()}
    index = {row["district"].id: row for row in load_index()}

    features = []
    queryset = District.objects.with_geometry().exclude(geom__isnull=True)
    for district in in_bbox(queryset, bounds):
        profile = profiles.get(district.id, {})
        scored = index.get(district.id, {})
        features.append(
            TileFeature(
                district.geom,
                {
                    "name": district.name,
                    "short_name": district.short_name,
                    "objects": profile.get("object_count"),
                    "capacity": _number(profile.get("capacity_tons")),
                    "congestion": _number(profile.get("congestion")),
                    "tone": profile.get("congestion_tone"),
                    "area_sq_km": _number(district.area_sq_km),
                    "population": district.population,
                    "index": scored.get("score"),
                    "rank": scored.get("rank"),
                },
                feature_id=district.id,
            )
        )
    return features


def _zones(bounds: Sequence[float]) -> list[TileFeature]:
    """Зоны ограничения движения грузового транспорта."""
    queryset = RestrictionZone.objects.exclude(geom__isnull=True)
    return [
        TileFeature(
            zone.geom,
            {
                "code": zone.code,
                "name": zone.name,
                "short_name": zone.short_name,
                "level": zone.level,
                "permit_from_tons": _number(zone.permit_required_from_tons),
                "seasonal_from_tons": _number(zone.seasonal_limit_tons),
                "eco_class": zone.min_ecological_class,
                "fine": zone.fine_rubles,
            },
            feature_id=zone.id,
        )
        for zone in in_bbox(queryset, bounds)
    ]


def _roads(bounds: Sequence[float]) -> list[TileFeature]:
    """Магистральная сеть с загруженностью и признаком грузового каркаса."""
    conditions = {item.road_id: item for item in selectors.latest_conditions()}
    queryset = RoadSegment.objects.exclude(geom__isnull=True).only(
        "id", "name", "road_class", "lanes", "length_km", "geom", "in_freight_frame"
    )

    features = []
    for road in in_bbox(queryset, bounds):
        condition = conditions.get(road.id)
        level = condition.congestion_level if condition else None
        code, label, tone = congestion_state(level)
        features.append(
            TileFeature(
                road.geom,
                {
                    "name": road.name,
                    "road_class": road.road_class,
                    "class_label": road.get_road_class_display(),
                    "lanes": road.lanes,
                    "length_km": _number(road.length_km),
                    "congestion": level,
                    "state": code,
                    "state_label": label,
                    "tone": tone,
                    "freight_frame": road.in_freight_frame,
                },
                feature_id=road.id,
            )
        )
    return features


def _routes(bounds: Sequence[float]) -> list[TileFeature]:
    """Федеральные грузовые коридоры."""
    queryset = CargoRoute.objects.exclude(geom__isnull=True)
    return [
        TileFeature(
            route.geom,
            {
                "name": route.name,
                "route_type": route.route_type,
                "type_label": route.get_route_type_display(),
                "distance_km": _number(route.distance_km),
                "trucks": route.truck_count_day,
            },
            feature_id=route.id,
        )
        for route in in_bbox(queryset, bounds)
    ]


def _objects(bounds: Sequence[float]) -> list[TileFeature]:
    """Объекты логистической инфраструктуры."""
    queryset = InfrastructureObject.objects.with_refs().located()
    return [
        TileFeature(
            obj.geom,
            {
                "name": obj.name,
                "type": obj.type.name,
                "type_code": obj.type.code,
                "district": obj.district.short_name,
                "address": obj.address,
                "area": _number(obj.area_sq_m),
                "capacity": _number(obj.capacity_tons),
                "hours": obj.operating_hours,
                "operator": obj.operator,
            },
            feature_id=obj.id,
        )
        for obj in in_bbox(queryset, bounds)
    ]


def _footprints(bounds: Sequence[float]) -> list[TileFeature]:
    """Контуры объектов — то, чем измерена их площадь.

    Слой появляется на крупных масштабах, где контур склада занимает
    заметную часть экрана: именно он, а не точка в его середине,
    показывает занятую территорию.
    """
    queryset = (
        InfrastructureObject.objects.select_related("type")
        .defer("district__geom")
        .exclude(footprint__isnull=True)
    )
    return [
        TileFeature(
            obj.footprint,
            {"name": obj.name, "type_code": obj.type.code, "area": _number(obj.area_sq_m)},
            feature_id=obj.id,
        )
        for obj in in_bbox(queryset, bounds, field="footprint")
    ]


def _incidents(bounds: Sequence[float]) -> list[TileFeature]:
    """Дорожные события: работы и происшествия на сети."""
    queryset = TrafficIncident.objects.with_refs().exclude(geom__isnull=True)
    features = []
    for incident in in_bbox(queryset, bounds):
        label, tone = incident.severity_state
        features.append(
            TileFeature(
                incident.geom,
                {
                    "type": incident.incident_type,
                    "type_label": incident.get_incident_type_display(),
                    "severity": incident.severity,
                    "severity_label": label,
                    "tone": tone,
                    "road": incident.road.name if incident.road_id else "",
                    "is_open": incident.is_open,
                    "affects_cargo": incident.affects_cargo,
                },
                feature_id=incident.id,
            )
        )
    return features


# ---------------------------------------------------------------------------
#  Реестр
# ---------------------------------------------------------------------------

LAYERS: tuple[TileLayer, ...] = (
    TileLayer(
        name="districts",
        title="Округа",
        min_zoom=0,
        fields={
            "name": "Наименование округа",
            "short_name": "Краткое наименование",
            "objects": "Число объектов инфраструктуры",
            "capacity": "Мощность хранения, т",
            "congestion": "Средняя загруженность сети, баллы",
            "area_sq_km": "Площадь, км²",
            "population": "Численность населения, чел.",
            "index": "Индекс логистической нагрузки, баллы",
            "rank": "Место по индексу",
        },
        build=_districts,
    ),
    TileLayer(
        name="zones",
        title="Зоны ограничения движения",
        min_zoom=0,
        fields={
            "code": "Код зоны",
            "name": "Наименование",
            "short_name": "Краткое наименование",
            "level": "Уровень вложенности",
            "permit_from_tons": "Пропуск требуется при РММ от, т",
            "seasonal_from_tons": "Сезонное ограничение при РММ от, т",
            "eco_class": "Наименьший экологический класс",
            "fine": "Штраф за нарушение, ₽",
        },
        build=_zones,
    ),
    TileLayer(
        name="roads",
        title="Магистральная сеть",
        min_zoom=7,
        fields={
            "name": "Наименование магистрали",
            "road_class": "Класс дороги",
            "class_label": "Класс дороги, название",
            "lanes": "Число полос",
            "length_km": "Протяжённость, км",
            "congestion": "Загруженность, баллы",
            "state": "Состояние движения",
            "state_label": "Состояние движения, название",
            "tone": "Тон отображения состояния",
            "freight_frame": "Входит в грузовой каркас",
        },
        build=_roads,
    ),
    TileLayer(
        name="routes",
        title="Грузовые коридоры",
        min_zoom=7,
        fields={
            "name": "Наименование коридора",
            "route_type": "Тип коридора",
            "type_label": "Тип коридора, название",
            "distance_km": "Протяжённость, км",
            "trucks": "Грузовых автомобилей в сутки",
        },
        build=_routes,
    ),
    TileLayer(
        name="objects",
        title="Объекты инфраструктуры",
        min_zoom=9,
        fields={
            "name": "Наименование объекта",
            "type": "Тип объекта",
            "type_code": "Код типа объекта",
            "district": "Округ",
            "address": "Адрес",
            "area": "Площадь, м²",
            "capacity": "Мощность хранения, т",
            "hours": "Режим работы",
            "operator": "Оператор",
        },
        build=_objects,
    ),
    TileLayer(
        name="incidents",
        title="Дорожные события",
        min_zoom=10,
        fields={
            "type": "Тип события",
            "type_label": "Тип события, название",
            "severity": "Уровень серьёзности",
            "severity_label": "Уровень серьёзности, название",
            "tone": "Тон отображения",
            "road": "Магистраль",
            "is_open": "Событие не закрыто",
            "affects_cargo": "Затрагивает грузовое движение",
        },
        build=_incidents,
    ),
    TileLayer(
        name="footprints",
        title="Контуры объектов",
        min_zoom=14,
        fields={
            "name": "Наименование объекта",
            "type_code": "Код типа объекта",
            "area": "Площадь, м²",
        },
        build=_footprints,
    ),
)

#: Наименьший и наибольший масштабы, на которых тайлы имеют содержание.
#: Ниже 5-го масштаба город не отличим от точки, выше 16-го дробить
#: собранные данные уже нечем — клиент растягивает последний тайл.
MIN_ZOOM = 5
MAX_ZOOM = 16


def layers_for_zoom(zoom: int) -> tuple[TileLayer, ...]:
    """Слои, попадающие в тайл заданного масштаба."""
    return tuple(layer for layer in LAYERS if zoom >= layer.min_zoom)


def intersects_city(bounds: Sequence[float]) -> bool:
    """Задевает ли прямоугольник тайла территорию города."""
    min_lon, min_lat, max_lon, max_lat = bounds
    city_min_lon, city_min_lat, city_max_lon, city_max_lat = settings.MAP_CITY_BOUNDS
    return (
        min_lon <= city_max_lon
        and max_lon >= city_min_lon
        and min_lat <= city_max_lat
        and max_lat >= city_min_lat
    )
