"""Оперативный мониторинг: дорожная обстановка и журнал инцидентов."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .. import selectors
from ..choices import FlowDirection, FlowScope, IncidentType, congestion_state
from ..models import District, TrafficCondition, TrafficIncident
from .base import choice_param, int_param, page_context, paginate


def traffic(request):
    """Сводная страница дорожной обстановки.

    Совмещает три среза: текущее состояние каждого участка, суточный профиль
    загруженности за две недели и распределение участков по состояниям.
    """
    conditions = selectors.latest_conditions()

    district_id = int_param(request, "district")
    if district_id:
        conditions = [c for c in conditions if c.road.district_id == district_id]

    # Распределение участков по состояниям движения — основа легенды и сводки.
    distribution: dict[str, dict] = {}
    for condition in conditions:
        code, label, tone = condition.state
        bucket = distribution.setdefault(code, {"label": label, "tone": tone, "count": 0})
        bucket["count"] += 1

    daily = selectors.traffic_daily_profile(days=14)
    peak = max((row for row in daily if row["avg"] is not None), key=lambda r: r["avg"], default=None)

    week_ago = timezone.now() - timedelta(days=7)
    weekly = TrafficCondition.objects.filter(recorded_at__gte=week_ago).aggregate(
        avg_level=Avg("congestion_level"),
        avg_speed=Avg("avg_speed_kmh"),
        samples=Count("id"),
    )

    average = (
        round(sum(c.congestion_level for c in conditions) / len(conditions), 2)
        if conditions
        else None
    )
    code, label, tone = congestion_state(average)

    context = page_context(
        request,
        title=_("Дорожная обстановка"),
        lead=_(
            "Загруженность улично-дорожной сети по последним замерам системы "
            "мониторинга. Шкала — от 0 (свободно) до 10 (движение остановлено)."
        ),
        active="traffic",
        crumbs=[(_("Дорожная сеть"), "core:road_list"), (_("Дорожная обстановка"),)],
        conditions=conditions,
        conditions_count=len(conditions) or 1,
        distribution=sorted(distribution.values(), key=lambda item: -item["count"]),
        daily=daily,
        peak=peak,
        weekly=weekly,
        average=average,
        average_label=label,
        average_tone=tone,
        districts=District.objects.all(),
        filters={"district": district_id},
        open_incidents=TrafficIncident.objects.open().count(),
    )
    return render(request, "pages/traffic.html", context)


def incident_list(request):
    """Журнал дорожных инцидентов с отбором по типу, состоянию и округу."""
    queryset = TrafficIncident.objects.with_refs()

    incident_type = choice_param(request, "type", IncidentType.values)
    state = choice_param(request, "state", ["open", "closed"])
    district_id = int_param(request, "district")
    severity = int_param(request, "severity")
    cargo_only = request.GET.get("cargo") == "1"

    if incident_type:
        queryset = queryset.filter(incident_type=incident_type)
    if state == "open":
        queryset = queryset.filter(resolved_at__isnull=True)
    elif state == "closed":
        queryset = queryset.filter(resolved_at__isnull=False)
    if district_id:
        queryset = queryset.filter(district_id=district_id)
    if severity:
        queryset = queryset.filter(severity__gte=severity)
    if cargo_only:
        queryset = queryset.filter(affects_cargo=True)

    context = page_context(
        request,
        title=_("Дорожные инциденты"),
        lead=_(
            "Происшествия, ремонтные работы и ограничения движения, влияющие "
            "на прохождение грузового транспорта по территории города."
        ),
        active="incidents",
        crumbs=[(_("Дорожная сеть"), "core:road_list"), (_("Инциденты"),)],
        export_dataset="incidents",
        export_geojson=True,
        page_obj=paginate(request, queryset.order_by("-reported_at")),
        total_count=queryset.count(),
        statistics=selectors.incident_statistics(days=30),
        incident_types=IncidentType.choices,
        districts=District.objects.all(),
        filters={
            "type": incident_type,
            "state": state,
            "district": district_id,
            "severity": severity,
            "cargo": cargo_only,
        },
    )
    return render(request, "pages/incident_list.html", context)


def incident_detail(request, pk: int):
    """Карточка дорожного инцидента."""
    incident = get_object_or_404(
        TrafficIncident.objects.select_related("road", "district", "source").defer(
            "district__geom"
        ),
        pk=pk,
    )

    # Обстановка на участке в окрестности события помогает оценить его влияние.
    context_window = []
    if incident.road_id:
        window_start = incident.reported_at - timedelta(hours=6)
        window_end = (incident.resolved_at or timezone.now()) + timedelta(hours=6)
        context_window = list(
            TrafficCondition.objects.filter(
                road_id=incident.road_id,
                recorded_at__gte=window_start,
                recorded_at__lte=window_end,
            ).order_by("recorded_at")[:120]
        )

    nearby = (
        TrafficIncident.objects.filter(road_id=incident.road_id)
        .exclude(pk=pk)
        .order_by("-reported_at")[:6]
        if incident.road_id
        else []
    )

    context = page_context(
        request,
        title=incident.get_incident_type_display(),
        lead=incident.description or _("Описание не предоставлено источником данных"),
        active="incidents",
        crumbs=[
            (_("Дорожная сеть"), "core:road_list"),
            (_("Инциденты"), "core:incident_list"),
            (f"№ {incident.pk}",),
        ],
        incident=incident,
        context_window=context_window,
        nearby=nearby,
    )
    return render(request, "pages/incident_detail.html", context)


def flow_overview(request):
    """Ряд перевозок грузов автомобильным транспортом.

    Показывается ряд одной территории. Складывать наблюдения города, области,
    округа и страны нельзя — территории вложены одна в другую, — поэтому
    территория и круг перевозчиков задаются условиями отбора, а не сводятся
    в один итог.
    """
    territories = selectors.flow_territories()
    names = [item["name"] for item in territories]
    territory = request.GET.get("territory", "").strip()
    if territory not in names:
        territory = selectors.CITY_TERRITORY if selectors.CITY_TERRITORY in names else (
            names[0] if names else ""
        )

    scope = choice_param(request, "scope", FlowScope.values) or FlowScope.ALL
    direction = choice_param(request, "direction", FlowDirection.values)

    # Условия отбора предлагаются только те, под которые в ряду есть
    # наблюдения: пустой выбор вводит в заблуждение сильнее, чем его
    # отсутствие.
    by_direction = [
        row for row in selectors.flow_by_direction(territory, scope) if row["volume"]
    ]
    series = selectors.flow_timeseries(territory, scope, direction)
    latest = series[-1] if series else None
    previous = series[-2] if len(series) >= 2 else None

    change = None
    if latest and previous and previous["volume"]:
        change = (latest["volume"] / previous["volume"] - 1) * 100

    context = page_context(
        request,
        title=_("Статистика перевозок грузов"),
        lead=_(
            "Объёмы перевозок и грузооборот автомобильного транспорта "
            "по данным государственной статистики. Ряды территорий "
            "показываются раздельно: территории вложены одна в другую "
            "и сложению не подлежат."
        ),
        active="flows",
        crumbs=[(_("Грузопотоки"), "core:flow_overview"), (_("Статистика"),)],
        export_dataset="flows",
        timeseries=series,
        latest=latest,
        change=change,
        depth=len(series),
        by_direction=by_direction,
        comparison=selectors.flow_comparison(scope),
        territories=territories,
        territory=territory,
        scopes=FlowScope.choices,
        directions=[(row["code"], row["label"]) for row in by_direction],
        districts=District.objects.all(),
        filters={"territory": territory, "scope": scope, "direction": direction},
    )
    return render(request, "pages/flow_overview.html", context)
