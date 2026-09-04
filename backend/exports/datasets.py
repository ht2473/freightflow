"""Наборы данных, доступные для выгрузки.

Каждый набор описывает состав колонок и способ получения записей с учётом
условий отбора. Условия совпадают с параметрами соответствующих страниц —
пользователь выгружает ровно то, что видит на экране.
"""

from __future__ import annotations

from collections.abc import Callable

from core import selectors
from core.models import (
    CargoRoute,
    FreightFlowStat,
    InfrastructureObject,
    RoadSegment,
    TrafficIncident,
)

from .builders import Column, Dataset


def _int(params, name: str) -> int | None:
    """Прочитать целочисленный параметр отбора."""
    raw = params.get(name)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
#  Объекты инфраструктуры
# ---------------------------------------------------------------------------


def objects_dataset(params) -> Dataset:
    """Реестр объектов логистической инфраструктуры."""
    queryset = (
        InfrastructureObject.objects.with_refs()
        .in_district(_int(params, "district"))
        .of_type(_int(params, "type"))
        .search(params.get("q"))
        .order_by("name")
    )
    rows = list(queryset)

    total_capacity = sum(float(row.capacity_tons or 0) for row in rows)
    total_area = sum(float(row.area_sq_m or 0) for row in rows)

    return Dataset(
        code="objects",
        title="Реестр объектов логистической инфраструктуры",
        description=(
            "Складские комплексы, грузовые терминалы, распределительные центры "
            "и площадки временного размещения грузов на территории Москвы."
        ),
        columns=[
            Column("Наименование", lambda o: o.name, width=42),
            Column("Тип объекта", lambda o: o.type.name, width=26),
            Column("Округ", lambda o: o.district.short_name, width=10),
            Column("Адрес", lambda o: o.address or "", width=46),
            Column("Мощность, т", lambda o: o.capacity_tons, width=14, numeric=True),
            Column("Площадь, м²", lambda o: o.area_sq_m, width=14, numeric=True),
            Column("Режим работы", lambda o: o.operating_hours or "", width=18),
            Column("Широта", lambda o: round(o.geom.lat, 6) if o.geom else None,
                   width=12, numeric=True),
            Column("Долгота", lambda o: round(o.geom.lon, 6) if o.geom else None,
                   width=12, numeric=True),
            Column("Источник данных", lambda o: o.source.name if o.source_id else "", width=30),
        ],
        rows=rows,
        summary=[
            ("Число объектов", len(rows)),
            ("Суммарная мощность хранения, т", round(total_capacity, 2)),
            ("Суммарная площадь, м²", round(total_area, 2)),
            ("С указанными координатами", sum(1 for row in rows if row.geom)),
        ],
    )


# ---------------------------------------------------------------------------
#  Округа
# ---------------------------------------------------------------------------


def districts_dataset(params) -> Dataset:
    """Профили административных округов."""
    profiles = selectors.district_profiles()

    return Dataset(
        code="districts",
        title="Профили административных округов",
        description=(
            "Сравнительные показатели двенадцати округов Москвы: обеспеченность "
            "складскими мощностями, объёмы грузопотока и загруженность сети."
        ),
        columns=[
            Column("Округ", lambda p: p["district"].name, width=26),
            Column("Аббревиатура", lambda p: p["district"].short_name, width=12),
            Column("Площадь, км²", lambda p: p["district"].area_sq_km, width=14, numeric=True),
            Column("Население, чел.", lambda p: p["district"].population, width=16, numeric=True),
            Column("Объектов", lambda p: p["object_count"], width=11, numeric=True),
            Column("Мощность, т", lambda p: round(p["capacity_tons"], 2), width=16, numeric=True),
            Column("Грузопоток, т", lambda p: round(p["volume_tons"], 2), width=16, numeric=True),
            Column("Рейсов", lambda p: p["vehicle_count"], width=12, numeric=True),
            Column("Дороги, км", lambda p: round(p["road_length_km"], 2), width=13, numeric=True),
            Column("Загруженность", lambda p: p["congestion"], width=14, numeric=True),
            Column("Состояние движения", lambda p: p["congestion_label"], width=26),
        ],
        rows=profiles,
        summary=[
            ("Округов в выборке", len(profiles)),
            ("Объектов всего", sum(p["object_count"] for p in profiles)),
            ("Суммарный грузопоток, т", round(sum(p["volume_tons"] for p in profiles), 2)),
        ],
    )


# ---------------------------------------------------------------------------
#  Дорожная сеть и события
# ---------------------------------------------------------------------------


def roads_dataset(params) -> Dataset:
    """Участки улично-дорожной сети с текущей обстановкой."""
    queryset = (
        RoadSegment.objects.select_related("district")
        .defer("district__geom")
        .order_by("name")
    )
    district = _int(params, "district")
    if district:
        queryset = queryset.filter(district_id=district)
    if params.get("class"):
        queryset = queryset.filter(road_class=params["class"])

    rows = list(queryset)
    conditions = {c.road_id: c for c in selectors.latest_conditions()}

    return Dataset(
        code="roads",
        title="Участки улично-дорожной сети",
        description=(
            "Магистрали и городские улицы под мониторингом с последней "
            "зарегистрированной оценкой загруженности движения."
        ),
        columns=[
            Column("Участок", lambda r: r.name, width=40),
            Column("Класс дороги", lambda r: r.get_road_class_display(), width=34),
            Column("Округ", lambda r: r.district.short_name if r.district_id else "", width=10),
            Column("Длина, км", lambda r: r.length_km, width=12, numeric=True),
            Column("Полос", lambda r: r.lanes, width=9, numeric=True),
            Column("Скорость, км/ч", lambda r: r.speed_limit_kmh, width=15, numeric=True),
            Column("Балл загруженности",
                   lambda r: conditions[r.id].congestion_level if r.id in conditions else None,
                   width=18, numeric=True),
            Column("Скорость потока, км/ч",
                   lambda r: conditions[r.id].avg_speed_kmh if r.id in conditions else None,
                   width=20, numeric=True),
            Column("Время замера",
                   lambda r: conditions[r.id].recorded_at if r.id in conditions else None,
                   width=20),
        ],
        rows=rows,
        summary=[
            ("Участков в выборке", len(rows)),
            ("Суммарная протяжённость, км",
             round(sum(float(r.length_km or 0) for r in rows), 2)),
            ("Участков с замерами", sum(1 for r in rows if r.id in conditions)),
        ],
    )


def incidents_dataset(params) -> Dataset:
    """Журнал дорожных инцидентов."""
    queryset = TrafficIncident.objects.with_refs().order_by("-reported_at")
    if params.get("type"):
        queryset = queryset.filter(incident_type=params["type"])
    if params.get("state") == "open":
        queryset = queryset.filter(resolved_at__isnull=True)
    elif params.get("state") == "closed":
        queryset = queryset.filter(resolved_at__isnull=False)
    if params.get("cargo") == "1":
        queryset = queryset.filter(affects_cargo=True)
    severity = _int(params, "severity")
    if severity:
        queryset = queryset.filter(severity__gte=severity)

    rows = list(queryset)

    return Dataset(
        code="incidents",
        title="Журнал дорожных инцидентов",
        description=(
            "Происшествия, ремонтные работы и ограничения движения, влияющие "
            "на прохождение грузового транспорта."
        ),
        columns=[
            Column("Номер", lambda i: i.pk, width=9, numeric=True),
            Column("Тип события", lambda i: i.get_incident_type_display(), width=34),
            Column("Серьёзность", lambda i: i.severity, width=13, numeric=True),
            Column("Оценка", lambda i: i.severity_state[0], width=18),
            Column("Участок", lambda i: i.road.name if i.road_id else "", width=36),
            Column("Округ",
                   lambda i: i.road.district.short_name
                   if i.road_id and i.road.district_id else "", width=10),
            Column("Зарегистрирован", lambda i: i.reported_at, width=20),
            Column("Устранён", lambda i: i.resolved_at, width=20),
            Column("Длительность, ч",
                   lambda i: round(i.duration_hours, 1) if i.duration_hours else None,
                   width=16, numeric=True),
            Column("Грузовой транспорт", lambda i: i.affects_cargo, width=19),
            Column("Описание", lambda i: i.description or "", width=52),
        ],
        rows=rows,
        summary=[
            ("Событий в выборке", len(rows)),
            ("Открытых", sum(1 for row in rows if row.is_open)),
            ("Влияющих на грузовой транспорт", sum(1 for row in rows if row.affects_cargo)),
        ],
    )


# ---------------------------------------------------------------------------
#  Грузопотоки и маршруты
# ---------------------------------------------------------------------------


def flows_dataset(params) -> Dataset:
    """Статистика грузопотоков по периодам."""
    queryset = FreightFlowStat.objects.select_related(
        "district", "cargo_category", "route"
    ).order_by("-period_date")

    district = _int(params, "district")
    category = _int(params, "category")
    if district:
        queryset = queryset.filter(district_id=district)
    if category:
        queryset = queryset.filter(cargo_category_id=category)
    if params.get("direction"):
        queryset = queryset.filter(direction=params["direction"])
    if params.get("territory"):
        queryset = queryset.filter(territory=params["territory"])
    if params.get("scope"):
        queryset = queryset.filter(scope=params["scope"])

    rows = list(queryset)
    # Объёмы суммируются только в пределах одной территории: территории
    # вложены одна в другую, и общий итог по ним не измеряет ничего.
    territories = {row.territory for row in rows}
    total_volume = (
        sum(float(row.volume_tons or 0) for row in rows) if len(territories) <= 1 else None
    )

    return Dataset(
        code="flows",
        title="Статистика грузопотоков",
        description=(
            "Объёмы перевозок и грузооборот по периодам, территориям и кругу "
            "перевозчиков по данным государственной статистики."
        ),
        columns=[
            Column("Период", lambda f: f.period_date.strftime("%m.%Y"), width=12),
            Column("Тип периода", lambda f: f.get_period_type_display(), width=14),
            Column("Территория", lambda f: f.territory, width=30),
            Column("Круг перевозчиков", lambda f: f.get_scope_display(), width=22),
            Column("Направление", lambda f: f.get_direction_display(), width=14),
            Column("Округ", lambda f: f.district.short_name if f.district_id else "", width=10),
            Column("Категория груза",
                   lambda f: f.cargo_category.name if f.cargo_category_id else "", width=32),
            Column("Маршрут", lambda f: f.route.name if f.route_id else "", width=36),
            Column("Объём, т", lambda f: f.volume_tons, width=15, numeric=True),
            Column("Грузооборот, т·км", lambda f: f.turnover_ton_km, width=20, numeric=True),
            Column("Среднее плечо, км",
                   lambda f: round(f.average_haul_km, 1) if f.average_haul_km else None,
                   width=18, numeric=True),
            Column("Рейсов", lambda f: f.vehicle_count, width=12, numeric=True),
            Column("Средняя скорость, км/ч", lambda f: f.avg_speed_kmh, width=21, numeric=True),
            Column("Происхождение", lambda f: f.get_origin_display() or "", width=16),
        ],
        rows=rows,
        summary=[
            ("Записей в выборке", len(rows)),
            ("Территорий в выборке", len(territories)),
            (
                "Суммарный объём, т",
                round(total_volume, 2) if total_volume is not None
                else "не суммируется: территории вложены одна в другую",
            ),
        ],
    )


def routes_dataset(params) -> Dataset:
    """Реестр грузовых маршрутов."""
    queryset = CargoRoute.objects.order_by("name")
    if params.get("type"):
        queryset = queryset.filter(route_type=params["type"])
    rows = list(queryset)

    return Dataset(
        code="routes",
        title="Грузовые маршруты",
        description="Транспортные коридоры ввоза, вывоза и транзита грузов.",
        columns=[
            Column("Маршрут", lambda r: r.name, width=42),
            Column("Тип", lambda r: r.get_route_type_display(), width=16),
            Column("Регион отправления", lambda r: r.origin_region or "", width=26),
            Column("Регион назначения", lambda r: r.destination or "", width=26),
            Column("Протяжённость, км", lambda r: r.distance_km, width=18, numeric=True),
            Column("Время в пути, ч", lambda r: r.avg_duration_h, width=16, numeric=True),
            Column("Интенсивность, ТС/сут", lambda r: r.truck_count_day, width=21, numeric=True),
        ],
        rows=rows,
        summary=[
            ("Маршрутов в выборке", len(rows)),
            ("Суммарная протяжённость, км",
             round(sum(float(r.distance_km or 0) for r in rows), 2)),
            ("Суммарная интенсивность, ТС/сут",
             sum(r.truck_count_day or 0 for r in rows)),
        ],
    )


#: Реестр наборов данных: код → (построитель, функция получения геометрии).
#: Второй элемент указывается только для наборов, пригодных к выгрузке в GeoJSON.
DATASETS: dict[str, tuple[Callable, Callable | None]] = {
    "objects": (objects_dataset, lambda row: row.geom),
    "districts": (districts_dataset, lambda row: row["district"].center),
    "roads": (roads_dataset, lambda row: row.geom),
    "incidents": (incidents_dataset, lambda row: row.geom),
    "routes": (routes_dataset, lambda row: row.geom),
    "flows": (flows_dataset, None),
}

#: Человекочитаемые наименования наборов для интерфейса выбора.
DATASET_TITLES: dict[str, str] = {
    "objects": "Объекты инфраструктуры",
    "districts": "Профили округов",
    "roads": "Участки дорожной сети",
    "incidents": "Дорожные инциденты",
    "routes": "Грузовые маршруты",
    "flows": "Статистика грузопотоков",
}
