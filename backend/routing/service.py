"""Расчёты по графу дорог: зоны доступности и маршруты.

Модуль переводит запросы предметной области — «куда доедет фура за
пятнадцать минут», «как проехать от терминала до склада и что для этого
требуется» — в обращения к службе маршрутизации и обратно, к величинам
и геометрии системы.

Расчёт ведётся по настоящему графу дорог, а не по расстоянию до точки:
пятнадцать минут хода различаются в разы в зависимости от того, выходит ли
выезд на магистраль или на улицу с ограничением массы. Там, где система
показывает расстояние по прямой, это сказано прямо.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from core import permits
from geo.geometry import Geometry

from . import polyline, profiles
from .client import RouterNotConfiguredError, RoutingClient, RoutingError, is_configured

#: Интервалы доступности по умолчанию, минуты. Пятнадцать минут — обычный
#: норматив подачи транспорта под погрузку внутри города, тридцать —
#: предел, за которым доставка перестаёт быть внутригородской.
DEFAULT_CONTOURS = (5, 15, 30)

#: Наибольшее число интервалов в одном запросе. Каждый контур — отдельный
#: обход графа, и десяток интервалов превращает расчёт в минутный.
MAX_CONTOURS = 5

#: Наибольшая длительность контура, минуты.
MAX_MINUTES = 60


@dataclass(frozen=True, slots=True)
class Isochrone:
    """Территория, достижимая за заданное время.

    Атрибуты:
        minutes: время хода, минуты;
        geometry: граница территории;
        area_sq_km: площадь территории.
    """

    minutes: int
    geometry: Geometry
    area_sq_km: float

    def as_feature(self) -> dict:
        """Представление для карты."""
        return self.geometry.as_feature(
            {"minutes": self.minutes, "area_sq_km": round(self.area_sq_km, 2)}
        )


@dataclass(frozen=True, slots=True)
class Route:
    """Маршрут грузового транспорта с условиями проезда.

    Атрибуты:
        geometry: ломаная маршрута;
        distance_km: протяжённость;
        duration_min: время в пути;
        steps: указания маршрута;
        verdict: условия проезда по зонам ограничения.
    """

    geometry: Geometry
    distance_km: float
    duration_min: float
    steps: list[dict]
    verdict: permits.Verdict

    def as_payload(self) -> dict:
        """Представление для клиентской части."""
        return {
            "geometry": self.geometry.geojson,
            "distance_km": round(self.distance_km, 2),
            "duration_min": round(self.duration_min, 1),
            "steps": self.steps,
            "zones": [zone.short_name for zone in self.verdict.zones],
            "permit": (
                self.verdict.required_permit.short_name
                if self.verdict.required_permit
                else None
            ),
            "fine_rubles": self.verdict.fine_rubles,
            "prohibitions": self.verdict.prohibitions,
            "notes": self.verdict.notes,
            "summary": self.verdict.summary(),
        }


def availability() -> dict:
    """Состояние службы маршрутизации.

    Возвращает то, что можно показать пользователю: настроена ли служба,
    отвечает ли она и что именно мешает расчёту.
    """
    if not is_configured():
        return {
            "configured": False,
            "reachable": False,
            "message": (
                "Служба маршрутизации не настроена: расчёт по графу дорог "
                "недоступен. Расстояния показываются по прямой."
            ),
        }

    try:
        status = RoutingClient().status()
    except RoutingError as error:
        return {"configured": True, "reachable": False, "message": str(error)}

    return {
        "configured": True,
        "reachable": True,
        "message": "Служба маршрутизации отвечает",
        "version": status.get("version", ""),
        "tileset_updated": status.get("tileset_last_modified"),
        "bbox": status.get("bbox"),
    }


def _contours(minutes: Sequence[int] | None) -> list[int]:
    """Привести перечень интервалов к допустимому виду."""
    values = sorted({int(m) for m in (minutes or DEFAULT_CONTOURS) if int(m) > 0})
    values = [m for m in values if m <= MAX_MINUTES]
    return values[:MAX_CONTOURS] or list(DEFAULT_CONTOURS)


def isochrones(
    lon: float,
    lat: float,
    minutes: Sequence[int] | None = None,
    profile: profiles.TruckProfile | None = None,
) -> list[Isochrone]:
    """Зоны доступности от точки за заданное время хода.

    Служба возвращает контуры от большего к меньшему; порядок сохраняется,
    поэтому на карте меньший контур ложится поверх большего.
    """
    truck = profile or profiles.get(None)
    payload = {
        "locations": [{"lat": float(lat), "lon": float(lon)}],
        "costing": "truck",
        "costing_options": truck.costing_options(),
        "contours": [{"time": value} for value in _contours(minutes)],
        "polygons": True,
        # Отсечение мелких лоскутов и сглаживание контура: без них граница
        # изохроны рассыпается на сотни отдельных многоугольников вокруг
        # каждого тупика, и читать её невозможно.
        "denoise": 0.4,
        "generalize": 60,
    }

    response = RoutingClient().isochrone(payload)
    result: list[Isochrone] = []
    for feature in response.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        shape = Geometry.from_geojson(geometry)
        result.append(
            Isochrone(
                minutes=int(feature.get("properties", {}).get("contour", 0)),
                geometry=shape,
                area_sq_km=shape.area_sq_m / 1_000_000,
            )
        )
    return result


def route(
    points: Sequence[Sequence[float]],
    profile: profiles.TruckProfile | None = None,
    moment: date | None = None,
) -> Route:
    """Маршрут между точками с проверкой условий проезда.

    Проверка ведётся по самому маршруту, а не по его концам: путь между
    двумя точками вне зон ограничения может проходить через центр города,
    и требования к транспортному средству определяет именно он.
    """
    truck = profile or profiles.get(None)
    payload = {
        "locations": [{"lat": float(lat), "lon": float(lon)} for lon, lat in points],
        "costing": "truck",
        "costing_options": truck.costing_options(),
        "directions_options": {"units": "kilometers", "language": "ru-RU"},
    }

    trip = RoutingClient().route(payload).get("trip") or {}
    legs = trip.get("legs") or []
    if not legs:
        raise RoutingError("Маршрут между указанными точками не найден")

    shape: list[list[float]] = []
    steps: list[dict] = []
    for leg in legs:
        shape.extend(polyline.decode(leg.get("shape", "")))
        for maneuver in leg.get("maneuvers") or []:
            steps.append(
                {
                    "instruction": maneuver.get("instruction", ""),
                    "distance_km": round(float(maneuver.get("length", 0)), 2),
                    "duration_min": round(float(maneuver.get("time", 0)) / 60, 1),
                }
            )

    if len(shape) < 2:
        raise RoutingError("Служба маршрутизации вернула маршрут без геометрии")

    summary = trip.get("summary") or {}
    verdict = permits.evaluate_route(
        permits.Vehicle(mass_tons=Decimal(truck.mass_tons)), shape, moment
    )

    return Route(
        geometry=Geometry("LINESTRING", shape),
        distance_km=float(summary.get("length", 0)),
        duration_min=float(summary.get("time", 0)) / 60,
        steps=steps,
        verdict=verdict,
    )


__all__ = [
    "DEFAULT_CONTOURS",
    "Isochrone",
    "Route",
    "RouterNotConfiguredError",
    "RoutingError",
    "availability",
    "isochrones",
    "route",
]
