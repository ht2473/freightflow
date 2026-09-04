"""Разбор грузового коридора: что лежит вдоль трассы.

Федеральные трассы входят в город как коридоры ввоза и вывоза, и вопрос
о каждой из них один: что вдоль неё расположено, какие округа она проходит,
в какие зоны ограничения заводит и что мешает движению сегодня.

Полоса разбора задаётся расстоянием до оси коридора. Прямая линия, а не
подъездной путь: связь площадки с трассой определяется тем, насколько
короток съезд, а его длина по графу зависит от развязок, которых в реестре
нет. Величина названа расстоянием по прямой и в этом качестве и приводится.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import permits
from core.models import (
    CargoRoute,
    District,
    InfrastructureObject,
    TrafficIncident,
)
from django.utils.translation import gettext_lazy as _
from geo.geometry import distance_to_polyline_km, haversine_km

#: Ширина полосы разбора по умолчанию, км.
DEFAULT_BAND_KM = 3.0

#: Допустимые значения ширины полосы. Перечень закрыт: разбор перебирает
#: реестр целиком, и произвольное значение из запроса открыло бы дорогу
#: расчётам, которые никто не заказывал.
BAND_OPTIONS: tuple[float, ...] = (1.0, 3.0, 5.0, 10.0)

#: Шаг прореживания вершин оси коридора. Трасса размечена подробно, а полоса
#: измеряется километрами: каждая третья вершина сохраняет форму линии.
VERTEX_STEP = 3


@dataclass(frozen=True)
class Reach:
    """Участок коридора в границах одного округа."""

    district: District
    length_km: float
    object_count: int


def analyze(route: CargoRoute, band_km: float = DEFAULT_BAND_KM) -> dict:
    """Разобрать коридор: округа, инфраструктура, зоны, события."""
    if route.geom is None:
        return {"available": False, "reason": str(_("Геометрия коридора не загружена"))}

    axis = route.geom.points
    if len(axis) < 2:
        return {"available": False, "reason": str(_("Ось коридора состоит из одной точки"))}

    # Ось прореживается, но остаётся ломаной: расстояние измеряется до самой
    # линии, и потеря промежуточной вершины меняет его на доли её отклонения.
    probes = _thin(axis)

    nearby = _objects_in_band(probes, band_km)
    reaches = _reaches(axis, nearby)
    zones = permits.zones_along(axis)

    return {
        "available": True,
        "route": route,
        "band_km": band_km,
        "length_km": round(route.geom.length_km, 1),
        "objects": nearby,
        "object_count": len(nearby),
        "capacity_tons": _sum(nearby, "capacity_tons"),
        "area_sq_m": _sum(nearby, "area_sq_m"),
        "reaches": reaches,
        "zones": zones,
        "permit_zone": zones[-1] if zones else None,
        "incidents": _incidents_in_band(probes, band_km),
    }


def _objects_in_band(probes: list, band_km: float) -> list[InfrastructureObject]:
    """Объекты реестра, ось коридора от которых не далее полосы разбора."""
    found = []
    for obj in InfrastructureObject.objects.located().with_refs():
        distance = _distance_km(probes, obj.geom.lon, obj.geom.lat)
        if distance <= band_km:
            # Расстояние понадобится при выводе, и считать его второй раз
            # незачем: запись живёт до конца запроса.
            obj.corridor_distance_km = round(distance, 2)
            found.append(obj)
    found.sort(key=lambda item: item.corridor_distance_km)
    return found


def _incidents_in_band(probes: list, band_km: float) -> list[TrafficIncident]:
    """Открытые дорожные события в полосе разбора."""
    found = []
    for incident in TrafficIncident.objects.open().with_refs().exclude(geom__isnull=True):
        distance = _distance_km(probes, incident.geom.lon, incident.geom.lat)
        if distance <= band_km:
            incident.corridor_distance_km = round(distance, 2)
            found.append(incident)
    found.sort(key=lambda item: (-item.severity, item.corridor_distance_km))
    return found


def _reaches(axis: list, nearby: list[InfrastructureObject]) -> list[Reach]:
    """Протяжённость коридора и число объектов в разрезе округов.

    Звено ломаной относится к тому округу, в котором лежит его середина.
    Звено на границе двух округов достаётся одному из них целиком, но при
    длине звена в десятки метров и коридоре в десятки километров такая
    погрешность на итог не влияет.
    """
    boundaries = [
        (district, district.geom, district.geom.bounds)
        for district in District.objects.with_geometry().exclude(geom__isnull=True)
        if district.geom is not None
    ]
    if not boundaries:
        return []

    lengths: dict[int, float] = {}
    for start, end in zip(axis, axis[1:], strict=False):
        middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        district = _locate(boundaries, middle[0], middle[1])
        if district is None:
            continue
        lengths[district.pk] = lengths.get(district.pk, 0.0) + haversine_km(start, end)

    counts: dict[int, int] = {}
    for obj in nearby:
        counts[obj.district_id] = counts.get(obj.district_id, 0) + 1

    by_pk = {district.pk: district for district, _geom, _box in boundaries}
    reaches = [
        Reach(
            district=by_pk[pk],
            length_km=round(length, 1),
            object_count=counts.get(pk, 0),
        )
        for pk, length in lengths.items()
    ]
    reaches.sort(key=lambda item: -item.length_km)
    return reaches


def _locate(boundaries, lon: float, lat: float) -> District | None:
    """Округ, в границах которого лежит точка."""
    for district, geometry, (west, south, east, north) in boundaries:
        if west <= lon <= east and south <= lat <= north and geometry.contains(lon, lat):
            return district
    return None


def _thin(axis: list) -> list:
    """Проредить ось, сохранив её начало и конец."""
    points = axis[::VERTEX_STEP]
    if points[-1] != axis[-1]:
        points.append(axis[-1])
    return points


def _distance_km(axis: list, lon: float, lat: float) -> float:
    """Расстояние по прямой до оси коридора."""
    return distance_to_polyline_km(axis, lon, lat)


def _sum(rows, field: str) -> float | None:
    """Сумма измеренных значений; ``None``, если не измерено ни одно."""
    measured = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    return float(sum(measured)) if measured else None


def band_option(raw: str | None) -> float:
    """Привести ширину полосы к допустимому значению."""
    try:
        value = float((raw or "").strip().replace(",", "."))
    except ValueError:
        return DEFAULT_BAND_KM
    return value if value in BAND_OPTIONS else DEFAULT_BAND_KM


__all__ = [
    "BAND_OPTIONS",
    "DEFAULT_BAND_KM",
    "Reach",
    "analyze",
    "band_option",
]
