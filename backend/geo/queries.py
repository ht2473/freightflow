"""Пространственные запросы поверх ORM.

Каждая функция имеет две реализации: «серверную» — через функции PostGIS,
выполняемые самой СУБД, и «прикладную» — на Python, применяемую на SQLite.
Выбор происходит автоматически по признаку ``connection.vendor``, вызывающий
код одинаков для обоих контуров.

Соглашение о единицах: расстояния — километры, координаты — градусы WGS-84
в порядке ``(долгота, широта)``, габаритный прямоугольник — кортеж
``(min_lon, min_lat, max_lon, max_lat)``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from django.db import connection
from django.db.models import QuerySet
from django.db.models.expressions import RawSQL

from .geometry import Geometry, feature_collection, haversine_km

# Порог, ниже которого прикладная фильтрация на Python остаётся приемлемой по
# времени отклика. При превышении в журнал пишется предупреждение о том, что
# контур следует перевести на PostGIS.
PYTHON_FALLBACK_WARN_ROWS = 50_000


def is_postgis() -> bool:
    """Признак того, что активное соединение обслуживается PostgreSQL/PostGIS."""
    return connection.vendor == "postgresql"


# ---------------------------------------------------------------------------
#  Фильтрация по видимой области карты
# ---------------------------------------------------------------------------


def _qualified(qs: QuerySet, field: str) -> str:
    """Полное имя колонки геометрии вида ``"таблица"."колонка"``.

    Квалификация обязательна: выражения передаются в SQL как есть, а выборка
    почти всегда содержит соединения со справочниками. Справочник округов
    имеет собственную колонку ``geom`` (границы округа), поэтому неполное имя
    становится неоднозначным и запрос отклоняется СУБД. Дефект проявляется
    только на PostgreSQL — SQLite разрешает неоднозначность молча, выбирая
    первую подходящую колонку, что привело бы к неверному результату.
    """
    column = qs.model._meta.get_field(field).column
    return f'"{qs.model._meta.db_table}"."{column}"'


def in_bbox(qs: QuerySet, bbox: Sequence[float], field: str = "geom") -> QuerySet:
    """Отобрать записи, геометрия которых попадает в габаритный прямоугольник.

    На PostGIS используется оператор ``&&``, опирающийся на индекс GiST, — это
    основной способ ограничить выдачу карты видимой областью экрана.
    """
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)

    if is_postgis():
        column = _qualified(qs, field)
        return qs.extra(  # noqa: S610 — параметры передаются связыванием
            where=[f"{column} && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"],
            params=[min_lon, min_lat, max_lon, max_lat],
        )

    # Прикладная фильтрация для SQLite. Как и в поиске ближайших, отбор идёт
    # в два прохода: сначала по паре «ключ — геометрия», затем выборка ORM
    # сужается по найденным ключам. Так объекты модели создаются только для
    # записей, попавших в видимую область, а не для всего реестра.
    matched: list = []
    for pk, geom in qs.values_list("pk", field).iterator(chunk_size=5000):
        if not isinstance(geom, Geometry):
            continue
        o_min_lon, o_min_lat, o_max_lon, o_max_lat = geom.bounds
        intersects = (
            o_min_lon <= max_lon
            and o_max_lon >= min_lon
            and o_min_lat <= max_lat
            and o_max_lat >= min_lat
        )
        if intersects:
            matched.append(pk)

    # Возвращается выборка ORM, а не список: вызывающий код может наложить на
    # неё срез, и он превратится в LIMIT на стороне СУБД.
    return qs.filter(pk__in=matched)


# ---------------------------------------------------------------------------
#  Поиск ближайших объектов
# ---------------------------------------------------------------------------


def annotate_distance(qs: QuerySet, lon: float, lat: float, field: str = "geom") -> QuerySet:
    """Добавить к выборке расстояние до заданной точки (км).

    Расчёт ведётся по типу ``geography``: PostGIS учитывает кривизну Земли и
    возвращает метры, которые здесь переводятся в километры.
    """
    if not is_postgis():
        return qs
    expression = (
        f"ST_Distance({_qualified(qs, field)}::geography, "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000.0"
    )
    return qs.annotate(distance_km=RawSQL(expression, (float(lon), float(lat))))


def nearest(
    qs: QuerySet,
    lon: float,
    lat: float,
    radius_km: float = 5.0,
    limit: int = 20,
    field: str = "geom",
) -> list[tuple[Any, float]]:
    """Найти ближайшие к точке записи в пределах радиуса.

    Возвращает список пар ``(объект, расстояние_км)``, отсортированный по
    возрастанию расстояния. У каждого объекта дополнительно проставляется
    атрибут ``distance_km`` — так шаблонам не нужно распаковывать кортежи.
    """
    lon, lat, radius_km = float(lon), float(lat), float(radius_km)

    if is_postgis():
        # ST_DWithin умеет опираться на индекс GiST, поэтому фильтр по радиусу
        # ставится до сортировки: СУБД отбрасывает заведомо далёкие строки.
        qs = qs.extra(  # noqa: S610 — параметры передаются связыванием
            where=[
                f"ST_DWithin({_qualified(qs, field)}::geography, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
            ],
            params=[lon, lat, radius_km * 1000.0],
        )
        qs = annotate_distance(qs, lon, lat, field).order_by("distance_km")[:limit]
        return [(obj, float(obj.distance_km)) for obj in qs]

    # Прикладная реализация для SQLite: расстояние вычисляется в Python.
    #
    # Отбор выполняется в два прохода. Первый читает только пару «ключ —
    # геометрия» через values_list: полное создание объектов модели для всех
    # записей обходится примерно вдвое дороже самого расчёта расстояний и
    # почти всегда напрасно — в результат попадает лишь несколько записей.
    # Второй проход загружает как объекты модели только отобранные записи.
    origin = (lon, lat)
    # Предварительное отсечение по прямоугольнику: сравнение чисел заметно
    # дешевле вычисления гаверсинуса, а прямоугольник заведомо накрывает круг
    # заданного радиуса.
    lat_margin = radius_km / 111.0
    lon_margin = radius_km / max(111.0 * math.cos(math.radians(lat)), 1e-6)
    bounds = (lon - lon_margin, lat - lat_margin, lon + lon_margin, lat + lat_margin)

    measured: list[tuple[Any, float]] = []
    for pk, geom in qs.values_list("pk", field).iterator(chunk_size=5000):
        if not isinstance(geom, Geometry):
            continue
        point = (geom.lon, geom.lat)
        if not (bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]):
            continue
        distance = haversine_km(origin, point)
        if distance <= radius_km:
            measured.append((pk, distance))

    measured.sort(key=lambda pair: pair[1])
    selected = measured[:limit]
    if not selected:
        return []

    distances = dict(selected)
    objects = {obj.pk: obj for obj in qs.filter(pk__in=distances)}

    found: list[tuple[Any, float]] = []
    for pk, distance in selected:
        obj = objects.get(pk)
        if obj is None:
            continue
        obj.distance_km = distance
        found.append((obj, distance))
    return found


def distance_between(a: Geometry | None, b: Geometry | None) -> float | None:
    """Расстояние между центрами двух геометрий, км."""
    if not isinstance(a, Geometry) or not isinstance(b, Geometry):
        return None
    return haversine_km((a.lon, a.lat), (b.lon, b.lat))


# ---------------------------------------------------------------------------
#  Формирование слоёв GeoJSON для карты
# ---------------------------------------------------------------------------


def to_feature_collection(
    rows: Sequence[Any],
    properties: callable,
    field: str = "geom",
    simplify_every: int = 1,
) -> dict:
    """Собрать коллекцию GeoJSON из записей ORM.

    Аргументы:
        rows: последовательность объектов модели;
        properties: функция, возвращающая словарь атрибутов для объекта;
        field: имя поля геометрии;
        simplify_every: прореживание вершин линий — брать каждую N-ю точку.
            Применяется к длинным маршрутам, чтобы не передавать в браузер
            избыточную детализацию.
    """
    features = []
    for obj in rows:
        geom = getattr(obj, field, None)
        if not isinstance(geom, Geometry):
            continue
        if simplify_every > 1 and geom.geom_type == "LINESTRING":
            geom = _thin_line(geom, simplify_every)
        features.append(geom.as_feature(properties(obj)))
    return feature_collection(features)


def _thin_line(geom: Geometry, step: int) -> Geometry:
    """Проредить вершины ломаной, сохранив первую и последнюю точки."""
    coords = geom.coordinates
    if len(coords) <= 3:
        return geom
    thinned = coords[::step]
    if thinned[-1] != coords[-1]:
        thinned.append(coords[-1])
    return Geometry("LINESTRING", thinned, geom.srid)


def bbox_of(rows: Sequence[Any], field: str = "geom") -> tuple[float, float, float, float] | None:
    """Общий габаритный прямоугольник набора записей."""
    boxes = [
        getattr(obj, field).bounds
        for obj in rows
        if isinstance(getattr(obj, field, None), Geometry)
    ]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
