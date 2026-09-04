"""Загрузка данных OpenStreetMap в реестры системы.

Два набора: границы административных округов и реестр объектов логистической
инфраструктуры. Оба проходят общий конвейер (:mod:`etl.pipeline`), поэтому
здесь описано только то, чем они отличаются от прочих источников, — запрос
к службе, правила отбора и приведение элемента разметки к записи реестра.

Выгрузка намеренно шире реестра: разметка сообщества неоднородна, и один и тот
же склад бывает обозначен зданием, территорией или точкой. Решение об отнесении
принимают правила :mod:`etl.osm.classify`, и обозначение сработавшего правила
сохраняется вместе с объектом — по нему видно, почему объект в реестре.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from core.choices import DataOrigin, OsmElement, SourceType, UpdateFrequency
from core.models import DataSource, District, InfrastructureObject, InfrastructureType
from geo.geometry import Geometry

from ..pipeline import Candidate, Context, Extract, ModelPipeline, RunReport
from ..quality import Check, condition, fits, not_negative, required
from . import queries
from .addresses import build_address, build_contacts, build_opening_hours, build_operator
from .classify import classify

logger = logging.getLogger("freightflow.etl")

#: Код источника данных в справочнике системы.
SOURCE_CODE = "osm"

#: Наибольшая допустимая площадь объекта, м². Складская территория крупнее
#: пяти квадратных километров в городской черте не встречается: такое значение
#: означает, что в контур попала вся промзона целиком, а не отдельный объект.
MAX_PLAUSIBLE_AREA_SQ_M = 5_000_000


def ensure_source() -> DataSource:
    """Справочная запись об источнике OpenStreetMap.

    Источник объявляется в справочнике только вместе с работающим загрузчиком:
    запись в реестре источников означает, что данные из него действительно
    поступают, а не что такая возможность предполагается.
    """
    source, _ = DataSource.objects.update_or_create(
        code=SOURCE_CODE,
        defaults={
            "name": "OpenStreetMap — картографические данные",
            "source_type": SourceType.API,
            "url": "https://overpass-api.de/api/interpreter",
            "update_frequency": UpdateFrequency.WEEKLY,
            "is_active": True,
        },
    )
    return source


def _osm_key(element: dict) -> tuple[str, int] | None:
    """Ключ исходного элемента: разновидность и идентификатор."""
    kind, identifier = element.get("type"), element.get("id")
    if kind not in OsmElement.values or identifier is None:
        return None
    return kind, int(identifier)


class OverpassPipeline(ModelPipeline):
    """Общая часть конвейеров, получающих данные из OpenStreetMap.

    Выгрузка приходит одним ответом службы: разбирать её по частям нельзя,
    поскольку сборка контура отношения требует всех его участков сразу.
    """

    source_code = SOURCE_CODE
    frequency = UpdateFrequency.WEEKLY
    #: Запрос на языке Overpass QL.
    query: str = ""

    def ensure_source(self) -> DataSource:
        return ensure_source()

    def extract(self, context: Context) -> Extract:
        response = context.client().fetch(self.query, refresh=context.refresh)
        return Extract(
            records=response.elements,
            count=response.count,
            fetched_at=response.fetched_at,
            from_cache=response.from_cache,
        )


# ---------------------------------------------------------------------------
#  Административные округа
# ---------------------------------------------------------------------------

#: Сопоставление наименований OpenStreetMap с аббревиатурами справочника.
#: Ведётся вручную: наименования в разметке содержат слова «административный
#: округ», а сопоставление по подстроке спутало бы Северный с Северо-Восточным.
DISTRICT_SHORT_NAMES: dict[str, str] = {
    "Центральный административный округ": "ЦАО",
    "Северный административный округ": "САО",
    "Северо-Восточный административный округ": "СВАО",
    "Восточный административный округ": "ВАО",
    "Юго-Восточный административный округ": "ЮВАО",
    "Южный административный округ": "ЮАО",
    "Юго-Западный административный округ": "ЮЗАО",
    "Западный административный округ": "ЗАО",
    "Северо-Западный административный округ": "СЗАО",
    "Зеленоградский административный округ": "ЗелАО",
    "Новомосковский административный округ": "НАО",
    "Троицкий административный округ": "ТАО",
}


def _district_in_registry(candidate: Candidate) -> str | None:
    """Округ из выгрузки должен присутствовать в справочнике.

    Справочник округов закрыт: их двенадцать, и появление тринадцатого
    означало бы не пополнение реестра, а ошибку сопоставления.
    """
    if candidate.extra.get("district") is None:
        return f"округ «{candidate.extra.get('name', '')}» отсутствует в справочнике"
    return None


class DistrictsPipeline(OverpassPipeline):
    """Границы административных округов.

    Округа присутствуют в справочнике изначально, но размечены одними
    центрами. Загрузка добавляет к ним настоящие границы, без которых
    невозможны ни картограмма, ни отнесение объекта к округу, ни расчёт
    обеспеченности территории складскими мощностями.
    """

    name = "osm.districts"
    title = "Административные округа"
    target_table = "districts"
    description = (
        "Отношения OpenStreetMap уровня admin_level=5. Контур собирается "
        "из участков границы, центр пересчитывается по контуру."
    )
    query = queries.DISTRICTS
    model = District
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        condition("reference.district", "Округ присутствует в справочнике",
                  _district_in_registry),
        required("geom", "Граница округа"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        return {"short_name": candidate.key}

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        from .geometry import extract as extract_geometry

        registry = {district.short_name: district
                    for district in District.objects.all()}

        for element in extract.records:
            name = (element.get("tags", {}).get("name") or "").strip()
            short_name = DISTRICT_SHORT_NAMES.get(name)
            if not short_name:
                # В габаритный прямоугольник попадают округа сопредельных
                # территорий; предметом реестра они не являются.
                report.skip("вне сопоставления округов")
                continue

            geometry = extract_geometry(element)
            yield Candidate(
                key=short_name,
                position=f"{element.get('type')}/{element.get('id')}",
                values={
                    "geom": geometry.footprint,
                    # Центр пересчитывается по контуру: значение, заданное
                    # вручную, с настоящими границами может не совпадать.
                    "center": (
                        Geometry.point(*geometry.footprint.centroid)
                        if geometry.footprint is not None
                        else None
                    ),
                },
                extra={"district": registry.get(short_name), "name": name},
                payload={"name": name, "short_name": short_name},
            )


# ---------------------------------------------------------------------------
#  Объекты инфраструктуры
# ---------------------------------------------------------------------------


def _district_index() -> list[tuple[District, Geometry]]:
    """Округа с границами — для отнесения объекта по координатам."""
    return [
        (district, district.geom)
        for district in District.objects.with_geometry().exclude(geom__isnull=True)
        if district.geom is not None
    ]


def _locate_district(index: list[tuple[District, Geometry]],
                     point: Geometry) -> District | None:
    """Определить округ, в котором лежит точка."""
    lon, lat = point.coordinates
    for district, boundary in index:
        if boundary.contains(lon, lat):
            return district
    return None


def _area_fields(area_sq_m: float | None) -> tuple[float | None, str]:
    """Площадь объекта и происхождение этого значения.

    Площадь считается измеренной, если она получена из размеченного контура.
    Неправдоподобно большие значения отбрасываются: они означают, что в контур
    попала промзона целиком, и такая величина исказила бы расчёт
    обеспеченности округа складскими мощностями.
    """
    if not area_sq_m or area_sq_m <= 0:
        return None, ""
    if area_sq_m > MAX_PLAUSIBLE_AREA_SQ_M:
        return None, ""
    return round(area_sq_m, 2), DataOrigin.MEASURED


def _display_name(tags: dict[str, str], facility_type: InfrastructureType) -> str:
    """Наименование объекта для реестра.

    У значительной части объектов наименование не размечено. Придумывать его
    нельзя, поэтому такие записи получают обозначение по типу и остаются
    видимыми как неполные: это состояние исходных данных, а не системы.
    """
    name = (tags.get("name") or tags.get("official_name") or "").strip()
    if name:
        return name[:200]
    return f"{facility_type.name} (наименование не указано)"


def _within_city(candidate: Candidate) -> str | None:
    """Объект должен относиться к одному из округов.

    Выгрузка ведётся по границе субъекта, а границы округов получены отдельным
    запросом: расхождение на доли метра оставляет часть приграничных объектов
    вне всех округов. Отнесение к округу обязательно — на нём держится вся
    территориальная аналитика.
    """
    if candidate.values.get("district") is None:
        return "объект не отнесён ни к одному округу"
    return None


class InfrastructurePipeline(OverpassPipeline):
    """Реестр объектов логистической инфраструктуры."""

    name = "osm.objects"
    title = "Объекты инфраструктуры"
    target_table = "infrastructure_objects"
    description = (
        "Склады, терминалы, грузовые дворы, стоянки и автотранспортные "
        "предприятия. Отбор ведут правила etl.osm.classify: сначала "
        "исключения, затем включения."
    )
    query = queries.INFRASTRUCTURE
    model = InfrastructureObject
    supports_prune = True
    checks: tuple[Check, ...] = (
        required("geom", "Координаты объекта"),
        condition("reference.district", "Объект отнесён к округу", _within_city),
        required("name", "Наименование"),
        fits("name", 200, "Наименование"),
        not_negative("area_sq_m", "Площадь"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        osm_type, osm_id = candidate.extra["osm_key"]
        return {"osm_type": osm_type, "osm_id": osm_id}

    def extract(self, context: Context) -> Extract:
        # Правила отбора присваивают объекту тип; отсутствующий в справочнике
        # тип означал бы молчаливую потерю объектов, поэтому справочник
        # приводится к составу правил до начала загрузки.
        created = ensure_types()
        if created:
            logger.info("Справочник типов дополнен: %d записей", created)
        return super().extract(context)

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        from .geometry import extract as extract_geometry

        districts = _district_index()
        if not districts:
            raise RuntimeError(
                "Не заполнены границы округов: отнести объекты к территориям "
                "невозможно. Выполните загрузку округов."
            )

        types = {facility.code: facility for facility in InfrastructureType.objects.all()}
        source = ensure_source()

        for element in extract.records:
            key = _osm_key(element)
            tags = element.get("tags") or {}
            if key is None or not tags:
                report.skip("элемент без разметки")
                continue

            verdict = classify(tags)
            if not verdict.accepted:
                report.skip(verdict.rule)
                continue

            facility_type = types.get(verdict.type_code)
            if facility_type is None:
                report.skip(f"тип «{verdict.type_code}» отсутствует в справочнике")
                continue

            geometry = extract_geometry(element)
            if not geometry.is_located:
                report.skip("координаты не размечены")
                continue

            area, area_origin = _area_fields(geometry.area_sq_m)
            website, phone = build_contacts(tags)
            yield Candidate(
                key=f"{key[0]}/{key[1]}",
                position=f"{key[0]}/{key[1]}",
                values={
                    "type": facility_type,
                    "district": _locate_district(districts, geometry.point),
                    "name": _display_name(tags, facility_type),
                    "address": build_address(tags),
                    "operating_hours": build_opening_hours(tags),
                    "operator": build_operator(tags),
                    "website": website,
                    "phone": phone,
                    "geom": geometry.point,
                    "footprint": geometry.footprint,
                    "area_sq_m": area,
                    "area_origin": area_origin,
                    "classification_rule": verdict.rule,
                    "source": source,
                    "source_updated_at": extract.fetched_at,
                },
                extra={"osm_key": key},
                payload=tags,
            )

    def prune(self, seen: set[str], context: Context) -> int:
        """Удалить записи реестра, отсутствующие в текущей выгрузке.

        Реестр приводится к состоянию источника: уходят и объекты, снятые
        с разметки в OpenStreetMap, и записи, которых в источнике не было
        никогда. Второе существеннее первого — именно так из реестра уходят
        сведения, попавшие в него в обход загрузки.
        """
        # Ключ сравнивается целиком: совпадение по одной его составляющей
        # ничего не означает, поскольку нумерация точек, линий и отношений
        # в OpenStreetMap независима. Отбор выполняется в приложении:
        # выразить проверку вхождения пары в множество средствами ORM без
        # расширений нельзя, а размер реестра исчисляется тысячами записей.
        doomed = [
            pk
            for pk, osm_type, osm_id in InfrastructureObject.objects.values_list(
                "pk", "osm_type", "osm_id"
            ).iterator(chunk_size=2000)
            if f"{osm_type}/{osm_id}" not in seen
        ]
        if not doomed:
            return 0
        removed, _ = InfrastructureObject.objects.filter(pk__in=doomed).delete()
        return removed


# ---------------------------------------------------------------------------
#  Справочник типов
# ---------------------------------------------------------------------------

#: Типы объектов, которыми оперируют правила отбора. Справочник приводится
#: к этому составу перед загрузкой: правило, присваивающее отсутствующий тип,
#: молча отбрасывало бы объекты.
REQUIRED_TYPES: tuple[tuple[str, str, str], ...] = (
    ("warehouse", "Склад", "Складские комплексы и здания складского назначения"),
    ("terminal", "Грузовой терминал",
     "Терминалы перевалки и обработки грузов, контейнерные площадки"),
    ("cargo_yard", "Грузовой двор",
     "Грузовые дворы железнодорожных станций"),
    ("parking", "Грузовая стоянка",
     "Стоянки, открытые для движения грузового транспорта"),
    ("checkpoint", "Пункт весового контроля",
     "Пункты весового и габаритного контроля"),
    ("distribution", "Распределительный центр",
     "Распределительные, сортировочные и логистические центры"),
    ("trucking", "Автотранспортное предприятие",
     "Предприятия грузовых автомобильных перевозок: автокомбинаты, автобазы"),
)


def ensure_types() -> int:
    """Привести справочник типов к составу, которым оперируют правила."""
    created = 0
    for code, name, description in REQUIRED_TYPES:
        _, was_created = InfrastructureType.objects.update_or_create(
            code=code, defaults={"name": name, "description": description},
        )
        created += int(was_created)
    return created


__all__ = [
    "DistrictsPipeline",
    "InfrastructurePipeline",
    "OverpassPipeline",
    "ensure_source",
    "ensure_types",
]
