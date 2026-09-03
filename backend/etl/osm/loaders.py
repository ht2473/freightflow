"""Загрузка данных OpenStreetMap в реестры системы.

Каждый загрузчик выполняет одну и ту же последовательность: получает выгрузку,
отбирает подходящие элементы, приводит их к доменной модели и записывает,
обновляя уже существующие записи по ключу исходного элемента. Повторный запуск
поэтому не создаёт дубликатов и приводит реестр к текущему состоянию
источника.

Итог загрузки возвращается структурой :class:`LoadReport`, а не печатается:
одна и та же процедура вызывается и из командной строки, и из панели
администратора, и из регламентного задания.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from core.choices import DataOrigin, OsmElement, SourceType, UpdateFrequency
from core.models import DataSource, District, InfrastructureObject, InfrastructureType
from django.db import transaction
from django.utils import timezone
from geo.geometry import Geometry

from ..client import OverpassClient
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


@dataclass
class LoadReport:
    """Итог загрузки одного набора данных."""

    dataset: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    unlocated: int = 0
    removed: int = 0
    from_cache: bool = False
    fetched_at: datetime | None = None
    notes: list[str] = field(default_factory=list)
    rejected_by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def written(self) -> int:
        return self.created + self.updated

    def summary(self) -> str:
        return (
            f"{self.dataset}: получено {self.fetched}, записано {self.written} "
            f"(создано {self.created}, обновлено {self.updated}), "
            f"отклонено {self.skipped}, без координат {self.unlocated}, "
            f"удалено {self.removed}"
        )


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


def load_districts(client: OverpassClient, refresh: bool = False) -> LoadReport:
    """Заполнить границы административных округов.

    Округа уже присутствуют в справочнике, но размечены одними центрами.
    Загрузка добавляет к ним настоящие границы, без которых невозможны ни
    картограмма, ни отнесение объекта к округу, ни расчёт плотности
    размещения складских мощностей.
    """
    from .geometry import extract

    report = LoadReport(dataset="Административные округа")
    response = client.fetch(queries.DISTRICTS, refresh=refresh)
    report.fetched = response.count
    report.from_cache = response.from_cache
    report.fetched_at = response.fetched_at

    with transaction.atomic():
        for element in response.elements:
            name = (element.get("tags", {}).get("name") or "").strip()
            short_name = DISTRICT_SHORT_NAMES.get(name)
            if not short_name:
                report.skipped += 1
                report.notes.append(f"округ «{name}» отсутствует в сопоставлении")
                continue

            district = District.objects.filter(short_name=short_name).first()
            if district is None:
                report.skipped += 1
                report.notes.append(f"округ {short_name} отсутствует в справочнике")
                continue

            geometry = extract(element)
            if geometry.footprint is None:
                report.unlocated += 1
                report.notes.append(f"контур округа {short_name} не собран")
                continue

            district.geom = geometry.footprint
            # Центр пересчитывается по контуру: прежнее значение задавалось
            # вручную и с настоящими границами могло не совпадать.
            district.center = Geometry.point(*geometry.footprint.centroid)
            district.save(update_fields=["geom", "center"])
            report.updated += 1

    return report


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


def load_infrastructure(client: OverpassClient, refresh: bool = False,
                        prune: bool = False) -> LoadReport:
    """Заполнить реестр объектов логистической инфраструктуры.

    Выгрузка шире итогового реестра: отбор выполняют правила
    :mod:`etl.osm.classify`, и каждое решение сохраняется вместе с объектом.
    Объекты, для которых не удалось определить округ, в реестр не попадают:
    отнесение к округу — обязательный признак, на нём строится вся
    территориальная аналитика.
    """
    from .geometry import extract

    report = LoadReport(dataset="Объекты инфраструктуры")
    response = client.fetch(queries.INFRASTRUCTURE, refresh=refresh)
    report.fetched = response.count
    report.from_cache = response.from_cache
    report.fetched_at = response.fetched_at

    districts = _district_index()
    if not districts:
        raise RuntimeError(
            "Не заполнены границы округов: отнести объекты к территориям "
            "невозможно. Выполните загрузку округов."
        )

    types = {t.code: t for t in InfrastructureType.objects.all()}
    source = ensure_source()
    present: set[tuple[str, int]] = set()

    with transaction.atomic():
        for element in response.elements:
            key = _osm_key(element)
            tags = element.get("tags") or {}
            if key is None or not tags:
                report.skipped += 1
                continue

            verdict = classify(tags)
            if not verdict.accepted:
                report.skipped += 1
                report.rejected_by_rule[verdict.rule] = (
                    report.rejected_by_rule.get(verdict.rule, 0) + 1
                )
                continue

            facility_type = types.get(verdict.type_code)
            if facility_type is None:
                report.skipped += 1
                report.notes.append(f"тип «{verdict.type_code}» отсутствует в справочнике")
                continue

            geometry = extract(element)
            if not geometry.is_located:
                report.unlocated += 1
                continue

            district = _locate_district(districts, geometry.point)
            if district is None:
                # Выгрузка ведётся по границе субъекта, а границы округов
                # получены отдельным запросом: расхождение на доли метра
                # оставляет часть приграничных объектов вне всех округов.
                report.unlocated += 1
                continue

            created = _write_facility(
                element=element,
                key=key,
                tags=tags,
                verdict=verdict,
                geometry=geometry,
                facility_type=facility_type,
                district=district,
                source=source,
                fetched_at=response.fetched_at,
            )
            if created:
                report.created += 1
            else:
                report.updated += 1
            present.add(key)

        if prune:
            report.removed = _prune(present)

    return report


def _prune(present: set[tuple[str, int]]) -> int:
    """Удалить записи реестра, отсутствующие в текущей выгрузке.

    Реестр приводится к состоянию источника: удаляются и объекты, снятые
    с разметки в OpenStreetMap, и записи, которых в источнике не было
    никогда. Второе существеннее первого — именно так из реестра уходят
    сведения, попавшие в него в обход загрузки.
    """
    # Ключ сравнивается целиком: совпадение по одной его составляющей ничего
    # не означает, поскольку нумерация точек, линий и отношений независима.
    # Отбор выполняется в приложении, а не запросом: выразить в SQL проверку
    # вхождения пары в множество средствами ORM без расширений нельзя,
    # а размер реестра исчисляется тысячами записей.
    doomed = [
        pk
        for pk, osm_type, osm_id in InfrastructureObject.objects.values_list(
            "pk", "osm_type", "osm_id"
        ).iterator(chunk_size=2000)
        if (osm_type, osm_id) not in present
    ]
    if not doomed:
        return 0
    removed, _ = InfrastructureObject.objects.filter(pk__in=doomed).delete()
    return removed


def _write_facility(*, element, key, tags, verdict, geometry, facility_type,
                    district, source, fetched_at) -> bool:
    """Записать объект, обновив существующий по ключу исходного элемента."""
    osm_type, osm_id = key

    area, area_origin = _area_fields(geometry.area_sq_m)

    values = {
        "type": facility_type,
        "district": district,
        "name": _display_name(tags, facility_type),
        "address": build_address(tags),
        "operating_hours": build_opening_hours(tags),
        "operator": build_operator(tags),
        "geom": geometry.point,
        "footprint": geometry.footprint,
        "area_sq_m": area,
        "area_origin": area_origin,
        "classification_rule": verdict.rule,
        "source": source,
        "source_updated_at": fetched_at,
    }
    values["website"], values["phone"] = build_contacts(tags)

    _, created = InfrastructureObject.objects.update_or_create(
        osm_type=osm_type, osm_id=osm_id, defaults=values,
    )
    return created


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


def log_run(report: LoadReport, source: DataSource, target_table: str,
            started_at: datetime) -> None:
    """Записать итог загрузки в журнал.

    Отклонённые элементы учитываются как ошибочные записи: с точки зрения
    загрузки это данные, поступившие из источника и не попавшие в реестр,
    и их доля — показатель качества выгрузки.
    """
    from core.choices import EtlStatus
    from core.models import EtlRun

    status = EtlStatus.SUCCESS if report.written else EtlStatus.FAILED
    if report.written and (report.unlocated or report.notes):
        status = EtlStatus.PARTIAL

    EtlRun.objects.create(
        source=source,
        target_table=target_table,
        status=status,
        started_at=started_at,
        finished_at=timezone.now(),
        records_loaded=report.written,
        records_errors=report.skipped + report.unlocated,
        error_message="; ".join(report.notes[:5]),
    )
