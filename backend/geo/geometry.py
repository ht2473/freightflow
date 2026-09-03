"""Лёгкие геометрические примитивы для работы с данными PostGIS.

Модуль реализует минимально необходимый набор операций над геометрией —
разбор и формирование WKT, преобразование в GeoJSON, расчёт габаритов и
центроида — без использования GDAL/GEOS и библиотеки ``django.contrib.gis``.

Мотивация решения (подробно — в docs/adr/0001-geometry-without-gdal.md):

* приложению требуются только хранение, отдача на карту и простые метрики,
  тогда как тяжёлые пространственные операции выполняет сама СУБД;
* отказ от GDAL снимает системные зависимости с прикладного сервера и
  существенно упрощает развёртывание на VPS и в CI;
* один и тот же код работает поверх PostgreSQL/PostGIS (промышленный контур)
  и SQLite (разработка, автотесты, демонстрационная сборка).

Все координаты хранятся и передаются в системе WGS-84 (EPSG:4326) в порядке
``(долгота, широта)`` — так же, как это принято в GeoJSON и PostGIS.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# Идентификатор системы координат по умолчанию: WGS-84, географические градусы.
DEFAULT_SRID = 4326

# Средний радиус Земли, км. Используется в формуле гаверсинуса.
EARTH_RADIUS_KM = 6371.0088

# Допустимые типы геометрии. Ограничение осознанное: предметная область
# оперирует точками (объекты, инциденты), линиями и наборами линий (дороги
# и маршруты, состоящие из разрозненных участков) и полигонами (границы
# округов, контуры объектов, зоны ограничения движения).
GEOM_TYPES = (
    "POINT",
    "LINESTRING",
    "MULTILINESTRING",
    "POLYGON",
    "MULTIPOLYGON",
)

_WKT_RE = re.compile(
    r"^\s*(?:SRID=(?P<srid>\d+);)?\s*(?P<type>[A-Z]+)\s*(?P<body>\(.*\))\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Число значащих знаков после запятой при выводе координат. Шесть знаков —
# около 11 см на экваторе, избыточная точность только раздувает ответы API.
COORD_PRECISION = 6


class GeometryError(ValueError):
    """Ошибка разбора или конструирования геометрии."""


def _fmt(value: float) -> str:
    """Отформатировать координату, убрав незначащие нули."""
    text = f"{value:.{COORD_PRECISION}f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True, slots=True)
class Geometry:
    """Неизменяемое представление геометрического объекта.

    Атрибуты:
        geom_type: тип геометрии в верхнем регистре (``POINT``, ``LINESTRING``…);
        coordinates: координаты в структуре GeoJSON — вложенные списки чисел;
        srid: код системы координат.
    """

    geom_type: str
    coordinates: Any
    srid: int = DEFAULT_SRID

    # ------------------------------------------------------------------ ввод

    @classmethod
    def from_wkt(cls, wkt: str) -> Geometry:
        """Разобрать геометрию из строки WKT либо EWKT (``SRID=4326;POINT(...)``)."""
        if not isinstance(wkt, str):
            raise GeometryError(f"Ожидалась строка WKT, получено {type(wkt)!r}")

        match = _WKT_RE.match(wkt)
        if not match:
            raise GeometryError(f"Некорректный WKT: {wkt[:64]!r}")

        geom_type = match.group("type").upper()
        if geom_type not in GEOM_TYPES:
            raise GeometryError(f"Неподдерживаемый тип геометрии: {geom_type}")

        srid = int(match.group("srid") or DEFAULT_SRID)
        coordinates = _parse_body(match.group("body"), geom_type)
        return cls(geom_type=geom_type, coordinates=coordinates, srid=srid)

    @classmethod
    def from_geojson(cls, data: dict | str, srid: int = DEFAULT_SRID) -> Geometry:
        """Собрать геометрию из объекта GeoJSON (или его JSON-представления)."""
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict) or "type" not in data:
            raise GeometryError("Ожидался объект GeoJSON с полем 'type'")

        # Допускается как сама геометрия, так и Feature-обёртка вокруг неё.
        if data["type"] == "Feature":
            data = data.get("geometry") or {}

        geom_type = str(data.get("type", "")).upper()
        if geom_type not in GEOM_TYPES:
            raise GeometryError(f"Неподдерживаемый тип геометрии: {geom_type}")
        return cls(geom_type=geom_type, coordinates=data.get("coordinates"), srid=srid)

    @classmethod
    def point(cls, lon: float, lat: float, srid: int = DEFAULT_SRID) -> Geometry:
        """Создать точку по паре координат (долгота, широта)."""
        return cls("POINT", [float(lon), float(lat)], srid)

    @classmethod
    def line(cls, points: Iterable[Sequence[float]], srid: int = DEFAULT_SRID) -> Geometry:
        """Создать ломаную по последовательности координатных пар."""
        coords = [[float(x), float(y)] for x, y in points]
        if len(coords) < 2:
            raise GeometryError("Ломаная требует минимум две точки")
        return cls("LINESTRING", coords, srid)

    # ----------------------------------------------------------------- вывод

    @property
    def wkt(self) -> str:
        """Строковое представление в формате WKT."""
        return f"{self.geom_type}({_render_body(self.coordinates, self.geom_type)})"

    @property
    def ewkt(self) -> str:
        """Расширенный WKT с указанием системы координат (диалект PostGIS)."""
        return f"SRID={self.srid};{self.wkt}"

    @property
    def geojson(self) -> dict:
        """Представление геометрии в виде словаря GeoJSON."""
        return {"type": _geojson_type(self.geom_type), "coordinates": self.coordinates}

    def as_feature(self, properties: dict | None = None) -> dict:
        """Обернуть геометрию в GeoJSON-объект Feature с атрибутами."""
        return {
            "type": "Feature",
            "geometry": self.geojson,
            "properties": properties or {},
        }

    # -------------------------------------------------------------- метрики

    @property
    def points(self) -> list[tuple[float, float]]:
        """Плоский список всех вершин геометрии."""
        return list(_iter_points(self.coordinates))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Габаритный прямоугольник ``(min_lon, min_lat, max_lon, max_lat)``."""
        pts = self.points
        if not pts:
            raise GeometryError("Геометрия не содержит координат")
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        return min(lons), min(lats), max(lons), max(lats)

    @property
    def centroid(self) -> tuple[float, float]:
        """Приближённый центр геометрии — среднее арифметическое вершин.

        Для точки возвращает её саму, для ломаной и полигона — центр масс
        вершин. Точность достаточна для позиционирования карты и подписей.
        """
        pts = self.points
        if not pts:
            raise GeometryError("Геометрия не содержит координат")
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )

    @property
    def length_km(self) -> float:
        """Длина по ортодромии, км. Для точки и полигона — ноль.

        У набора линий длины частей складываются: соединять концы соседних
        участков нельзя, они могут лежать в разных местах города.
        """
        if self.geom_type == "LINESTRING":
            return _polyline_length_km(self.coordinates)
        if self.geom_type == "MULTILINESTRING":
            return sum(_polyline_length_km(part) for part in self.coordinates)
        return 0.0

    def contains(self, lon: float, lat: float) -> bool:
        """Лежит ли точка внутри полигона.

        Применяется метод трассировки луча: из проверяемой точки мысленно
        проводится луч, и подсчитывается число пересечений с границей.
        Нечётное число означает, что точка внутри.

        Точка внутри внутреннего кольца считается лежащей вне полигона:
        пустота в контуре — не территория объекта.

        Расчёт ведётся в градусах, без перехода к проекции. Для отнесения
        объекта к административному округу этого достаточно: искажение
        проекции меняет форму, но не факт вхождения, а границы округов
        не пересекают меридиан 180°.
        """
        if self.geom_type == "POLYGON":
            return _polygon_contains(self.coordinates, lon, lat)
        if self.geom_type == "MULTIPOLYGON":
            return any(_polygon_contains(rings, lon, lat) for rings in self.coordinates)
        return False

    @property
    def area_sq_m(self) -> float:
        """Площадь полигона на сфере, квадратные метры.

        Расчёт ведётся по формуле сферического избытка и не требует выбора
        проекции: результат не зависит от того, в какой части города лежит
        объект, и остаётся верным для площадей любого размера — от контура
        склада до границ административного округа.

        Внутренние кольца (пустоты) вычитаются. Для точки и ломаной
        возвращается ноль: площади у них нет.
        """
        if self.geom_type == "POLYGON":
            rings = self.coordinates
            return _polygon_area_sq_m(rings)
        if self.geom_type == "MULTIPOLYGON":
            return sum(_polygon_area_sq_m(rings) for rings in self.coordinates)
        return 0.0

    @property
    def lon(self) -> float:
        """Долгота точки. Для линий и полигонов — долгота центроида."""
        return self.coordinates[0] if self.geom_type == "POINT" else self.centroid[0]

    @property
    def lat(self) -> float:
        """Широта точки. Для линий и полигонов — широта центроида."""
        return self.coordinates[1] if self.geom_type == "POINT" else self.centroid[1]

    def __str__(self) -> str:
        return self.wkt


# ---------------------------------------------------------------------------
#  Разбор и рендеринг тела WKT
# ---------------------------------------------------------------------------


def _parse_body(body: str, geom_type: str) -> Any:
    """Преобразовать скобочную часть WKT в координатную структуру GeoJSON."""
    depth_required = {
        "POINT": 0,
        "LINESTRING": 1,
        "MULTILINESTRING": 2,
        "POLYGON": 2,
        "MULTIPOLYGON": 3,
    }[geom_type]
    parsed = _parse_group(body.strip())
    # Точка в WKT записывается как POINT(x y) — одна пара без вложенности.
    if depth_required == 0:
        return parsed[0] if parsed and isinstance(parsed[0], list) else parsed
    return parsed


def _parse_group(text: str) -> Any:
    """Рекурсивно разобрать выражение вида ``((a b, c d), (…))``."""
    text = text.strip()
    if not text.startswith("(") or not text.endswith(")"):
        raise GeometryError(f"Ожидались скобки в выражении WKT: {text[:48]!r}")
    inner = text[1:-1].strip()

    if not inner.startswith("("):
        # Уровень координат: «37.6 55.7, 37.7 55.8».
        return [_parse_pair(chunk) for chunk in inner.split(",") if chunk.strip()]

    # Уровень группы: разбиваем по запятым верхнего уровня.
    return [_parse_group(part) for part in _split_top_level(inner)]


def _split_top_level(text: str) -> list[str]:
    """Разбить строку по запятым, игнорируя запятые внутри вложенных скобок."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_pair(chunk: str) -> list[float]:
    """Разобрать пару координат «долгота широта»."""
    values = chunk.replace(",", " ").split()
    if len(values) < 2:
        raise GeometryError(f"Ожидалась пара координат, получено {chunk!r}")
    try:
        return [float(values[0]), float(values[1])]
    except ValueError as exc:  # pragma: no cover — защита от повреждённых данных
        raise GeometryError(f"Нечисловые координаты: {chunk!r}") from exc


def _render_body(coordinates: Any, geom_type: str) -> str:
    """Собрать скобочную часть WKT из координатной структуры."""
    if geom_type == "POINT":
        return f"{_fmt(coordinates[0])} {_fmt(coordinates[1])}"
    return _render_group(coordinates)


def _render_group(node: Any) -> str:
    """Рекурсивно собрать вложенные координатные группы."""
    if node and isinstance(node[0], (int, float)):
        return f"{_fmt(node[0])} {_fmt(node[1])}"
    rendered = [_render_group(item) for item in node]
    if node and isinstance(node[0], list) and node[0] and isinstance(node[0][0], (int, float)):
        return ", ".join(rendered)
    return ", ".join(f"({item})" for item in rendered)


def _iter_points(node: Any):
    """Обойти координатную структуру и выдать все вершины."""
    if not node:
        return
    if isinstance(node[0], (int, float)):
        yield float(node[0]), float(node[1])
        return
    for item in node:
        yield from _iter_points(item)


def _geojson_type(geom_type: str) -> str:
    """Привести тип WKT к написанию, принятому в GeoJSON."""
    return {
        "POINT": "Point",
        "LINESTRING": "LineString",
        "MULTILINESTRING": "MultiLineString",
        "POLYGON": "Polygon",
        "MULTIPOLYGON": "MultiPolygon",
    }[geom_type]


# ---------------------------------------------------------------------------
#  Метрики на сфере
# ---------------------------------------------------------------------------


def _polyline_length_km(points: Sequence[Sequence[float]]) -> float:
    """Длина ломаной по ортодромии, км."""
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1))


def _ring_contains(ring: Sequence[Sequence[float]], lon: float, lat: float) -> bool:
    """Лежит ли точка внутри замкнутого кольца (метод трассировки луча)."""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        # Ребро пересекает горизонтальный луч, если его концы лежат по разные
        # стороны от широты точки. Несимметричное сравнение исключает двойной
        # счёт вершин, попавших ровно на луч.
        if (y1 > lat) != (y2 > lat):
            crossing_lon = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if crossing_lon > lon:
                inside = not inside
    return inside


def _polygon_contains(rings: Sequence[Sequence[Sequence[float]]],
                      lon: float, lat: float) -> bool:
    """Лежит ли точка внутри полигона с учётом внутренних колец."""
    if not rings or not _ring_contains(rings[0], lon, lat):
        return False
    return not any(_ring_contains(hole, lon, lat) for hole in rings[1:])


def _ring_area_sq_m(ring: Sequence[Sequence[float]]) -> float:
    """Площадь замкнутого кольца на сфере, квадратные метры.

    Применяется формула сферического избытка::

        S = R² / 2 · |Σ (λ_{i+1} − λ_i) · (2 + sin φ_i + sin φ_{i+1})|

    Здесь λ — долгота, φ — широта в радианах, R — средний радиус Земли.
    Формула не требует выбора проекции, поэтому результат одинаково верен
    для контура склада и для границ округа, а искажения проекции
    не накапливаются к краям города.

    Знак суммы зависит от направления обхода кольца, поэтому берётся модуль:
    внешние и внутренние кольца различаются по положению в структуре
    полигона, а не по направлению обхода.
    """
    if len(ring) < 4:
        # Кольцо замкнуто, поэтому у треугольника четыре вершины.
        # Меньшее число точек площади не образует.
        return 0.0

    radius_m = EARTH_RADIUS_KM * 1000.0
    total = 0.0
    for index in range(len(ring) - 1):
        lon1, lat1 = math.radians(ring[index][0]), math.radians(ring[index][1])
        lon2, lat2 = math.radians(ring[index + 1][0]), math.radians(ring[index + 1][1])
        total += (lon2 - lon1) * (2.0 + math.sin(lat1) + math.sin(lat2))

    return abs(total) * radius_m * radius_m / 2.0


def _polygon_area_sq_m(rings: Sequence[Sequence[Sequence[float]]]) -> float:
    """Площадь полигона: внешнее кольцо за вычетом внутренних."""
    if not rings:
        return 0.0
    outer = _ring_area_sq_m(rings[0])
    holes = sum(_ring_area_sq_m(ring) for ring in rings[1:])
    return max(outer - holes, 0.0)


def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    """Расстояние между двумя точками по ортодромии, километры.

    Аргументы — пары ``(долгота, широта)`` в градусах. Формула гаверсинуса
    даёт погрешность менее 0,5 % и полностью достаточна для городских
    расстояний, где сжатием эллипсоида можно пренебречь.
    """
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bbox_contains(bbox: Sequence[float], point: Sequence[float]) -> bool:
    """Проверить попадание точки в габаритный прямоугольник."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= point[0] <= max_lon and min_lat <= point[1] <= max_lat


def feature_collection(features: Iterable[dict]) -> dict:
    """Собрать коллекцию GeoJSON из отдельных объектов Feature."""
    items = list(features)
    return {"type": "FeatureCollection", "features": items, "count": len(items)}
