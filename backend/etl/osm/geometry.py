"""Преобразование элементов OpenStreetMap в геометрию системы.

Overpass отдаёт три разновидности элементов, и геометрия у каждой устроена
по-своему:

* **точка** (``node``) — координаты лежат прямо в элементе;
* **линия** (``way``) — список вершин в поле ``geometry``; замкнутая линия
  описывает контур площадного объекта, незамкнутая — линейный;
* **отношение** (``relation``) — набор участков с ролями ``outer`` и ``inner``,
  которые требуется собрать в кольца: сообщество размечает крупные территории
  именно так, и части контура приходят разрозненными и произвольно
  направленными отрезками.

Модуль возвращает пару «точка — контур». Точка нужна для карты и поиска
по радиусу, контур — для измерения площади и проверки вхождения в границы.
У объекта, размеченного одной точкой, контура нет, и площадь его неизвестна;
это различие сохраняется в данных, а не заполняется догадкой.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from geo.geometry import Geometry, GeometryError

logger = logging.getLogger("freightflow.etl")

#: Наименьшее число вершин замкнутого кольца: треугольник плюс повтор первой.
MIN_RING_POINTS = 4

#: Допуск замыкания кольца в градусах. Части контура в OpenStreetMap стыкуются
#: по общим узлам, поэтому расхождение либо нулевое, либо означает разрыв.
CLOSURE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ExtractedGeometry:
    """Геометрия объекта, извлечённая из элемента OpenStreetMap.

    Атрибуты:
        point: положение объекта; для площадных — центр контура;
        footprint: контур объекта либо ``None``, если он не размечен;
        area_sq_m: площадь по контуру либо ``None``, если контура нет.
    """

    point: Geometry | None
    footprint: Geometry | None
    area_sq_m: float | None

    @property
    def is_located(self) -> bool:
        return self.point is not None


def _coords(nodes: list[dict]) -> list[list[float]]:
    """Привести вершины Overpass к порядку «долгота, широта»."""
    return [[float(n["lon"]), float(n["lat"])] for n in nodes if "lon" in n and "lat" in n]


def _is_closed(ring: list[list[float]]) -> bool:
    if len(ring) < MIN_RING_POINTS:
        return False
    first, last = ring[0], ring[-1]
    return (
        abs(first[0] - last[0]) < CLOSURE_TOLERANCE
        and abs(first[1] - last[1]) < CLOSURE_TOLERANCE
    )


def _close(ring: list[list[float]]) -> list[list[float]]:
    """Замкнуть кольцо, если последняя вершина не совпадает с первой."""
    return ring if _is_closed(ring) else [*ring, ring[0]]


def assemble_rings(segments: list[list[list[float]]]) -> list[list[list[float]]]:
    """Собрать замкнутые кольца из разрозненных участков контура.

    Отношение ``multipolygon`` в OpenStreetMap состоит из участков, которые
    стыкуются по общим узлам, но приходят в произвольном порядке и с
    произвольным направлением обхода. Алгоритм последовательно приращивает
    цепочку: берётся первый свободный участок, к его концу подбирается
    следующий — прямой или развёрнутый, — и так до замыкания.

    Участки, не образовавшие замкнутого кольца, отбрасываются: незамкнутый
    контур площади не имеет, а достраивать его догадкой значило бы получить
    измеренную величину из вымысла.
    """
    pending = [list(segment) for segment in segments if len(segment) >= 2]
    rings: list[list[list[float]]] = []

    while pending:
        chain = pending.pop(0)

        extended = True
        while extended and not _is_closed(chain):
            extended = False
            for index, candidate in enumerate(pending):
                if chain[-1] == candidate[0]:
                    chain.extend(candidate[1:])
                elif chain[-1] == candidate[-1]:
                    chain.extend(reversed(candidate[:-1]))
                elif chain[0] == candidate[-1]:
                    chain = [*candidate[:-1], *chain]
                elif chain[0] == candidate[0]:
                    chain = [*reversed(candidate[1:]), *chain]
                else:
                    continue
                pending.pop(index)
                extended = True
                break

        if _is_closed(chain) and len(chain) >= MIN_RING_POINTS:
            rings.append(chain)

    return rings


def _multipolygon(outer: list[list[list[float]]],
                  inner: list[list[list[float]]]) -> Geometry | None:
    """Собрать мультиполигон из внешних и внутренних колец.

    Вложенность колец не разбирается: внутренние кольца присоединяются
    к первому внешнему. Для объектов предметной области — складских
    территорий, промзон, административных округов — этого достаточно:
    случаи, когда пустота относится не к первому контуру, единичны,
    а на площадь такое упрощение не влияет, поскольку она считается
    вычитанием суммы внутренних колец.
    """
    if not outer:
        return None
    rings = [[outer[0], *inner], *[[ring] for ring in outer[1:]]]
    return Geometry("MULTIPOLYGON", rings)


def extract(element: dict) -> ExtractedGeometry:
    """Извлечь геометрию из элемента ответа Overpass.

    Ожидается элемент, полученный запросом с ``out geom``. При отсутствии
    геометрии возвращается пустой результат, а не исключение: объект без
    координат остаётся в реестре и виден в отчёте о качестве данных.
    """
    kind = element.get("type")

    if kind == "node":
        return _from_node(element)
    if kind == "way":
        return _from_way(element)
    if kind == "relation":
        return _from_relation(element)

    return ExtractedGeometry(None, None, None)


def _from_node(element: dict) -> ExtractedGeometry:
    lon, lat = element.get("lon"), element.get("lat")
    if lon is None or lat is None:
        return ExtractedGeometry(None, None, None)
    return ExtractedGeometry(Geometry.point(float(lon), float(lat)), None, None)


def _from_way(element: dict) -> ExtractedGeometry:
    ring = _coords(element.get("geometry") or [])
    if not ring:
        # Запрос без ``out geom`` возвращает только вычисленный центр.
        return _from_center(element)

    if not _is_closed(ring) or len(ring) < MIN_RING_POINTS:
        # Незамкнутая линия — линейный объект: положение есть, площади нет.
        centre = Geometry("LINESTRING", ring).centroid
        return ExtractedGeometry(Geometry.point(*centre), None, None)

    footprint = _multipolygon([_close(ring)], [])
    return _with_footprint(footprint)


def _from_relation(element: dict) -> ExtractedGeometry:
    members = element.get("members") or []
    outer_segments, inner_segments = [], []

    for member in members:
        segment = _coords(member.get("geometry") or [])
        if len(segment) < 2:
            continue
        # Роль по умолчанию — внешнее кольцо: часть отношений размечена
        # без указания роли, и такие участки образуют внешний контур.
        if member.get("role") == "inner":
            inner_segments.append(segment)
        else:
            outer_segments.append(segment)

    outer = assemble_rings(outer_segments)
    inner = assemble_rings(inner_segments)

    if not outer:
        return _from_center(element)

    return _with_footprint(_multipolygon(outer, inner))


def _from_center(element: dict) -> ExtractedGeometry:
    """Запасной путь: воспользоваться центром, вычисленным Overpass."""
    centre = element.get("center") or {}
    lon, lat = centre.get("lon"), centre.get("lat")
    if lon is None or lat is None:
        return ExtractedGeometry(None, None, None)
    return ExtractedGeometry(Geometry.point(float(lon), float(lat)), None, None)


def _with_footprint(footprint: Geometry | None) -> ExtractedGeometry:
    if footprint is None:
        return ExtractedGeometry(None, None, None)
    try:
        centre = footprint.centroid
        area = footprint.area_sq_m
    except GeometryError:
        return ExtractedGeometry(None, None, None)
    return ExtractedGeometry(Geometry.point(*centre), footprint, area)
