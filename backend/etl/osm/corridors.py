"""Загрузка федеральных автомобильных коридоров, входящих в Москву.

Внешние грузовые потоки приходят в город по федеральным трассам, которые
продолжаются вылетными магистралями внутри города. Коридор — это трасса
целиком, от границы города до примыкания к кольцевой магистрали, и в
OpenStreetMap он описан маршрутным отношением с учётным номером вида
``М-4`` или ``А-103``.

Загружается только часть трассы, попадающая в габаритный прямоугольник
Москвы: коридор «Дон» тянется до Новороссийска, и в реестре логистической
инфраструктуры города ему место лишь в пределах города и ближних подходов.

Интенсивность движения по коридорам не загружается: измерений в открытом
доступе нет, а выводить их из чего-либо было бы домыслом. Поле остаётся
незаполненным и видно в отчёте о качестве данных.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

from core.choices import RouteType, UpdateFrequency
from core.models import CargoRoute
from geo.geometry import Geometry

from ..pipeline import Candidate, Context, Extract, RunReport
from ..quality import Check, fits, required, within
from .loaders import OverpassPipeline

logger = logging.getLogger("freightflow.etl")

#: Габаритный прямоугольник Москвы с ближними подходами.
BBOX = "55.30,36.70,56.10,38.00"

#: Коридоры отбираются по учётному номеру федеральной или региональной трассы.
#: Европейские маршруты (E 30, E 101) дублируют федеральные и исключены:
#: одна и та же дорога попадала бы в реестр дважды.
CORRIDORS_QUERY = f"""[out:json][timeout:900];
relation["type"="route"]["route"="road"]["ref"~"^[МA]|^А-"]({BBOX});
out geom;"""

#: Учётный номер федеральной или региональной трассы.
_REF_RE = re.compile(r"^[МA]-?\d|^А-\d", re.IGNORECASE)

#: Наименьшая протяжённость части коридора в пределах выгрузки, км.
#: Более короткие вхождения — это касание границы прямоугольника, а не
#: коридор, ведущий в город.
MIN_CORRIDOR_LENGTH_KM = 3.0


#: Границы обрезки: минимальная и максимальная широта и долгота.
CLIP_BOUNDS = (55.30, 36.70, 56.10, 38.00)


def _clip(points: list[list[float]]) -> list[list[list[float]]]:
    """Оставить от ломаной части, попадающие в границы города.

    Маршрутное отношение описывает трассу целиком: «Дон» тянется до
    Новороссийска, и без обрезки в реестр городской инфраструктуры попали бы
    полторы тысячи километров, к Москве отношения не имеющих.

    Ломаная разрезается там, где выходит за границы, и каждая оставшаяся
    часть сохраняется отдельно. Соединять их нельзя: между ними лежит
    участок, из выгрузки исключённый.
    """
    min_lat, min_lon, max_lat, max_lon = CLIP_BOUNDS
    parts: list[list[list[float]]] = []
    current: list[list[float]] = []

    for lon, lat in points:
        inside = min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
        if inside:
            current.append([lon, lat])
        elif len(current) >= 2:
            parts.append(current)
            current = []
        else:
            current = []

    if len(current) >= 2:
        parts.append(current)
    return parts


def _corridor_geometry(element: dict) -> Geometry | None:
    """Собрать геометрию коридора из участков маршрутного отношения.

    Части хранятся набором линий, а не склеиваются в одну ломаную: участки
    отношения не всегда смежны, и соединение их подряд дало бы отрезки через
    полгорода и протяжённость в тысячи километров.
    """
    parts: list[list[list[float]]] = []
    for member in element.get("members") or []:
        points = [
            [float(node["lon"]), float(node["lat"])]
            for node in (member.get("geometry") or [])
            if "lon" in node and "lat" in node
        ]
        parts.extend(_clip(points))

    if not parts:
        return None
    return Geometry("MULTILINESTRING", parts)


def _direction(tags: dict[str, str]) -> str:
    """Отнести коридор к направлению относительно города.

    Отношение описывает трассу целиком, в обе стороны, поэтому направление
    ввоза и вывоза по разметке не различается. Все коридоры относятся
    к транзитным: через город они и проходят, а разделение потоков
    по направлениям требует данных о перевозках, которых нет.
    """
    return RouteType.TRANSIT


class CorridorsPipeline(OverpassPipeline):
    """Реестр федеральных грузовых коридоров, входящих в город."""

    name = "osm.corridors"
    title = "Грузовые коридоры"
    target_table = "cargo_routes"
    description = (
        "Маршрутные отношения федеральных и региональных трасс. Геометрия "
        "обрезается по габаритам города: коридор нужен реестру в пределах "
        "Москвы и ближних подходов."
    )
    query = CORRIDORS_QUERY
    model = CargoRoute
    frequency = UpdateFrequency.MONTHLY
    supports_prune = True
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        required("name", "Наименование коридора"),
        fits("name", 200, "Наименование коридора"),
        required("geom", "Геометрия коридора"),
        within("distance_km", 3, 500, "Протяжённость в пределах выгрузки", "км"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        return {"name": candidate.key}

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        source = self.ensure_source()

        for element in extract.records:
            tags = element.get("tags") or {}
            ref = (tags.get("ref") or "").strip()
            name = (tags.get("name") or "").strip().strip("«»\"")

            if not ref or not _REF_RE.match(ref):
                # В выгрузку попадают европейские маршруты и городские
                # автобусные линии: учётного номера трассы у них нет.
                report.skip("учётный номер трассы не распознан")
                continue

            geometry = _corridor_geometry(element)
            if geometry is None:
                report.skip("вне габаритов города")
                continue

            length_km = geometry.length_km
            if length_km < MIN_CORRIDOR_LENGTH_KM:
                # Касание границы прямоугольника коридором в город не является.
                report.skip("короче порога включения")
                continue

            title = (f"{ref} «{name}»" if name else ref)[:200]
            yield Candidate(
                key=title,
                position=f"relation/{element.get('id')}",
                values={
                    "route_type": _direction(tags),
                    "origin_region": (tags.get("from") or "")[:120],
                    "destination": (tags.get("to") or "")[:120],
                    "distance_km": round(length_km, 2),
                    "geom": geometry,
                    "source": source,
                },
                extra={"name": title, "ref": ref},
                payload=tags,
            )

    def prune(self, seen: set[str], context: Context) -> int:
        doomed = [
            pk
            for pk, name in CargoRoute.objects.values_list("pk", "name").iterator()
            if name not in seen
        ]
        if not doomed:
            return 0
        removed, _ = CargoRoute.objects.filter(pk__in=doomed).delete()
        return removed


__all__ = ["CORRIDORS_QUERY", "CorridorsPipeline"]
