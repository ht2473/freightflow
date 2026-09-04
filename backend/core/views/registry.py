"""Реестры предметной области и карточки записей.

Раздел объединяет страницы, построенные по единому образцу «список с отбором
и сортировкой → карточка записи»: объекты инфраструктуры, административные
округа, участки дорожной сети, грузовые маршруты и справочники.
"""

from __future__ import annotations

from django.db.models import Avg, Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from geo import nearest

from .. import selectors
from ..choices import RoadClass, RouteType
from ..models import (
    CargoCategory,
    CargoRoute,
    DataSource,
    District,
    EtlRun,
    FreightFlowStat,
    InfrastructureObject,
    InfrastructureType,
    RoadSegment,
    TrafficCondition,
)
from .base import (
    apply_sort,
    choice_param,
    int_param,
    minimap_settings,
    page_context,
    paginate,
    working_area,
)

# Разрешённые варианты сортировки реестра объектов: код → выражение ORM.
OBJECT_SORTS = {
    "name": "name",
    "-name": "-name",
    "capacity": "-capacity_tons,name",
    "area": "-area_sq_m,name",
    "district": "district__name,name",
    "type": "type__name,name",
}


def object_list(request):
    """Реестр объектов логистической инфраструктуры."""
    queryset = InfrastructureObject.objects.with_refs()

    # Условия отбора считываются из адресной строки — это делает любое
    # состояние реестра адресуемым и пригодным для сохранения в кабинете.
    district_id, area_district = working_area(request)
    type_id = int_param(request, "type")
    source_id = int_param(request, "source")
    term = (request.GET.get("q") or "").strip()
    only_round_clock = request.GET.get("round_clock") == "1"

    queryset = queryset.in_district(district_id).of_type(type_id).search(term)
    if source_id:
        queryset = queryset.filter(source_id=source_id)
    if only_round_clock:
        queryset = queryset.filter(
            Q(operating_hours__icontains="кругл") | Q(operating_hours="00:00-24:00")
        )

    queryset = apply_sort(queryset, request, OBJECT_SORTS, "name")
    totals = queryset.aggregate(capacity=Sum("capacity_tons"), area=Sum("area_sq_m"))

    context = page_context(
        request,
        title=_("Реестр объектов инфраструктуры"),
        lead=_(
            "Складские комплексы, грузовые терминалы, распределительные центры "
            "и площадки временного размещения грузов на территории Москвы."
        ),
        active="objects",
        crumbs=[(_("Инфраструктура"), "core:object_list"), (_("Реестр объектов"),)],
        export_dataset="objects",
        export_geojson=True,
        page_obj=paginate(request, queryset),
        total_count=queryset.count(),
        total_capacity=totals["capacity"] or 0,
        total_area=totals["area"] or 0,
        districts=District.objects.all(),
        area_district=area_district,
        types=InfrastructureType.objects.all(),
        sources=DataSource.objects.filter(is_active=True),
        filters={
            "district": district_id,
            "type": type_id,
            "source": source_id,
            "q": term,
            "round_clock": only_round_clock,
            "sort": request.GET.get("sort", "name"),
        },
    )
    return render(request, "pages/object_list.html", context)


def object_detail(request, pk: int):
    """Карточка объекта инфраструктуры."""
    obj = get_object_or_404(
        InfrastructureObject.objects.with_footprint(), pk=pk
    )

    # Соседние объекты в радиусе — показывают локальную концентрацию мощностей.
    neighbours: list = []
    if obj.geom is not None:
        candidates = (
            InfrastructureObject.objects.with_refs().located().exclude(pk=obj.pk)
        )
        neighbours = [
            {"object": item, "distance_km": round(distance, 2)}
            for item, distance in nearest(candidates, obj.geom.lon, obj.geom.lat, 3.0, 8)
        ]

    same_district = (
        InfrastructureObject.objects.filter(district_id=obj.district_id)
        .exclude(pk=obj.pk)
        .select_related("type")[:6]
    )
    district_stats = selectors.district_profile(obj.district_id)

    context = page_context(
        request,
        minimap=minimap_settings(obj.geom, zoom=14),
        title=obj.name,
        lead=obj.address or _("Адрес не указан в источнике данных"),
        active="objects",
        crumbs=[
            (_("Инфраструктура"), "core:object_list"),
            (_("Реестр объектов"), "core:object_list"),
            (obj.name,),
        ],
        object=obj,
        neighbours=neighbours,
        same_district=same_district,
        district_stats=district_stats,
    )
    return render(request, "pages/object_detail.html", context)


def district_list(request):
    """Профили административных округов."""
    profiles = selectors.district_profiles()
    # Неизмеренные величины в итог не входят и остаются неопределёнными:
    # сумма из одних пропусков — не ноль, а отсутствие сведений.
    totals = {
        "objects": sum(p["object_count"] for p in profiles),
        "capacity": _total(profiles, "capacity_tons"),
        "volume": _total(profiles, "volume_tons"),
        "roads": _total(profiles, "road_length_km"),
    }
    context = page_context(
        request,
        title=_("Административные округа"),
        lead=_(
            "Сравнительные профили двенадцати округов Москвы: обеспеченность "
            "складскими мощностями, объёмы грузопотока и загруженность дорог."
        ),
        active="districts",
        crumbs=[(_("Инфраструктура"), "core:object_list"), (_("Округа"),)],
        export_dataset="districts",
        export_geojson=True,
        profiles=profiles,
        totals=totals,
        max_volume=max((p["volume_tons"] or 0 for p in profiles), default=0) or 1,
        max_objects=max((p["object_count"] for p in profiles), default=0) or 1,
    )
    return render(request, "pages/district_list.html", context)


def _total(profiles: list[dict], key: str) -> float | None:
    """Сумма измеренных значений; ``None``, если не измерено ни одного."""
    values = [profile[key] for profile in profiles if profile[key]]
    return sum(values) if values else None


def district_detail(request, pk: int):
    """Карточка административного округа."""
    district = get_object_or_404(District, pk=pk)
    profile = selectors.district_profile(pk) or {}

    objects_by_type = (
        InfrastructureObject.objects.filter(district_id=pk)
        .values("type__name", "type__code")
        .annotate(count=Count("id"), capacity=Sum("capacity_tons"))
        .order_by("-count")
    )
    roads = RoadSegment.objects.filter(district_id=pk).order_by("name")
    timeseries = selectors.flow_timeseries(district_id=pk)
    largest = (
        InfrastructureObject.objects.filter(district_id=pk)
        .select_related("type")
        .order_by("-capacity_tons")[:8]
    )

    context = page_context(
        request,
        title=f"{district.name} административный округ",
        lead=(
            f"Профиль округа {district.short_name}: инфраструктура, дорожная сеть "
            "и динамика грузопотока."
        ),
        active="districts",
        crumbs=[
            (_("Инфраструктура"), "core:object_list"),
            (_("Округа"), "core:district_list"),
            (district.short_name,),
        ],
        district=district,
        profile=profile,
        objects_by_type=list(objects_by_type),
        roads=roads,
        timeseries=timeseries,
        largest_objects=largest,
    )
    return render(request, "pages/district_detail.html", context)


def type_list(request):
    """Классификатор типов объектов инфраструктуры."""
    rows = selectors.objects_by_type()
    types = {t.id: t for t in InfrastructureType.objects.all()}
    for row in rows:
        row["type"] = types.get(row["id"])
    context = page_context(
        request,
        title=_("Типы объектов инфраструктуры"),
        lead=_(
            "Классификатор, по которому ведётся учёт объектов: от складских "
            "комплексов до весовых пунктов контроля."
        ),
        active="types",
        crumbs=[(_("Инфраструктура"), "core:object_list"), (_("Типы объектов"),)],
        rows=rows,
        total=sum(row["count"] for row in rows),
    )
    return render(request, "pages/type_list.html", context)


def cargo_list(request):
    """Классификатор категорий грузов."""
    volumes = {row["id"]: row for row in selectors.flow_by_category(limit=100)}
    categories = []
    for category in CargoCategory.objects.all():
        stats = volumes.get(category.id, {})
        categories.append(
            {
                "category": category,
                "volume": stats.get("volume", 0.0),
                "vehicles": stats.get("vehicles", 0),
            }
        )
    categories.sort(key=lambda item: item["volume"], reverse=True)

    context = page_context(
        request,
        title=_("Категории перевозимых грузов"),
        lead=_(
            "Классификатор грузов с указанием класса опасности по ДОПОГ. "
            "Перевозка опасных грузов требует согласования маршрута и времени."
        ),
        active="cargo",
        crumbs=[(_("Грузопотоки"), "core:flow_overview"), (_("Категории грузов"),)],
        categories=categories,
        max_volume=max((c["volume"] for c in categories), default=0) or 1,
        hazardous_count=sum(1 for c in categories if c["category"].is_hazardous),
    )
    return render(request, "pages/cargo_list.html", context)


ROAD_SORTS = {
    "name": "name",
    "length": "-length_km,name",
    "lanes": "-lanes,name",
    "speed": "-speed_limit_kmh,name",
}


def road_list(request):
    """Реестр участков улично-дорожной сети."""
    queryset = RoadSegment.objects.select_related("district", "source").defer(
        "district__geom"
    )

    district_id, area_district = working_area(request)
    road_class = choice_param(request, "class", RoadClass.values)
    term = (request.GET.get("q") or "").strip()

    if district_id:
        queryset = queryset.filter(district_id=district_id)
    if road_class:
        queryset = queryset.filter(road_class=road_class)
    if term:
        queryset = queryset.filter(name__icontains=term)

    queryset = apply_sort(queryset, request, ROAD_SORTS, "name")

    # Текущее состояние участков подтягивается одним словарём, чтобы список
    # не выполнял отдельный запрос на каждую строку.
    conditions = {c.road_id: c for c in selectors.latest_conditions()}
    page = paginate(request, queryset)
    for road in page.object_list:
        road.current = conditions.get(road.id)

    context = page_context(
        request,
        title=_("Участки дорожной сети"),
        lead=_(
            "Магистрали и городские улицы, включённые в систему мониторинга "
            "грузового движения, с текущей оценкой загруженности."
        ),
        active="roads",
        crumbs=[(_("Дорожная сеть"), "core:road_list"), (_("Участки сети"),)],
        export_dataset="roads",
        export_geojson=True,
        page_obj=page,
        total_count=queryset.count(),
        total_length=queryset.aggregate(total=Sum("length_km"))["total"] or 0,
        districts=District.objects.all(),
        area_district=area_district,
        road_classes=RoadClass.choices,
        filters={
            "district": district_id,
            "class": road_class,
            "q": term,
            "sort": request.GET.get("sort", "name"),
        },
    )
    return render(request, "pages/road_list.html", context)


def road_detail(request, pk: int):
    """Карточка участка дорожной сети."""
    road = get_object_or_404(
        RoadSegment.objects.select_related("district", "source").defer("district__geom"),
        pk=pk,
    )

    history = list(
        TrafficCondition.objects.filter(road_id=pk).order_by("-recorded_at")[:200]
    )
    incidents = road.incidents.order_by("-reported_at")[:10]
    stats = TrafficCondition.objects.filter(road_id=pk).aggregate(
        avg_level=Avg("congestion_level"),
        avg_speed=Avg("avg_speed_kmh"),
        max_level=Max("congestion_level"),
        samples=Count("id"),
    )

    context = page_context(
        request,
        minimap=minimap_settings(road.geom),
        title=road.name,
        lead=f"{road.get_road_class_display()} · {road.length_km or '—'} км",
        active="roads",
        crumbs=[
            (_("Дорожная сеть"), "core:road_list"),
            (_("Участки сети"), "core:road_list"),
            (road.name,),
        ],
        road=road,
        current=road.latest_condition,
        history=list(reversed(history)),
        incidents=incidents,
        stats=stats,
    )
    return render(request, "pages/road_detail.html", context)


ROUTE_SORTS = {
    "name": "name",
    "distance": "-distance_km,name",
    "trucks": "-truck_count_day,name",
    "duration": "-avg_duration_h,name",
}


def route_list(request):
    """Реестр грузовых маршрутов."""
    queryset = CargoRoute.objects.select_related("source")

    route_type = choice_param(request, "type", RouteType.values)
    term = (request.GET.get("q") or "").strip()
    if route_type:
        queryset = queryset.filter(route_type=route_type)
    if term:
        queryset = queryset.filter(
            Q(name__icontains=term)
            | Q(origin_region__icontains=term)
            | Q(destination__icontains=term)
        )

    queryset = apply_sort(queryset, request, ROUTE_SORTS, "name")
    totals = queryset.aggregate(distance=Sum("distance_km"), trucks=Sum("truck_count_day"))

    by_type = list(
        CargoRoute.objects.values("route_type")
        .annotate(count=Count("id"), trucks=Sum("truck_count_day"))
        .order_by("-trucks")
    )

    context = page_context(
        request,
        title=_("Грузовые маршруты"),
        lead=_(
            "Транспортные коридоры ввоза, вывоза и транзита грузов через "
            "Московский транспортный узел."
        ),
        active="routes",
        crumbs=[(_("Грузопотоки"), "core:flow_overview"), (_("Грузовые маршруты"),)],
        export_dataset="routes",
        export_geojson=True,
        page_obj=paginate(request, queryset),
        total_count=queryset.count(),
        total_distance=totals["distance"] or 0,
        total_trucks=totals["trucks"] or 0,
        by_type=by_type,
        route_types=RouteType.choices,
        filters={"type": route_type, "q": term, "sort": request.GET.get("sort", "name")},
    )
    return render(request, "pages/route_list.html", context)


def route_detail(request, pk: int):
    """Карточка грузового маршрута."""
    route = get_object_or_404(CargoRoute.objects.select_related("source"), pk=pk)
    flows = (
        FreightFlowStat.objects.filter(route_id=pk)
        .select_related("cargo_category", "district")
        .defer("district__geom")
        .order_by("-period_date")[:24]
    )
    by_category = (
        FreightFlowStat.objects.filter(route_id=pk, cargo_category__isnull=False)
        .values("cargo_category__name")
        .annotate(volume=Sum("volume_tons"))
        .order_by("-volume")[:8]
    )
    similar = (
        CargoRoute.objects.filter(route_type=route.route_type)
        .exclude(pk=pk)
        .order_by("-truck_count_day")[:6]
    )

    context = page_context(
        request,
        minimap=minimap_settings(route.geom),
        title=route.name,
        lead=f"{route.get_route_type_display()} · {route.distance_km or '—'} км",
        active="routes",
        crumbs=[
            (_("Грузопотоки"), "core:flow_overview"),
            (_("Грузовые маршруты"), "core:route_list"),
            (route.name,),
        ],
        route=route,
        flows=flows,
        by_category=list(by_category),
        similar=similar,
    )
    return render(request, "pages/route_detail.html", context)


def source_list(request):
    """Реестр источников данных, интегрированных в систему."""
    sources = DataSource.objects.all()
    runs = {
        row["source_id"]: row
        for row in EtlRun.objects.values("source_id").annotate(
            runs=Count("id"),
            loaded=Sum("records_loaded"),
            errors=Sum("records_errors"),
            last_at=Max("started_at"),
        )
    }
    rows = []
    for source in sources:
        stats = runs.get(source.id, {})
        rows.append(
            {
                "source": source,
                "runs": stats.get("runs", 0),
                "loaded": stats.get("loaded", 0) or 0,
                "errors": stats.get("errors", 0) or 0,
                "last_at": stats.get("last_at"),
            }
        )

    context = page_context(
        request,
        title=_("Источники данных"),
        lead=_(
            "Система консолидирует сведения из ведомственных информационных "
            "систем, открытых данных и результатов натурных обследований."
        ),
        active="sources",
        crumbs=[(_("Данные"), "core:source_list"), (_("Источники данных"),)],
        rows=rows,
        active_count=sum(1 for row in rows if row["source"].is_active),
        coverage=selectors.data_coverage(),
    )
    return render(request, "pages/source_list.html", context)


def source_detail(request, pk: int):
    """Карточка источника данных."""
    source = get_object_or_404(DataSource, pk=pk)
    runs = source.etl_runs.order_by("-started_at")[:30]
    aggregates = source.etl_runs.aggregate(
        total=Count("id"), loaded=Sum("records_loaded"), errors=Sum("records_errors")
    )
    usage = {
        "objects": source.facilities.count(),
        "roads": source.roads.count(),
        "routes": source.routes.count(),
        "incidents": source.incidents.count(),
        "flows": source.flow_stats.count(),
        "traffic": source.traffic_records.count(),
    }

    context = page_context(
        request,
        title=source.name,
        lead=f"{source.get_source_type_display()} · обновление: {source.get_update_frequency_display() or '—'}",
        active="sources",
        crumbs=[
            (_("Данные"), "core:source_list"),
            (_("Источники данных"), "core:source_list"),
            (source.name,),
        ],
        source=source,
        runs=runs,
        aggregates=aggregates,
        usage=usage,
    )
    return render(request, "pages/source_detail.html", context)


def etl_log(request):
    """Журнал загрузок данных."""
    queryset = EtlRun.objects.select_related("source")

    status = choice_param(request, "status", ["running", "success", "partial", "failed"])
    source_id = int_param(request, "source")
    if status:
        queryset = queryset.filter(status=status)
    if source_id:
        queryset = queryset.filter(source_id=source_id)

    context = page_context(
        request,
        title=_("Журнал загрузок данных"),
        lead=_(
            "Хронология обновления сведений: время выполнения, объём "
            "загруженных записей и число отклонённых строк."
        ),
        active="etl",
        crumbs=[(_("Данные"), "core:source_list"), (_("Журнал загрузок"),)],
        page_obj=paginate(request, queryset.order_by("-started_at")),
        health=selectors.etl_health(limit=1),
        sources=DataSource.objects.all(),
        filters={"status": status, "source": source_id},
    )
    return render(request, "pages/etl_log.html", context)
