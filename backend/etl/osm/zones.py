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
допустимого прерывает загрузку — молча записанная неверная граница исказила бы
все последующие расчёты, от определения нужного пропуска до отбора объектов.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from core.choices import DataOrigin
from core.models import RestrictionZone, RoadSegment
from django.db import transaction
from geo.geometry import Geometry

from ..client import OverpassClient
from .geometry import assemble_rings
from .loaders import LoadReport

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


class ZoneAssemblyError(RuntimeError):
    """Границу зоны не удалось собрать или она не прошла проверку."""


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


def build_boundary(elements: list[dict], reference_km: float) -> tuple[Geometry, float, float]:
    """Собрать границу зоны из частей кольцевой магистрали.

    Возвращает кортеж ``(геометрия, площадь_км², периметр_км)``.

    Из собранных контуров выбирается наибольший по площади: разделённая
    проезжая часть образует два вложенных кольца, и внешнее из них
    ограничивает зону.
    """
    rings = assemble_rings(_segments(elements))
    if not rings:
        raise ZoneAssemblyError(
            "Замкнутый контур не собран: части кольцевой магистрали не образуют "
            "непрерывной цепочки"
        )

    candidates = [(Geometry("POLYGON", [ring]), ring) for ring in rings]
    boundary, ring = max(candidates, key=lambda pair: pair[0].area_sq_m)

    perimeter_km = Geometry("LINESTRING", ring).length_km
    deviation = abs(perimeter_km - reference_km) / reference_km
    if deviation > PERIMETER_TOLERANCE:
        raise ZoneAssemblyError(
            f"Периметр собранного контура {perimeter_km:.1f} км расходится "
            f"со справочной протяжённостью {reference_km} км на "
            f"{deviation * 100:.0f} % — граница собрана неверно"
        )

    geometry = Geometry("MULTIPOLYGON", [[ring]])
    return geometry, geometry.area_sq_m / 1e6, perimeter_km


def load_zones(client: OverpassClient, refresh: bool = False) -> LoadReport:
    """Построить зоны ограничения движения по геометрии кольцевых магистралей."""
    report = LoadReport(dataset="Зоны ограничения движения")
    roads = {road.name: road for road in RoadSegment.objects.all()}

    with transaction.atomic():
        for spec in ZONES:
            response = client.fetch(spec.query, refresh=refresh)
            report.fetched += response.count
            report.from_cache = response.from_cache
            report.fetched_at = response.fetched_at

            try:
                geometry, area_sq_km, perimeter_km = build_boundary(
                    response.elements, spec.reference_perimeter_km
                )
            except ZoneAssemblyError as exc:
                report.skipped += 1
                report.notes.append(f"{spec.short_name}: {exc}")
                continue

            _, created = RestrictionZone.objects.update_or_create(
                code=spec.code,
                defaults={
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
                    "geom": geometry,
                    "area_sq_km": round(area_sq_km, 2),
                    "perimeter_km": round(perimeter_km, 2),
                    "geometry_origin": DataOrigin.MEASURED,
                    "source_updated_at": response.fetched_at,
                },
            )
            if created:
                report.created += 1
            else:
                report.updated += 1

            logger.info(
                "Зона %s: площадь %.1f км², периметр %.1f км",
                spec.short_name, area_sq_km, perimeter_km,
            )

    _check_nesting(report)
    return report


def _check_nesting(report: LoadReport) -> None:
    """Проверить, что зоны действительно вложены одна в другую.

    Вложенность — не оформительское допущение, а условие, на котором держится
    расчёт пропуска: пропуск во внутреннюю зону действует и во внешних.
    Нарушение означает, что какая-то из границ собрана неверно.
    """
    zones = list(RestrictionZone.objects.exclude(geom__isnull=True).order_by("level"))
    for outer, inner in zip(zones, zones[1:], strict=False):
        if outer.area_sq_km <= inner.area_sq_km:
            report.notes.append(
                f"{inner.short_name} ({inner.area_sq_km} км²) не меньше "
                f"{outer.short_name} ({outer.area_sq_km} км²): вложенность нарушена"
            )
            continue
        centre = inner.geom.centroid
        if not outer.geom.contains(*centre):
            report.notes.append(
                f"центр зоны {inner.short_name} лежит вне зоны {outer.short_name}"
            )
