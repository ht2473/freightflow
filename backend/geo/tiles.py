"""Разбиение слоёв карты на тайлы.

Слой, отданный одним ответом, растёт вместе с реестром: полная выгрузка
дорожной сети — это полторы тысячи километров ломаных, и объём ответа не
зависит от того, смотрит пользователь на весь город или на один квартал.
Тайловая сетка снимает эту зависимость: клиент запрашивает только те
квадраты, которые видит, и ровно с той подробностью, которая на его
масштабе различима.

Модуль отвечает за геометрическую часть: переход от градусов к координатной
сетке тайла, прореживание вершин и обрезка по его границам. Двоичная
упаковка результата — в :mod:`geo.mvt`.

Сетка — общепринятая XYZ в проекции Меркатора: масштаб ``z`` делит мир на
``2^z`` квадратов по каждой оси, ``x`` растёт на восток, ``y`` — на юг.
Внутри тайла работает целочисленная сетка со стороной :data:`TILE_EXTENT`,
поэтому координаты не зависят от размера, в котором тайл будет нарисован.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .geometry import Geometry

#: Сторона координатной сетки тайла. Значение задано спецификацией Mapbox
#: Vector Tile и понимается всеми клиентами без дополнительных указаний.
TILE_EXTENT = 4096

#: Поля вокруг тайла, в единицах сетки. Линия, обрезанная точно по границе,
#: даёт разрыв в обводке на стыке соседних тайлов, а знак у самого края
#: пропадает: клиенту нужен запас за пределами видимого квадрата.
TILE_BUFFER = 64

#: Порог прореживания вершин, в единицах сетки. Тайл рисуется стороной
#: 512 экранных точек, то есть восемь единиц сетки составляют одну точку;
#: отклонение в половину точки на изображении неразличимо.
TILE_TOLERANCE = 4.0

#: Предел широты проекции Меркатора: у полюсов она обращается в
#: бесконечность, поэтому сетка обрывается симметрично около 85°.
MAX_LATITUDE = 85.05112878

#: Наибольший масштаб сетки. Число тайлов растёт как ``4^z``, а разрешение
#: сетки на 22-м масштабе составляет доли сантиметра — дальше наращивать
#: масштаб бессмысленно.
MAX_ZOOM = 22

#: Типы геометрии тайла в нумерации спецификации.
KIND_POINT = 1
KIND_LINE = 2
KIND_POLYGON = 3


class TileError(ValueError):
    """Запрошен тайл, которого не существует в сетке."""


@dataclass(frozen=True, slots=True)
class TileGeometry:
    """Геометрия, приведённая к целочисленной сетке одного тайла.

    Атрибуты:
        kind: тип геометрии в нумерации спецификации;
        parts: части — точки, ломаные либо кольца полигонов.
    """

    kind: int
    parts: tuple[tuple[tuple[int, int], ...], ...]


# ---------------------------------------------------------------------------
#  Сетка тайлов
# ---------------------------------------------------------------------------


def validate(z: int, x: int, y: int) -> None:
    """Проверить, что тайл принадлежит сетке.

    Номера приходят из адреса запроса, а за пределами сетки арифметика
    перехода к градусам даёт бессмысленный результат вместо отказа.
    """
    if not 0 <= z <= MAX_ZOOM:
        raise TileError(f"Масштаб вне диапазона 0…{MAX_ZOOM}: {z}")
    side = 1 << z
    if not (0 <= x < side and 0 <= y < side):
        raise TileError(f"Тайл {x}/{y} вне сетки масштаба {z}")


def _lat_to_norm(lat: float) -> float:
    """Широта → доля высоты мира, отсчитанная от северного края."""
    lat = max(-MAX_LATITUDE, min(MAX_LATITUDE, lat))
    sin_lat = math.sin(math.radians(lat))
    return 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)


def _norm_to_lat(norm: float) -> float:
    """Доля высоты мира → широта."""
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * norm))))


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Номер тайла, в который попадает точка на заданном масштабе."""
    side = 1 << z
    x = int(math.floor((lon + 180.0) / 360.0 * side))
    y = int(math.floor(_lat_to_norm(lat) * side))
    return max(0, min(side - 1, x)), max(0, min(side - 1, y))


def tile_bounds(z: int, x: int, y: int, buffer_units: int = 0) -> tuple[float, float, float, float]:
    """Границы тайла в градусах: ``(min_lon, min_lat, max_lon, max_lat)``.

    Запас ``buffer_units`` задаётся в единицах сетки тайла и расширяет
    прямоугольник во все стороны. По этим границам отбираются записи для
    тайла, поэтому запас должен совпадать с тем, по которому идёт обрезка:
    иначе объект, задевающий поля, в выборку не попадёт.
    """
    validate(z, x, y)
    side = 1 << z
    margin = buffer_units / TILE_EXTENT

    min_lon = (x - margin) / side * 360.0 - 180.0
    max_lon = (x + 1 + margin) / side * 360.0 - 180.0
    # Ось Y растёт на юг, поэтому северному краю соответствует меньший номер.
    max_lat = _norm_to_lat((y - margin) / side)
    min_lat = _norm_to_lat((y + 1 + margin) / side)
    return min_lon, min_lat, max_lon, max_lat


def project(lon: float, lat: float, z: int, x: int, y: int) -> tuple[float, float]:
    """Градусы → координаты внутри тайла, в единицах сетки."""
    side = 1 << z
    tx = ((lon + 180.0) / 360.0 * side - x) * TILE_EXTENT
    ty = (_lat_to_norm(lat) * side - y) * TILE_EXTENT
    return tx, ty


# ---------------------------------------------------------------------------
#  Прореживание
# ---------------------------------------------------------------------------


def _segment_distance(point, start, end) -> float:
    """Расстояние от точки до отрезка в координатах тайла."""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    # Положение проекции точки на отрезке, приведённое к долям его длины.
    share = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    share = max(0.0, min(1.0, share))
    return math.hypot(px - (ax + share * dx), py - (ay + share * dy))


def simplify_points(
    points: Sequence[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    """Проредить ломаную методом Дугласа — Пекера.

    Метод сохраняет форму: отбрасываются вершины, отклонение которых от
    упрощённой линии меньше порога. Прореживание «каждой n-й вершины»
    такого свойства не даёт — оно срезает углы там, где точки расположены
    часто, то есть именно в местах изгиба.
    """
    if len(points) < 3 or tolerance <= 0:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        worst, worst_index = tolerance, -1
        for index in range(first + 1, last):
            offset = _segment_distance(points[index], points[first], points[last])
            if offset > worst:
                worst, worst_index = offset, index
        if worst_index > 0:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))

    return [point for point, taken in zip(points, keep, strict=True) if taken]


def _simplify_ring(
    ring: Sequence[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    """Проредить замкнутое кольцо, сохранив замыкание.

    У кольца нет естественных концов, поэтому первая вершина закрепляется
    как начальная, а прореживается разомкнутая часть. Кольцо, потерявшее
    площадь, возвращается пустым: контур, выродившийся в отрезок, рисовать
    нечем.
    """
    if len(ring) < 4:
        return []
    closed = list(ring)
    if not _close(closed[0], closed[-1]):
        closed.append(closed[0])
    simplified = simplify_points(closed, tolerance)
    if len(simplified) < 4:
        return []
    return simplified


# ---------------------------------------------------------------------------
#  Обрезка по границам тайла
# ---------------------------------------------------------------------------


def _close(a: Sequence[float], b: Sequence[float]) -> bool:
    """Совпадают ли точки с точностью, различимой в сетке тайла."""
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def clip_line(
    points: Sequence[tuple[float, float]], low: float, high: float
) -> list[list[tuple[float, float]]]:
    """Обрезать ломаную прямоугольником ``[low, high]`` по обеим осям.

    Применяется метод Лианга — Барски: для каждого звена ищутся доли его
    длины, на которых оно остаётся внутри прямоугольника. Ломаная,
    выходящая за границу и возвращающаяся обратно, распадается на
    несколько частей, поэтому возвращается список.
    """
    parts: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []

    for index in range(len(points) - 1):
        clipped = _clip_segment(points[index], points[index + 1], low, high)
        if clipped is None:
            current = []
            continue
        start, end = clipped
        if current and _close(current[-1], start):
            current.append(end)
            continue
        current = [start, end]
        parts.append(current)

    return [part for part in parts if len(part) >= 2]


def _clip_segment(start, end, low: float, high: float):
    """Обрезать одно звено; ``None``, если звено целиком вне прямоугольника."""
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    enter, leave = 0.0, 1.0

    for delta, distance in (
        (-dx, x1 - low),
        (dx, high - x1),
        (-dy, y1 - low),
        (dy, high - y1),
    ):
        if delta == 0:
            if distance < 0:
                return None  # Звено параллельно границе и лежит за ней.
            continue
        share = distance / delta
        if delta < 0:
            enter = max(enter, share)
        else:
            leave = min(leave, share)
        if enter > leave:
            return None

    return (
        (x1 + enter * dx, y1 + enter * dy),
        (x1 + leave * dx, y1 + leave * dy),
    )


def clip_ring(
    ring: Sequence[tuple[float, float]], low: float, high: float
) -> list[tuple[float, float]]:
    """Обрезать кольцо полигона прямоугольником ``[low, high]``.

    Применяется метод Сазерленда — Ходжмана: кольцо последовательно
    отсекается четырьмя полуплоскостями. В отличие от ломаной кольцо
    остаётся связным — участки границы тайла замыкают контур.
    """
    output = list(ring)
    if output and _close(output[0], output[-1]):
        output = output[:-1]

    edges = (
        (0, low, True),
        (0, high, False),
        (1, low, True),
        (1, high, False),
    )
    for axis, bound, keep_greater in edges:
        if not output:
            return []
        source, output = output, []
        for index, point in enumerate(source):
            previous = source[index - 1]
            point_in = (point[axis] >= bound) if keep_greater else (point[axis] <= bound)
            previous_in = (previous[axis] >= bound) if keep_greater else (previous[axis] <= bound)
            if point_in != previous_in:
                output.append(_edge_crossing(previous, point, axis, bound))
            if point_in:
                output.append(point)

    if len(output) < 3:
        return []
    return output + output[:1]


def _edge_crossing(start, end, axis: int, bound: float) -> tuple[float, float]:
    """Точка пересечения звена с прямой ``axis = bound``."""
    span = end[axis] - start[axis]
    share = 0.0 if span == 0 else (bound - start[axis]) / span
    other = 1 - axis
    crossing = [0.0, 0.0]
    crossing[axis] = bound
    crossing[other] = start[other] + share * (end[other] - start[other])
    return crossing[0], crossing[1]


# ---------------------------------------------------------------------------
#  Приведение геометрии к сетке тайла
# ---------------------------------------------------------------------------


def _quantize(points: Iterable[tuple[float, float]]) -> list[tuple[int, int]]:
    """Округлить координаты до узлов сетки, убрав повторы подряд."""
    result: list[tuple[int, int]] = []
    for x, y in points:
        node = (int(round(x)), int(round(y)))
        if not result or result[-1] != node:
            result.append(node)
    return result


def _ring_area(ring: Sequence[tuple[int, int]]) -> float:
    """Площадь кольца со знаком в координатах тайла.

    Ось Y направлена вниз, поэтому спецификация требует положительной
    площади у внешнего кольца и отрицательной у внутреннего: по знаку
    клиент отличает контур от пустоты в нём.
    """
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _oriented(ring: list[tuple[int, int]], outer: bool) -> list[tuple[int, int]]:
    """Развернуть кольцо в направлении, принятом для его роли."""
    if (_ring_area(ring) > 0) == outer:
        return ring
    return ring[::-1]


def _polygon_parts(rings, z, x, y, low, high, tolerance) -> list[list[tuple[int, int]]]:
    """Собрать кольца одного полигона: внешнее и внутренние."""
    parts: list[list[tuple[int, int]]] = []
    for position, ring in enumerate(rings):
        projected = [project(lon, lat, z, x, y) for lon, lat in ring]
        simplified = _simplify_ring(projected, tolerance)
        nodes = _quantize(clip_ring(simplified, low, high)) if simplified else []
        if len(nodes) > 1 and _close(nodes[0], nodes[-1]):
            nodes = nodes[:-1]
        if len(nodes) < 3:
            if position == 0:
                return []  # Внешний контур не сохранился — полигона нет.
            continue
        nodes.append(nodes[0])
        parts.append(_oriented(nodes, outer=position == 0))
    return parts


def to_tile_geometry(
    geom: Geometry,
    z: int,
    x: int,
    y: int,
    *,
    buffer_units: int = TILE_BUFFER,
    tolerance: float = TILE_TOLERANCE,
) -> TileGeometry | None:
    """Привести геометрию к сетке тайла.

    Возвращает ``None``, если после обрезки от геометрии ничего не
    осталось: объект задел прямоугольник выборки габаритами, но в сам
    тайл не попал.
    """
    validate(z, x, y)
    low = float(-buffer_units)
    high = float(TILE_EXTENT + buffer_units)

    if geom.geom_type == "POINT":
        px, py = project(geom.coordinates[0], geom.coordinates[1], z, x, y)
        if not (low <= px <= high and low <= py <= high):
            return None
        return TileGeometry(KIND_POINT, (tuple(_quantize([(px, py)])),))

    if geom.geom_type in ("LINESTRING", "MULTILINESTRING"):
        lines = [geom.coordinates] if geom.geom_type == "LINESTRING" else geom.coordinates
        parts: list[tuple[tuple[int, int], ...]] = []
        for line in lines:
            projected = [project(lon, lat, z, x, y) for lon, lat in line]
            simplified = simplify_points(projected, tolerance)
            for piece in clip_line(simplified, low, high):
                nodes = _quantize(piece)
                if len(nodes) >= 2:
                    parts.append(tuple(nodes))
        return TileGeometry(KIND_LINE, tuple(parts)) if parts else None

    if geom.geom_type in ("POLYGON", "MULTIPOLYGON"):
        polygons = [geom.coordinates] if geom.geom_type == "POLYGON" else geom.coordinates
        parts = []
        for rings in polygons:
            for ring in _polygon_parts(rings, z, x, y, low, high, tolerance):
                parts.append(tuple(ring))
        return TileGeometry(KIND_POLYGON, tuple(parts)) if parts else None

    return None
