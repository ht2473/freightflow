"""Слой выборок: агрегаты и сводки предметной области.

Модуль сосредотачивает все нетривиальные запросы к базе. Представления
получают готовые структуры данных и занимаются только подготовкой контекста
шаблона, а тестировать логику агрегации можно без обращения к HTTP-слою.

Тяжёлые сводки кешируются: набор данных обновляется процедурами загрузки не
чаще раза в час, поэтому пересчитывать их на каждый просмотр страницы
нецелесообразно.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, Max, Min, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .choices import congestion_state
from .models import (
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
    TrafficIncident,
)


def _cached(key: str, builder, ttl: int | None = None):
    """Вернуть значение из кеша либо вычислить и сохранить его."""
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, ttl or settings.ANALYTICS_CACHE_TTL)
    return value


# ---------------------------------------------------------------------------
#  Сводка главной страницы
# ---------------------------------------------------------------------------


def dashboard_summary() -> dict:
    """Ключевые показатели системы для оперативной сводки.

    Возвращает словарь с числом объектов, суммарной мощностью хранения,
    протяжённостью сети под наблюдением, текущей загруженностью и числом
    открытых инцидентов.
    """

    def build() -> dict:
        objects = InfrastructureObject.objects.aggregate(
            total=Count("id"),
            capacity=Sum("capacity_tons"),
            area=Sum("area_sq_m"),
        )
        roads = RoadSegment.objects.aggregate(total=Count("id"), length=Sum("length_km"))
        flows = FreightFlowStat.objects.aggregate(
            volume=Sum("volume_tons"),
            vehicles=Sum("vehicle_count"),
            period_from=Min("period_date"),
            period_to=Max("period_date"),
        )
        incidents_open = TrafficIncident.objects.open().count()
        incidents_cargo = TrafficIncident.objects.open().affecting_cargo().count()

        congestion = current_congestion_avg()
        code, label, tone = congestion_state(congestion)

        return {
            "object_count": objects["total"] or 0,
            "capacity_tons": objects["capacity"] or Decimal(0),
            "area_sq_m": objects["area"] or Decimal(0),
            "road_count": roads["total"] or 0,
            "road_length_km": roads["length"] or Decimal(0),
            "route_count": CargoRoute.objects.count(),
            "district_count": District.objects.count(),
            "volume_tons": flows["volume"] or Decimal(0),
            "vehicle_count": flows["vehicles"] or 0,
            "period_from": flows["period_from"],
            "period_to": flows["period_to"],
            "incidents_open": incidents_open,
            "incidents_cargo": incidents_cargo,
            "congestion_avg": congestion,
            "congestion_code": code,
            "congestion_label": label,
            "congestion_tone": tone,
            "measured_at": timezone.now(),
        }

    return _cached("dashboard:summary", build, ttl=300)


def current_congestion_avg() -> float:
    """Средняя загруженность сети по последним замерам на каждом участке.

    Усреднение выполняется по последним значениям участков, а не по всем
    записям: иначе участки с более частой телеметрией получили бы больший вес.

    При отсутствии замеров возвращается ноль, а не ``None``: показатель
    выводится на главной странице и участвует в выборе цвета по семафорной
    шкале, поэтому неопределённое значение потребовало бы дополнительной
    проверки в каждом месте использования.
    """
    latest = latest_conditions()
    values = [row.congestion_level for row in latest]
    return round(sum(values) / len(values), 2) if values else 0.0


def latest_conditions() -> list[TrafficCondition]:
    """Последний замер обстановки для каждого участка сети.

    Реализация намеренно не использует конструкцию ``DISTINCT ON``: она
    поддерживается только PostgreSQL, тогда как система должна одинаково
    работать и на SQLite. Число участков сети измеряется десятками, поэтому
    отбор по паре «участок — максимальное время» обходится дёшево, а
    результат кешируется на несколько минут.
    """

    def build() -> list[int]:
        pairs = (
            TrafficCondition.objects.values("road_id")
            .annotate(last_at=Max("recorded_at"))
            .order_by()
        )
        ids: list[int] = []
        for row in pairs:
            last_id = (
                TrafficCondition.objects.filter(
                    road_id=row["road_id"], recorded_at=row["last_at"]
                )
                .order_by("-id")
                .values_list("id", flat=True)
                .first()
            )
            if last_id:
                ids.append(last_id)
        return ids

    ids = _cached("traffic:latest_ids", build, ttl=180)
    return list(
        TrafficCondition.objects.filter(id__in=ids)
        .select_related("road", "road__district")
        .defer("road__district__geom")
        .order_by("-congestion_level", "road__name")
    )


# ---------------------------------------------------------------------------
#  Сводки по округам
# ---------------------------------------------------------------------------


def district_profiles() -> list[dict]:
    """Профили всех округов: инфраструктура, грузопоток, дороги.

    Соответствует представлению ``v_district_summary``, но собирается силами
    ORM, поэтому одинаково работает на обоих поддерживаемых бэкендах.
    """

    def build() -> list[dict]:
        base = {d.id: d for d in District.objects.all()}

        objects = {
            row["district_id"]: row
            for row in InfrastructureObject.objects.values("district_id").annotate(
                object_count=Count("id"),
                capacity=Sum("capacity_tons"),
                area=Sum("area_sq_m"),
            )
        }
        flows = {
            row["district_id"]: row
            for row in FreightFlowStat.objects.values("district_id").annotate(
                volume=Sum("volume_tons"), vehicles=Sum("vehicle_count")
            )
        }
        roads = {
            row["district_id"]: row
            for row in RoadSegment.objects.values("district_id").annotate(
                road_count=Count("id"), length=Sum("length_km")
            )
        }
        congestion = {
            row["road__district_id"]: row["avg_level"]
            for row in TrafficCondition.objects.values("road__district_id").annotate(
                avg_level=Avg("congestion_level")
            )
        }

        profiles: list[dict] = []
        for district_id, district in base.items():
            obj = objects.get(district_id, {})
            flow = flows.get(district_id, {})
            road = roads.get(district_id, {})
            level = congestion.get(district_id)
            code, label, tone = congestion_state(level)
            profiles.append(
                {
                    "district": district,
                    "object_count": obj.get("object_count", 0),
                    "capacity_tons": float(obj.get("capacity") or 0),
                    "area_sq_m": float(obj.get("area") or 0),
                    "volume_tons": float(flow.get("volume") or 0),
                    "vehicle_count": flow.get("vehicles") or 0,
                    "road_count": road.get("road_count", 0),
                    "road_length_km": float(road.get("length") or 0),
                    "congestion": round(float(level), 2) if level is not None else None,
                    "congestion_code": code,
                    "congestion_label": label,
                    "congestion_tone": tone,
                }
            )
        profiles.sort(key=lambda item: item["volume_tons"], reverse=True)
        return profiles

    return _cached("district:profiles", build)


def district_profile(district_id: int) -> dict | None:
    """Профиль конкретного округа."""
    for profile in district_profiles():
        if profile["district"].id == district_id:
            return profile
    return None


# ---------------------------------------------------------------------------
#  Временные ряды грузопотоков
# ---------------------------------------------------------------------------


def flow_timeseries(
    district_id: int | None = None,
    category_id: int | None = None,
    direction: str | None = None,
) -> list[dict]:
    """Помесячная динамика грузопотока с учётом наложенных условий отбора."""
    qs = FreightFlowStat.objects.all()
    if district_id:
        qs = qs.filter(district_id=district_id)
    if category_id:
        qs = qs.filter(cargo_category_id=category_id)
    if direction:
        qs = qs.filter(direction=direction)

    rows = (
        qs.annotate(month=TruncMonth("period_date"))
        .values("month")
        .annotate(volume=Sum("volume_tons"), vehicles=Sum("vehicle_count"), speed=Avg("avg_speed_kmh"))
        .order_by("month")
    )
    return [
        {
            "month": row["month"],
            "volume": float(row["volume"] or 0),
            "vehicles": row["vehicles"] or 0,
            "speed": round(float(row["speed"]), 1) if row["speed"] is not None else None,
        }
        for row in rows
        if row["month"]
    ]


def flow_by_category(limit: int = 10) -> list[dict]:
    """Распределение объёма перевозок по категориям грузов."""
    rows = (
        FreightFlowStat.objects.filter(cargo_category__isnull=False)
        .values("cargo_category__name", "cargo_category__hazard_class", "cargo_category_id")
        .annotate(volume=Sum("volume_tons"), vehicles=Sum("vehicle_count"))
        .order_by("-volume")[:limit]
    )
    return [
        {
            "id": row["cargo_category_id"],
            "name": row["cargo_category__name"],
            "hazard_class": row["cargo_category__hazard_class"],
            "volume": float(row["volume"] or 0),
            "vehicles": row["vehicles"] or 0,
        }
        for row in rows
    ]


def flow_by_direction() -> list[dict]:
    """Соотношение ввоза, вывоза и транзита."""
    rows = (
        FreightFlowStat.objects.values("direction")
        .annotate(volume=Sum("volume_tons"), vehicles=Sum("vehicle_count"))
        .order_by("-volume")
    )
    labels = {"in": _("Ввоз"), "out": _("Вывоз"), "transit": _("Транзит")}
    return [
        {
            "code": row["direction"],
            "label": labels.get(row["direction"], row["direction"]),
            "volume": float(row["volume"] or 0),
            "vehicles": row["vehicles"] or 0,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
#  Инфраструктура и дорожная сеть
# ---------------------------------------------------------------------------


def objects_by_type() -> list[dict]:
    """Распределение объектов инфраструктуры по типам."""
    rows = (
        InfrastructureObject.objects.values("type__name", "type__code", "type_id")
        .annotate(count=Count("id"), capacity=Sum("capacity_tons"))
        .order_by("-count")
    )
    return [
        {
            "id": row["type_id"],
            "name": row["type__name"],
            "code": row["type__code"],
            "count": row["count"],
            "capacity": float(row["capacity"] or 0),
        }
        for row in rows
    ]


def top_congested_roads(limit: int = 8) -> list[TrafficCondition]:
    """Наиболее загруженные участки по последним замерам."""
    return latest_conditions()[:limit]


def recent_incidents(limit: int = 8) -> list[TrafficIncident]:
    """Последние зарегистрированные дорожные события."""
    return list(TrafficIncident.objects.with_refs().order_by("-reported_at")[:limit])


def incident_statistics(days: int = 30) -> dict:
    """Статистика инцидентов за период: по типам и по серьёзности."""
    since = timezone.now() - timedelta(days=days)
    qs = TrafficIncident.objects.filter(reported_at__gte=since)
    by_type = list(
        qs.values("incident_type").annotate(count=Count("id")).order_by("-count")
    )
    by_severity = list(qs.values("severity").annotate(count=Count("id")).order_by("severity"))
    return {
        "total": qs.count(),
        "open": qs.filter(resolved_at__isnull=True).count(),
        "cargo": qs.filter(affects_cargo=True).count(),
        "by_type": by_type,
        "by_severity": by_severity,
        "days": days,
    }


def traffic_daily_profile(days: int = 14) -> list[dict]:
    """Средняя загруженность сети по часам суток.

    Показывает утренний и вечерний пики — базовый материал для планирования
    временных окон доставки в город.
    """
    since = timezone.now() - timedelta(days=days)
    rows = TrafficCondition.objects.filter(recorded_at__gte=since).values_list(
        "recorded_at", "congestion_level"
    )
    buckets: dict[int, list[int]] = {hour: [] for hour in range(24)}
    for recorded_at, level in rows:
        buckets[timezone.localtime(recorded_at).hour].append(level)
    return [
        {
            "hour": hour,
            "avg": round(sum(values) / len(values), 2) if values else None,
            "samples": len(values),
        }
        for hour, values in sorted(buckets.items())
    ]


# ---------------------------------------------------------------------------
#  Источники и загрузки
# ---------------------------------------------------------------------------


def etl_health(limit: int = 10) -> dict:
    """Состояние процедур загрузки данных."""
    runs = list(EtlRun.objects.select_related("source").order_by("-started_at")[:limit])
    totals = EtlRun.objects.aggregate(
        loaded=Sum("records_loaded"), errors=Sum("records_errors"), runs=Count("id")
    )
    failed = EtlRun.objects.filter(status="failed").count()
    return {
        "runs": runs,
        "total_runs": totals["runs"] or 0,
        "total_loaded": totals["loaded"] or 0,
        "total_errors": totals["errors"] or 0,
        "failed_runs": failed,
        # При отсутствии запусков доля успешных равна нулю: сообщать о
        # стопроцентной успешности там, где загрузка ни разу не выполнялась,
        # означало бы скрывать неработающий регламент обновления данных.
        "success_rate": round(
            (1 - failed / totals["runs"]) * 100 if totals["runs"] else 0.0, 1
        ),
    }


def data_coverage() -> list[dict]:
    """Наполненность основных таблиц — для страницы качества данных."""
    return [
        {"table": "infrastructure_objects", "title": _("Объекты инфраструктуры"),
         "count": InfrastructureObject.objects.count(),
         "geo": InfrastructureObject.objects.exclude(geom__isnull=True).count()},
        {"table": "road_segments", "title": _("Участки дорожной сети"),
         "count": RoadSegment.objects.count(),
         "geo": RoadSegment.objects.exclude(geom__isnull=True).count()},
        {"table": "cargo_routes", "title": _("Грузовые маршруты"),
         "count": CargoRoute.objects.count(),
         "geo": CargoRoute.objects.exclude(geom__isnull=True).count()},
        {"table": "traffic_conditions", "title": _("Замеры обстановки"),
         "count": TrafficCondition.objects.count(), "geo": None},
        {"table": "freight_flow_stats", "title": _("Показатели грузопотоков"),
         "count": FreightFlowStat.objects.count(), "geo": None},
        {"table": "traffic_incidents", "title": _("Дорожные инциденты"),
         "count": TrafficIncident.objects.count(),
         "geo": TrafficIncident.objects.exclude(geom__isnull=True).count()},
    ]


def reference_counts() -> dict:
    """Объёмы справочников — используется в панели администратора."""
    return {
        "districts": District.objects.count(),
        "types": InfrastructureType.objects.count(),
        "categories": CargoCategory.objects.count(),
        "sources": DataSource.objects.count(),
        "routes": CargoRoute.objects.count(),
    }


def invalidate_caches() -> None:
    """Сбросить кеш сводок после загрузки или изменения данных."""
    for key in ("dashboard:summary", "district:profiles", "traffic:latest_ids"):
        cache.delete(key)
