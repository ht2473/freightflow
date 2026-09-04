"""Разбор сжатой записи ломаной, которой маршрутизатор отдаёт геометрию.

Маршрут возвращается не списком координат, а строкой: каждая вершина
записана как приращение к предыдущей, число разбито на группы по пять бит,
и к каждой группе прибавлено 63, чтобы получился печатный символ. Запись
занимает примерно вчетверо меньше JSON-массива и потому применяется всеми
службами маршрутизации.

Точность записи задаётся числом знаков после запятой: службы OSRM и Google
используют пять, Valhalla — шесть.
"""

from __future__ import annotations

#: Число знаков после запятой в записи координат маршрутизатора.
PRECISION = 6


def decode(shape: str, precision: int = PRECISION) -> list[list[float]]:
    """Развернуть сжатую запись в список координат ``[долгота, широта]``.

    Порядок координат приводится к принятому в системе: служба записывает
    широту первой, GeoJSON и PostGIS — долготу.
    """
    if not shape:
        return []

    factor = 10.0**precision
    points: list[list[float]] = []
    index = 0
    lat = lon = 0

    while index < len(shape):
        for axis in range(2):
            shift = result = 0
            while True:
                if index >= len(shape):
                    # Запись оборвана: возвращается то, что удалось прочитать.
                    return points
                value = ord(shape[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            # Знак приращения хранится в младшем бите.
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append([lon / factor, lat / factor])

    return points


__all__ = ["PRECISION", "decode"]
