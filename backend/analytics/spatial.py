"""Пространственный анализ размещения логистической инфраструктуры.

Модуль отвечает на вопрос, которого не видно ни в одной таблице реестра:
как объекты распределены по территории города и остаются ли места, откуда
до ближайшего склада далеко.

Территория покрывается регулярной сеткой, и для центра каждой ячейки
отыскивается расстояние до ближайшего объекта реестра. Отсюда получаются
три величины: доля территории в пределах заданного радиуса, обеспеченность
округа и перечень зон, где обеспеченности нет.

**Расстояние измеряется по прямой.** Это верхняя оценка доступности:
по улицам путь всегда длиннее, а на сколько именно — зависит от связности
сети, которая в системе пока не представлена. Величина названа так, как
она получена, и выдавать её за время подъезда нельзя.

Расчёт ведётся средствами модуля ``geo``: прикладной код не должен знать,
PostGIS под ним или SQLite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.models import District, InfrastructureObject
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from geo.geometry import haversine_km

#: Сторона ячейки сетки, км.
#:
#: Четыре квадратных километра — подробнее округа и грубее отдельного
#: объекта. Меньший шаг увеличивает объём расчёта вчетверо на каждое
#: деление, ничего не добавляя: реестр знает положение объекта с точностью
#: до его контура, а не до сотни метров.
GRID_STEP_KM = 2.0

#: Радиус обслуживания по умолчанию, км.
#:
#: Три километра по прямой отвечают примерно четырём километрам пути
#: по улицам — таково типичное отношение между ними в городской застройке, —
#: то есть порядка десяти минут хода гружёного транспорта.
DEFAULT_RADIUS_KM = 3.0

#: Радиусы, доступные к выбору.
RADIUS_OPTIONS: tuple[float, ...] = (2.0, 3.0, 5.0, 10.0)

#: Длина дуги в один градус широты, км. Долгота сжимается косинусом широты.
DEGREE_KM = 111.32


@dataclass(frozen=True)
class Cell:
    """Ячейка сетки: положение центра, округ и расстояние до объекта."""

    lon: float
    lat: float
    district_id: int
    distance_km: float


def _district_index() -> list[tuple[District, object, tuple[float, float, float, float]]]:
    """Округа с границами и их габаритами.

    Габариты проверяются перед точным вхождением: ячейка, лежащая вне
    прямоугольника округа, заведомо вне его границы, и обход тысячи вершин
    для неё не нужен.
    """
    return [
        (district, district.geom, district.geom.bounds)
        for district in District.objects.with_geometry().exclude(geom__isnull=True)
        if district.geom is not None
    ]


def _object_points() -> list[tuple[float, float]]:
    """Координаты объектов реестра."""
    points = []
    for geom in InfrastructureObject.objects.exclude(geom__isnull=True).values_list(
        "geom", flat=True
    ):
        if geom is not None:
            points.append(geom.coordinates)
    return points


def _covering_bounds(index) -> tuple[float, float, float, float] | None:
    """Габариты города — объединение габаритов округов."""
    if not index:
        return None
    boxes = [bounds for _, _, bounds in index]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def build_grid(step_km: float = GRID_STEP_KM) -> list[Cell]:
    """Построить сетку по территории города с расстояниями до объектов.

    В сетку входят только ячейки, центр которых лежит в границах какого-либо
    округа: прямоугольник габаритов захватывает и область за городом,
    а обеспеченность считается по городу.
    """
    index = _district_index()
    bounds = _covering_bounds(index)
    if bounds is None:
        return []

    points = _object_points()
    west, south, east, north = bounds
    lat_step = step_km / DEGREE_KM
    # Шаг по долготе зависит от широты: на широте Москвы градус долготы
    # короче градуса широты почти вдвое, и одинаковый шаг дал бы вытянутые
    # ячейки, а вместе с ними — смещённую оценку площади.
    lon_step = step_km / (DEGREE_KM * math.cos(math.radians((south + north) / 2)))

    cells: list[Cell] = []
    lat = south + lat_step / 2
    while lat < north:
        lon = west + lon_step / 2
        while lon < east:
            district = _locate(index, lon, lat)
            if district is not None:
                cells.append(
                    Cell(
                        lon=round(lon, 5),
                        lat=round(lat, 5),
                        district_id=district.id,
                        distance_km=_nearest_distance(points, lon, lat),
                    )
                )
            lon += lon_step
        lat += lat_step
    return cells


def _locate(index, lon: float, lat: float) -> District | None:
    """Округ, в границах которого лежит точка."""
    for district, boundary, (west, south, east, north) in index:
        if west <= lon <= east and south <= lat <= north and boundary.contains(lon, lat):
            return district
    return None


def _nearest_distance(points: list[tuple[float, float]], lon: float, lat: float) -> float:
    """Расстояние до ближайшего объекта, км.

    Перебор ведётся по всем объектам: их около тысячи, и на сетке города
    это несколько миллионов сравнений — заметно меньше, чем стоило бы
    построение и обход пространственного индекса ради однократного расчёта.
    """
    if not points:
        return math.inf
    return min(haversine_km((lon, lat), point) for point in points)


def coverage(radius_km: float = DEFAULT_RADIUS_KM, step_km: float = GRID_STEP_KM) -> dict:
    """Оценить обеспеченность территории объектами инфраструктуры.

    Возвращает долю территории в пределах радиуса, разрез по округам
    и перечень необеспеченных ячеек, упорядоченный по удалённости.
    """

    def build() -> dict:
        cells = build_grid(step_km)
        if not cells:
            return {"available": False, "districts": [], "gaps": []}

        cell_area = step_km**2
        names = {district.id: district for district in District.objects.all()}

        by_district: dict[int, list[Cell]] = {}
        for cell in cells:
            by_district.setdefault(cell.district_id, []).append(cell)

        districts = []
        for district_id, group in by_district.items():
            distances = [cell.distance_km for cell in group if cell.distance_km < math.inf]
            covered = sum(1 for cell in group if cell.distance_km <= radius_km)
            districts.append(
                {
                    "district": names.get(district_id),
                    "cells": len(group),
                    "area_sq_km": round(len(group) * cell_area, 1),
                    "covered": covered,
                    "share": round(covered / len(group) * 100, 1),
                    "mean_distance": (
                        round(sum(distances) / len(distances), 2) if distances else None
                    ),
                    "max_distance": round(max(distances), 2) if distances else None,
                }
            )
        districts.sort(key=lambda row: row["share"])

        gaps = sorted(
            (cell for cell in cells if cell.distance_km > radius_km),
            key=lambda cell: cell.distance_km,
            reverse=True,
        )
        covered = len(cells) - len(gaps)
        return {
            "available": True,
            "radius_km": radius_km,
            "step_km": step_km,
            "cells": len(cells),
            "cell_area_sq_km": cell_area,
            "area_sq_km": round(len(cells) * cell_area, 1),
            "covered": covered,
            "share": round(covered / len(cells) * 100, 1),
            "gap_area_sq_km": round(len(gaps) * cell_area, 1),
            "districts": districts,
            "gaps": [
                {
                    "lon": cell.lon,
                    "lat": cell.lat,
                    "district": names.get(cell.district_id),
                    "distance_km": round(cell.distance_km, 2),
                }
                for cell in gaps[:20]
            ],
            "gap_count": len(gaps),
            "mean_distance": round(
                sum(cell.distance_km for cell in cells if cell.distance_km < math.inf)
                / len(cells),
                2,
            ),
        }

    return _cached(f"analytics:coverage:{radius_km}:{step_km}", build)


def accessibility_layer(radius_km: float = DEFAULT_RADIUS_KM) -> dict:
    """Слой доступности: ячейки сетки с расстоянием до ближайшего объекта.

    Отдаётся точками, а не квадратами: на карте ячейка обозначается кружком
    заданного цвета, и передавать по четыре угла на каждую значило бы
    вчетверо увеличить ответ ради формы, которой на экране не видно.
    """
    cells = build_grid()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [cell.lon, cell.lat]},
            "properties": {
                "distance_km": round(cell.distance_km, 2),
                "covered": cell.distance_km <= radius_km,
                "district": cell.district_id,
            },
        }
        for cell in cells
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "radius_km": radius_km,
    }


#: Оттенки картограммы от наименьшего значения показателя к наибольшему.
#:
#: Шкала последовательная, а не семафорная: величина показателя означает
#: «больше или меньше», а не «хорошо или плохо», и красный цвет на её конце
#: подсказывал бы оценку, которой в данных нет.
CHOROPLETH_SCALE: tuple[str, ...] = (
    "#1b2b38",
    "#274a5c",
    "#356b78",
    "#4f8f83",
    "#87b077",
    "#d0c56a",
)


def choropleth(values: dict[int, float | None], simplify_every: int = 4) -> dict:
    """Подготовить картограмму округов по заданному показателю.

    Границы проецируются на плоскость и приводятся к системе координат
    рисунка. Проекция равнопромежуточная с поправкой на широту: город
    занимает четверть градуса по широте, и на таком протяжении искажение
    формы неразличимо, тогда как без поправки Москва оказалась бы
    вытянутой вдвое.

    Округ без измеренного значения остаётся незакрашенным: цвет из шкалы
    означал бы, что величина известна.
    """
    index = _district_index()
    bounds = _covering_bounds(index)
    if bounds is None:
        return {"available": False, "shapes": []}

    west, south, east, north = bounds
    scale_lon = math.cos(math.radians((south + north) / 2))
    width = (east - west) * scale_lon
    height = north - south
    # Рисунок вписывается в тысячу единиц по большей стороне: разметка
    # масштабирует его сама, а целые числа читаемее в исходном коде страницы.
    span = max(width, height) or 1.0
    size = 1000.0

    measured = [value for value in values.values() if value is not None]
    low, high = (min(measured), max(measured)) if measured else (0.0, 0.0)

    shapes = []
    for district, geometry, _bounds in index:
        value = values.get(district.id)
        rings = []
        for polygon in geometry.coordinates:
            for ring in polygon:
                thinned = ring[::simplify_every]
                if thinned[-1] != ring[-1]:
                    thinned = [*thinned, ring[-1]]
                # Прореживание рассчитано на контур в тысячи вершин.
                # Малый контур оно вырождает в отрезок, поэтому такой
                # берётся целиком: подробность его и без того невелика.
                if len(thinned) < 4:
                    thinned = ring
                if len(thinned) < 3:
                    continue
                points = " ".join(
                    f"{(lon - west) * scale_lon / span * size:.1f},{(north - lat) / span * size:.1f}"
                    for lon, lat in thinned
                )
                rings.append(points)
        if not rings:
            continue
        shapes.append(
            {
                "district": district,
                "value": value,
                "rings": rings,
                "fill": _shade(value, low, high),
                "label": _label_position(geometry, west, north, scale_lon, span, size),
            }
        )

    return {
        "available": True,
        "shapes": shapes,
        "width": round(width / span * size, 1),
        "height": round(height / span * size, 1),
        "low": low,
        "high": high,
        "scale": CHOROPLETH_SCALE,
    }


def _shade(value: float | None, low: float, high: float) -> str:
    """Цвет ячейки картограммы по величине показателя."""
    if value is None:
        return "none"
    if math.isclose(low, high):
        return CHOROPLETH_SCALE[len(CHOROPLETH_SCALE) // 2]
    position = (value - low) / (high - low)
    step = min(int(position * len(CHOROPLETH_SCALE)), len(CHOROPLETH_SCALE) - 1)
    return CHOROPLETH_SCALE[step]


def _label_position(geometry, west: float, north: float, scale_lon: float,
                    span: float, size: float) -> tuple[float, float]:
    """Положение подписи округа в координатах рисунка."""
    lon, lat = geometry.centroid
    return (
        round((lon - west) * scale_lon / span * size, 1),
        round((north - lat) / span * size, 1),
    )


#: Показатели, доступные к отображению на картограмме.
CHOROPLETH_METRICS: dict[str, str] = {
    "score": _("Индекс логистической нагрузки"),
    "storage": _("Концентрация складских площадей"),
    "network": _("Обеспеченность магистральной сетью"),
    "restrictions": _("Помехи движению"),
    "residential": _("Плотность жилой застройки"),
    "objects": _("Объектов инфраструктуры"),
}


def metric_values(code: str) -> tuple[dict[int, float | None], str]:
    """Значения выбранного показателя по округам и его единица измерения."""
    from . import services

    rows = services.load_index()
    if code == "score":
        return {row["district"].id: row["score"] for row in rows}, str(_("баллов"))
    if code == "objects":
        return (
            {row["district"].id: float(row["object_count"]) for row in rows},
            str(_("объектов")),
        )
    component = services.COMPONENT_BY_KEY.get(code)
    if component is None:
        return {row["district"].id: row["score"] for row in rows}, str(_("баллов"))
    return (
        {row["district"].id: row["raw"].get(code) for row in rows},
        str(component.unit),
    )


def _cached(key: str, builder):
    """Кешировать результат расчёта на срок, заданный настройками."""
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, settings.ANALYTICS_CACHE_TTL)
    return value


def invalidate() -> None:
    """Сбросить кеш пространственных расчётов."""
    for radius in RADIUS_OPTIONS:
        cache.delete(f"analytics:coverage:{radius}:{GRID_STEP_KM}")


__all__ = [
    "CHOROPLETH_METRICS",
    "DEFAULT_RADIUS_KM",
    "GRID_STEP_KM",
    "RADIUS_OPTIONS",
    "accessibility_layer",
    "build_grid",
    "choropleth",
    "coverage",
    "invalidate",
    "metric_values",
]
