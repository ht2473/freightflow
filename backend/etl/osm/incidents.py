"""Событийный слой: дорожные работы на улично-дорожной сети.

Оперативной ленты перекрытий в открытом доступе нет: ЦОДД потока не публикует,
а сводки в средствах массовой информации машиночитаемой формы не имеют.
Проверяемый источник сведений о ремонтах — сама разметка OpenStreetMap:
участок, закрытый на реконструкцию, размечается ``highway=construction``,
и по этой отметке видно, где движение изменено.

Что именно берётся из источника:

* положение работ — координата участка;
* время появления сведений — отметка последней правки элемента; это и есть
  момент, когда о работах стало известно источнику, и ничего более точного
  разметка не содержит;
* класс дороги до закрытия — тег ``construction``; по нему определяется,
  затронуто ли грузовое движение;
* объявленный срок открытия — тег ``opening_date``, если он размечен.

Признака устранения источник не публикует. Работы, исчезнувшие из разметки,
удаляются из реестра приведением к составу источника, а не помечаются
устранёнными: время устранения пришлось бы выдумать.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

from core.choices import DataOrigin, IncidentType, UpdateFrequency
from core.models import RoadSegment, TrafficIncident
from django.utils.dateparse import parse_datetime
from geo.geometry import Geometry

from ..pipeline import Candidate, Context, Extract, RunReport
from ..quality import Check, required, within
from .loaders import OverpassPipeline
from .roads import normalize_road_name

logger = logging.getLogger("freightflow.etl")

#: Участки, закрытые на реконструкцию, в границах города.
#:
#: Выгрузка ограничена линиями: работы на площадных объектах — это стройки,
#: а не изменение условий движения. Отметка ``meta`` нужна ради времени
#: правки, ``center`` — ради координаты участка.
ROADWORKS_QUERY = """[out:json][timeout:600];
area["name"="Москва"]["admin_level"="4"]->.searchArea;
way(area.searchArea)["highway"="construction"];
out tags meta center;"""

#: Классы дорог, движение по которым существенно для грузовых перевозок,
#: и серьёзность события при их закрытии.
#:
#: Шкала выведена из значимости дороги в сети, а не из наблюдений за заторами:
#: закрытие вылетной магистрали перекладывает поток на соседние направления,
#: закрытие улицы районного значения — на соседний квартал. Правило приведено
#: здесь целиком, чтобы величина в карточке события читалась однозначно.
SEVERITY_BY_CLASS: dict[str, int] = {
    "motorway": 5,
    "trunk": 5,
    "motorway_link": 4,
    "trunk_link": 4,
    "primary": 4,
    "primary_link": 3,
    "secondary": 3,
    "secondary_link": 2,
    "tertiary": 2,
    "tertiary_link": 2,
    "unclassified": 1,
    "residential": 1,
    "road": 1,
    "living_street": 1,
    "yes": 1,
}

#: Классы, по которым допускается движение грузового транспорта: закрытие
#: такого участка меняет условия именно грузовой перевозки.
FREIGHT_CLASSES = frozenset(
    {"motorway", "trunk", "primary", "secondary",
     "motorway_link", "trunk_link", "primary_link"}
)

#: Возраст отметки, после которого сведения о работах считаются требующими
#: сверки. Отметка о реконструкции живёт в разметке, пока её не снимут вручную.
STALE_AFTER = timedelta(days=365 * 3)


def _severity(construction_class: str) -> int:
    return SEVERITY_BY_CLASS.get(construction_class, 1)


def _description(tags: dict[str, str], construction_class: str) -> str:
    """Пояснение к событию из того, что размечено.

    Собирается только из размеченных сведений: класс дороги, объявленный срок
    открытия, дата последней сверки. Ничего не добавляется от себя — карточка
    события должна читаться как выписка из источника.
    """
    parts = [f"Участок закрыт на реконструкцию, класс дороги до закрытия — "
             f"{construction_class}"]
    if tags.get("opening_date"):
        parts.append(f"объявленный срок открытия — {tags['opening_date']}")
    if tags.get("check_date"):
        parts.append(f"сведения сверялись {tags['check_date']}")
    if tags.get("description"):
        parts.append(tags["description"][:200])
    return "; ".join(parts)


class RoadworksPipeline(OverpassPipeline):
    """Реестр участков, закрытых на реконструкцию."""

    name = "osm.roadworks"
    title = "Дорожные работы"
    target_table = "traffic_incidents"
    description = (
        "Линии OpenStreetMap с отметкой highway=construction в границах "
        "города. Серьёзность выводится из класса дороги до закрытия, "
        "время регистрации — из отметки правки элемента."
    )
    query = ROADWORKS_QUERY
    model = TrafficIncident
    frequency = UpdateFrequency.DAILY
    supports_prune = True
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        required("reported_at", "Время появления сведений"),
        required("geom", "Координаты участка"),
        within("severity", 1, 5, "Серьёзность"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        return {"source": candidate.extra["source"], "external_key": candidate.key}

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        source = self.ensure_source()
        roads = {
            normalize_road_name(road.name).lower(): road
            for road in RoadSegment.objects.all()
        }

        for element in extract.records:
            tags = element.get("tags") or {}
            construction_class = (tags.get("construction") or "").strip()
            if construction_class not in SEVERITY_BY_CLASS:
                # Пешеходные дорожки, лестницы и рельсовые пути условий
                # движения автомобильного транспорта не меняют.
                report.skip(f"класс «{construction_class or 'не размечен'}» вне сети")
                continue

            centre = element.get("center") or {}
            point = (
                Geometry.point(float(centre["lon"]), float(centre["lat"]))
                if "lon" in centre and "lat" in centre
                else None
            )
            road = roads.get(normalize_road_name(tags.get("name") or "").lower())
            key = f"{element.get('type')}/{element.get('id')}"

            yield Candidate(
                key=key,
                position=key,
                values={
                    "reported_at": _timestamp(element.get("timestamp")),
                    "incident_type": IncidentType.ROADWORKS,
                    "severity": _severity(construction_class),
                    "road": road,
                    "description": _description(tags, construction_class),
                    "geom": point,
                    "affects_cargo": (
                        construction_class in FREIGHT_CLASSES
                        or bool(road and road.in_freight_frame)
                    ),
                    "origin": DataOrigin.MEASURED,
                    "source": source,
                },
                extra={"source": source, "construction": construction_class},
                payload=tags,
            )

    def prune(self, seen: set[str], context: Context) -> int:
        """Снять с учёта работы, исчезнувшие из разметки.

        Реестр приводится к составу источника. Помечать такие записи
        устранёнными нельзя: времени окончания работ источник не сообщает,
        а проставить его текущей датой значило бы выдать момент загрузки
        за момент события.
        """
        source = self.ensure_source()
        doomed = [
            pk
            for pk, key in TrafficIncident.objects.filter(source=source)
            .exclude(external_key="")
            .values_list("pk", "external_key")
            .iterator(chunk_size=2000)
            if key not in seen
        ]
        if not doomed:
            return 0
        removed, _ = TrafficIncident.objects.filter(pk__in=doomed).delete()
        return removed

    def verify(self, report: RunReport, context: Context) -> None:
        """Сообщить о возрасте разметки и о связи событий с реестром дорог.

        Отметка о реконструкции живёт в разметке до тех пор, пока её не снимут
        вручную: часть записей старше нескольких лет и, вероятно, описывает
        уже завершённые работы. Величина эта — свойство источника, и её
        следует показывать, а не сглаживать.
        """
        source = self.ensure_source()
        events = TrafficIncident.objects.filter(source=source).exclude(external_key="")
        total = events.count()
        if not total:
            return

        from django.utils import timezone

        stale_before = timezone.now() - STALE_AFTER
        stale = events.filter(reported_at__lt=stale_before).count()
        linked = events.exclude(road__isnull=True).count()
        cargo = events.filter(affects_cargo=True).count()

        report.detail(f"затрагивают грузовое движение: {cargo} из {total}")
        report.detail(f"сопоставлено с реестром магистралей: {linked} из {total}")
        if stale:
            report.detail(
                f"разметка старше трёх лет у {stale} записей: работы могли "
                f"завершиться без снятия отметки"
            )


def _timestamp(raw: str | None) -> datetime | None:
    """Разобрать отметку времени правки элемента."""
    return parse_datetime(raw) if raw else None


__all__ = ["ROADWORKS_QUERY", "SEVERITY_BY_CLASS", "RoadworksPipeline"]
