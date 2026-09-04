"""Построение зон ограничения движения грузового транспорта.

Постановление Правительства Москвы № 379-ПП от 22.08.2011 определяет три зоны
не координатами, а кольцевыми магистралями: Московской кольцевой автодорогой,
Третьим транспортным кольцом и Садовым кольцом. Границы зон поэтому не
задаются отдельным набором данных, а выводятся из геометрии самих колец —
так, как их определяет нормативный акт.

Кольцо в исходных данных разбито на сотни частей, а разделённая проезжая часть
образует два независимых замкнутых контура — внутренний и внешний. Границей
зоны служит внешний: за него транспортное средство при движении по кольцу
не выходит.

Правильность сборки проверяется сопоставлением с известными величинами:
периметр контура сверяется с протяжённостью магистрали. Расхождение сверх
допустимого отклоняет зону в карантин — молча записанная неверная граница
исказила бы все последующие расчёты, от определения нужного пропуска
до отбора объектов.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

from core.choices import DataOrigin, UpdateFrequency
from core.models import RestrictionZone, RoadSegment
from geo.geometry import Geometry

from ..pipeline import Candidate, Context, Extract, RunReport
from ..quality import Check, condition, required
from .geometry import assemble_rings
from .loaders import OverpassPipeline

logger = logging.getLogger("freightflow.etl")

#: Наибольшее допустимое расхождение собранного периметра со справочным, доля.
#: Десять процентов покрывают разницу между осевой линией и внешней проезжей
#: частью, но не пропустят контур, собранный из посторонних участков.
PERIMETER_TOLERANCE = 0.10


@dataclass(frozen=True)
class ZoneSpec:
    """Описание зоны: условия въезда по постановлению и способ построения границы.

    Числовые условия взяты из постановления № 379-ПП и приведены здесь
    в одном месте: при изменении нормативного акта правится только эта
    таблица, а не расчётный код.
    """

    code: str
    name: str
    short_name: str
    level: int
    description: str
    #: Запрос Overpass, возвращающий части кольцевой магистрали.
    query: str
    #: Наименование магистрали в реестре — для связи зоны с её границей.
    road_name: str
    #: Справочная протяжённость кольца, км — для проверки сборки.
    reference_perimeter_km: float
    permit_required_from_tons: Decimal
    min_ecological_class: int | None
    seasonal_limit_tons: Decimal | None
    fine_rubles: int


#: Границы зон определяются кольцевыми магистралями. МКАД собирается из линий
#: улично-дорожной сети — покилометровая разметка образует замкнутый контур
#: сама по себе. Садовое кольцо и Третье транспортное собираются из маршрутных
#: отношений: обе магистрали состоят из улиц с разными наименованиями,
#: и связывает их именно отношение.
_MKAD_QUERY = """[out:json][timeout:600];
way["highway"]["name"~"^МКАД"](55.4,37.2,56.0,37.95);
out geom;"""

_TTK_QUERY = """[out:json][timeout:600];
relation(2094286);
out geom;"""

_SK_QUERY = """[out:json][timeout:600];
relation(2094267);
out geom;"""


ZONES: tuple[ZoneSpec, ...] = (
    ZoneSpec(
        code="mkad",
        name="Зона МКАД",
        short_name="МКАД",
        level=1,
        description=(
            "Территория внутри Московской кольцевой автомобильной дороги, "
            "включая саму МКАД. Движение грузового транспорта с разрешённой "
            "максимальной массой свыше 3,5 тонны допускается по пропуску."
        ),
        query=_MKAD_QUERY,
        road_name="МКАД",
        reference_perimeter_km=108.9,
        permit_required_from_tons=Decimal("3.5"),
        min_ecological_class=2,
        seasonal_limit_tons=Decimal("12"),
        fine_rubles=7500,
    ),
    ZoneSpec(
        code="ttk",
        name="Зона Третьего транспортного кольца",
        short_name="ТТК",
        level=2,
        description=(
            "Территория внутри Третьего транспортного кольца. Пропуск зоны "
            "МКАД здесь не действует: для въезда требуется пропуск с зоной "
            "действия «ТТК» либо более внутренней."
        ),
        query=_TTK_QUERY,
        road_name="Третье транспортное кольцо",
        reference_perimeter_km=35.0,
        permit_required_from_tons=Decimal("3.5"),
        min_ecological_class=2,
        seasonal_limit_tons=Decimal("12"),
        fine_rubles=7500,
    ),
    ZoneSpec(
        code="sk",
        name="Зона Садового кольца",
        short_name="СК",
        level=3,
        description=(
            "Территория внутри Садового кольца — наиболее ограниченная зона. "
            "Требуется пропуск с зоной действия «СК»."
        ),
        query=_SK_QUERY,
        road_name="Садовое кольцо",
        reference_perimeter_km=15.6,
        permit_required_from_tons=Decimal("3.5"),
        min_ecological_class=2,
        seasonal_limit_tons=Decimal("12"),
        fine_rubles=7500,
    ),
)

LEGAL_BASIS = "Постановление Правительства Москвы № 379-ПП от 22.08.2011"


@dataclass(frozen=True)
class Boundary:
    """Граница зоны, собранная из частей кольцевой магистрали."""

    geometry: Geometry | None
    area_sq_km: float | None
    perimeter_km: float | None
    error: str = ""


def _segments(elements: list[dict]) -> list[list[list[float]]]:
    """Извлечь отрезки из элементов ответа: линий и участников отношений."""
    segments: list[list[list[float]]] = []

    def add(nodes: list[dict]) -> None:
        points = [
            [float(n["lon"]), float(n["lat"])]
            for n in nodes
            if "lon" in n and "lat" in n
        ]
        if len(points) >= 2:
            segments.append(points)

    for element in elements:
        if element.get("geometry"):
            add(element["geometry"])
        for member in element.get("members") or []:
            add(member.get("geometry") or [])

    return segments


def build_boundary(elements: list[dict]) -> Boundary:
    """Собрать границу зоны из частей кольцевой магистрали.

    Из собранных контуров выбирается наибольший по площади: разделённая
    проезжая часть образует два вложенных кольца, и внешнее из них
    ограничивает зону.

    Несобравшийся контур возвращается описанием причины, а не исключением:
    неудача сборки одной зоны не должна прерывать загрузку остальных, а сама
    причина попадает в карантин наравне с прочими отклонениями.
    """
    rings = assemble_rings(_segments(elements))
    if not rings:
        return Boundary(
            None, None, None,
            "замкнутый контур не собран: части кольцевой магистрали "
            "не образуют непрерывной цепочки",
        )

    candidates = [(Geometry("POLYGON", [ring]), ring) for ring in rings]
    _outer, ring = max(candidates, key=lambda pair: pair[0].area_sq_m)

    geometry = Geometry("MULTIPOLYGON", [[ring]])
    return Boundary(
        geometry=geometry,
        area_sq_km=geometry.area_sq_m / 1e6,
        perimeter_km=Geometry("LINESTRING", ring).length_km,
    )


def _boundary_assembled(candidate: Candidate) -> str | None:
    """Контур зоны должен замыкаться."""
    return candidate.extra.get("assembly_error") or None


def _perimeter_matches(candidate: Candidate) -> str | None:
    """Периметр контура должен совпадать с протяжённостью кольцевой магистрали.

    Сверка со справочной величиной — единственный доступный способ убедиться,
    что контур собран из нужных участков. Молча записанная неверная граница
    исказила бы всё, что на ней держится: определение требуемого пропуска,
    отбор объектов по зоне, расчёт площади обслуживаемой территории.
    """
    perimeter = candidate.extra.get("perimeter_km")
    reference = candidate.extra.get("reference_perimeter_km")
    if not perimeter or not reference:
        return None
    deviation = abs(perimeter - reference) / reference
    if deviation > PERIMETER_TOLERANCE:
        return (
            f"периметр собранного контура {perimeter:.1f} км расходится "
            f"со справочной протяжённостью {reference} км на "
            f"{deviation * 100:.0f} %"
        )
    return None


class RestrictionZonesPipeline(OverpassPipeline):
    """Зоны ограничения движения грузового транспорта.

    Границы не задаются набором данных: постановление определяет их через
    кольцевые магистрали, поэтому геометрия зоны выводится из геометрии
    соответствующего кольца. Каждой зоне отвечает свой запрос — кольца
    размечены по-разному, и общего отбора для них нет.
    """

    name = "osm.zones"
    title = "Зоны ограничения движения"
    target_table = "restriction_zones"
    description = (
        "МКАД, Третье транспортное и Садовое кольцо. Условия въезда взяты "
        "из постановления № 379-ПП, геометрия — из разметки самих колец."
    )
    model = RestrictionZone
    frequency = UpdateFrequency.MONTHLY
    checks: tuple[Check, ...] = (
        condition("assembly.ring", "Контур зоны замыкается", _boundary_assembled),
        required("geom", "Граница зоны"),
        condition("assembly.perimeter", "Периметр совпадает со справочным",
                  _perimeter_matches),
    )

    def lookup(self, candidate: Candidate) -> dict:
        return {"code": candidate.key}

    def extract(self, context: Context) -> Extract:
        """Получить разметку всех трёх колец.

        Ответы приходят на разные запросы, поэтому выгрузка собирается
        по частям: каждой зоне отвечает своя пара «описание — элементы».
        """
        client = context.client()
        records: list[tuple[ZoneSpec, list[dict]]] = []
        extract = Extract(records=records, from_cache=True)

        for spec in ZONES:
            response = client.fetch(spec.query, refresh=context.refresh)
            records.append((spec, response.elements))
            extract.count += response.count
            extract.fetched_at = response.fetched_at
            extract.from_cache = extract.from_cache and response.from_cache
        return extract

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        roads = {road.name: road for road in RoadSegment.objects.all()}

        for spec, elements in extract.records:
            boundary = build_boundary(elements)
            yield Candidate(
                key=spec.code,
                position=f"кольцо «{spec.road_name}», элементов {len(elements)}",
                values={
                    "name": spec.name,
                    "short_name": spec.short_name,
                    "level": spec.level,
                    "description": spec.description,
                    "boundary_road": roads.get(spec.road_name),
                    "permit_required_from_tons": spec.permit_required_from_tons,
                    "min_ecological_class": spec.min_ecological_class,
                    "seasonal_limit_tons": spec.seasonal_limit_tons,
                    "fine_rubles": spec.fine_rubles,
                    "legal_basis": LEGAL_BASIS,
                    "geom": boundary.geometry,
                    "area_sq_km": (
                        round(boundary.area_sq_km, 2) if boundary.area_sq_km else None
                    ),
                    "perimeter_km": (
                        round(boundary.perimeter_km, 2) if boundary.perimeter_km else None
                    ),
                    "geometry_origin": DataOrigin.MEASURED,
                    "source_updated_at": extract.fetched_at,
                },
                extra={
                    "assembly_error": boundary.error,
                    "perimeter_km": boundary.perimeter_km,
                    "reference_perimeter_km": spec.reference_perimeter_km,
                },
                payload={
                    "code": spec.code,
                    "road": spec.road_name,
                    "perimeter_km": boundary.perimeter_km,
                    "reference_perimeter_km": spec.reference_perimeter_km,
                },
            )

    def verify(self, report: RunReport, context: Context) -> None:
        """Проверить, что зоны действительно вложены одна в другую.

        Вложенность — не оформительское допущение, а условие, на котором
        держится расчёт пропуска: пропуск во внутреннюю зону действует
        и во внешних. Нарушение означает, что какая-то из границ собрана
        неверно, и увидеть его можно только на наборе целиком.
        """
        zones = list(RestrictionZone.objects.exclude(geom__isnull=True).order_by("level"))
        for outer, inner in zip(zones, zones[1:], strict=False):
            if outer.area_sq_km <= inner.area_sq_km:
                report.note(
                    f"{inner.short_name} ({inner.area_sq_km} км²) не меньше "
                    f"{outer.short_name} ({outer.area_sq_km} км²): "
                    f"вложенность нарушена"
                )
                continue
            centre = inner.geom.centroid
            if not outer.geom.contains(*centre):
                report.note(
                    f"центр зоны {inner.short_name} лежит вне зоны {outer.short_name}"
                )
            else:
                report.detail(
                    f"{inner.short_name} ({inner.area_sq_km} км²) вложена "
                    f"в {outer.short_name} ({outer.area_sq_km} км²)"
                )


__all__ = ["ZONES", "Boundary", "RestrictionZonesPipeline", "ZoneSpec", "build_boundary"]
