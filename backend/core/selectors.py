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
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .choices import FlowDirection, FlowScope, congestion_state
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

#: Территория, показатели которой представляет система.
#:
#: Значение совпадает с подписью строки в статистической публикации: по ней
#: ряд города отделяется от рядов сравнения — области, федерального округа
#: и страны. Складывать их нельзя, город входит в каждый из остальных.
CITY_TERRITORY = "г. Москва"

#: Территории, в состав которых входит город. Доля города в их объёме
#: осмысленна и показывается; для смежных территорий — например, для области —
#: такое отношение не означает ничего, и вместо него ставится прочерк.
TERRITORIES_CONTAINING_CITY = frozenset(
    {"Центральный федеральный округ", "Российская Федерация"}
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
        # Показатель перевозок берётся последним наблюдением по городу,
        # а не суммой всех строк таблицы: в ней лежат ряды нескольких
        # территорий за десятки лет, и их сумма не измеряет ничего.
        series = city_flow_series()
        latest = series[-1] if series else None
        incidents_open = TrafficIncident.objects.open().count()
        incidents_cargo = TrafficIncident.objects.open().affecting_cargo().count()

        congestion = current_congestion_avg()
        code, label, tone = congestion_state(congestion)

        return {
            "object_count": objects["total"] or 0,
            "capacity_tons": objects["capacity"] or None,
            "area_sq_m": objects["area"] or None,
            "road_count": roads["total"] or 0,
            "road_length_km": roads["length"] or Decimal(0),
            "route_count": CargoRoute.objects.count(),
            "district_count": District.objects.count(),
            "volume_tons": latest["volume"] if latest else 0.0,
            "turnover_ton_km": latest["turnover"] if latest else 0.0,
            "volume_period": latest["period"] if latest else None,
            "volume_period_type": latest["period_type"] if latest else "",
            "vehicle_count": latest["vehicles"] if latest else 0,
            "period_from": series[0]["period"] if series else None,
            "period_to": series[-1]["period"] if series else None,
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
                    # Неизмеренная величина остаётся неопределённой, а не
                    # нулевой: ноль означал бы, что мощности нет, тогда как
                    # она попросту не публикуется источником.
                    "capacity_tons": float(obj["capacity"]) if obj.get("capacity") else None,
                    "area_sq_m": float(obj["area"]) if obj.get("area") else None,
                    "volume_tons": float(flow["volume"]) if flow.get("volume") else None,
                    "vehicle_count": flow.get("vehicles") or None,
                    "road_count": road.get("road_count", 0),
                    "road_length_km": float(road.get("length") or 0),
                    "congestion": round(float(level), 2) if level is not None else None,
                    "congestion_code": code,
                    "congestion_label": label,
                    "congestion_tone": tone,
                }
            )
        # Упорядочение по измеренным величинам: грузопоток известен не всегда,
        # и при его отсутствии округа выстраиваются по складским площадям.
        profiles.sort(
            key=lambda item: (
                item["volume_tons"] or 0,
                item["area_sq_m"] or 0,
                item["object_count"],
            ),
            reverse=True,
        )
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


def flow_territories() -> list[dict]:
    """Территории, по которым в системе есть ряды перевозок.

    Территория города идёт первой: она предмет системы, остальные служат
    основанием для сравнения.
    """
    rows = (
        FreightFlowStat.objects.exclude(territory="")
        .values("territory")
        .annotate(count=Count("id"), first=Min("period_date"), last=Max("period_date"))
        .order_by("territory")
    )
    territories = [
        {
            "name": row["territory"],
            "count": row["count"],
            "first": row["first"],
            "last": row["last"],
            "is_city": row["territory"] == CITY_TERRITORY,
        }
        for row in rows
    ]
    return sorted(territories, key=lambda item: (not item["is_city"], item["name"]))


def flow_timeseries(
    territory: str | None = None,
    scope: str | None = None,
    direction: str | None = None,
    district_id: int | None = None,
    category_id: int | None = None,
) -> list[dict]:
    """Ряд перевозок по одной территории.

    Наблюдения разных территорий не складываются: город входит в федеральный
    округ, округ — в страну, и сумма таких рядов не измеряет ничего. Поэтому
    ряд строится по одной территории, а не по всем сразу; та же причина
    запрещает складывать ряд по всем перевозчикам с рядом перевозок
    на коммерческой основе.

    Наряду с объёмом возвращается грузооборот и выведенное из них среднее
    расстояние перевозки: рост объёма при падении грузооборота означает
    укорочение плеча доставки, и различить эти случаи по одному ряду нельзя.
    """
    # Отсутствие территории означает внутригородской ряд: он собран по округам
    # и маршрутам, и территория у него не заполняется. Без явного условия
    # в такой ряд попали бы и ведомственные наблюдения по городу целиком.
    qs = FreightFlowStat.objects.filter(territory=territory or "")
    if scope:
        qs = qs.filter(scope=scope)
    if direction:
        qs = qs.filter(direction=direction)
    if district_id:
        qs = qs.filter(district_id=district_id)
    if category_id:
        qs = qs.filter(cargo_category_id=category_id)

    rows = (
        qs.values("period_date", "period_type")
        .annotate(
            volume=Sum("volume_tons"),
            turnover=Sum("turnover_ton_km"),
            vehicles=Sum("vehicle_count"),
            speed=Avg("avg_speed_kmh"),
        )
        .order_by("period_date")
    )

    series = []
    for row in rows:
        volume = float(row["volume"] or 0)
        turnover = float(row["turnover"] or 0)
        series.append(
            {
                "period": row["period_date"],
                # Ключ сохранён ради общего построителя графиков: он ожидает
                # поле с датой под этим именем.
                "month": row["period_date"],
                "period_type": row["period_type"],
                "volume": volume,
                "turnover": turnover,
                "haul": round(turnover / volume, 1) if volume and turnover else None,
                "vehicles": row["vehicles"] or 0,
                "speed": round(float(row["speed"]), 1) if row["speed"] is not None else None,
            }
        )

    # Изменение к предыдущему наблюдению — величина, которую иначе пришлось бы
    # считать в шаблоне.
    for previous, current in zip(series, series[1:], strict=False):
        if previous["volume"]:
            current["change_pct"] = (current["volume"] / previous["volume"] - 1) * 100
    return series


def city_flow_series(scope: str = FlowScope.ALL) -> list[dict]:
    """Ряд перевозок по городу.

    Берётся ведомственный ряд по территории города; при его отсутствии —
    внутригородской, собранный по округам. Одно или другое, но не оба сразу:
    это одна и та же величина, полученная разными способами, и складывать
    их значило бы учесть перевозки дважды.
    """
    return flow_timeseries(CITY_TERRITORY, scope) or flow_timeseries(scope=scope)


def flow_by_scope(territory: str | None = None) -> list[dict]:
    """Последнее наблюдение по каждому кругу перевозчиков.

    Ряды различаются в разы: первый учитывает перевозки предприятий
    для собственных нужд, второй — только выполненные за плату. Показанные
    рядом, они дают представление о доле коммерческого рынка.
    """
    rows = []
    for scope, label in FlowScope.choices:
        latest = flow_latest(territory or CITY_TERRITORY, scope)
        if latest and latest["volume"]:
            rows.append({"code": scope, "label": label, "volume": latest["volume"],
                         "period": latest["period"]})
    return rows


def flow_latest(territory: str | None = None, scope: str = FlowScope.ALL) -> dict | None:
    """Последнее наблюдение ряда по территории."""
    series = flow_timeseries(territory or CITY_TERRITORY, scope)
    return series[-1] if series else None


def flow_comparison(scope: str = FlowScope.ALL) -> list[dict]:
    """Последние наблюдения по всем территориям — для сопоставления.

    Территории вложены одна в другую, поэтому рядом с объёмом показывается
    доля города в нём: это единственное осмысленное соотношение между такими
    рядами.
    """
    city = flow_latest(CITY_TERRITORY, scope)
    rows = []
    for territory in flow_territories():
        latest = flow_latest(territory["name"], scope)
        if latest is None:
            continue
        contains_city = territory["name"] in TERRITORIES_CONTAINING_CITY
        share = None
        if city and latest["volume"] and contains_city:
            share = city["volume"] / latest["volume"] * 100
        rows.append(
            {
                "territory": territory["name"],
                "is_city": territory["is_city"],
                "contains_city": contains_city,
                "period": latest["period"],
                "volume": latest["volume"],
                "turnover": latest["turnover"],
                "haul": latest["haul"],
                "city_share": share,
            }
        )
    return rows


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


def flow_by_direction(territory: str | None = None,
                      scope: str | None = None) -> list[dict]:
    """Соотношение направлений перевозки в пределах одной территории."""
    qs = FreightFlowStat.objects.all()
    if territory:
        qs = qs.filter(territory=territory)
    if scope:
        qs = qs.filter(scope=scope)

    rows = (
        qs.values("direction")
        .annotate(volume=Sum("volume_tons"), vehicles=Sum("vehicle_count"))
        .order_by("-volume")
    )
    labels = dict(FlowDirection.choices)
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


#: Счётчик поколений данных карты. Входит в ключ каждого собранного тайла:
#: перечислить тайлы по одному невозможно — квадратов сетки миллионы, —
#: а смена поколения делает недействительными сразу все.
TILE_GENERATION_KEY = "map:tiles:generation"


def tile_generation() -> int:
    """Текущее поколение данных карты."""
    return cache.get_or_set(TILE_GENERATION_KEY, 1, None) or 1


def invalidate_tiles() -> None:
    """Объявить собранные тайлы устаревшими."""
    try:
        cache.incr(TILE_GENERATION_KEY)
    except ValueError:
        # Счётчика в кеше нет — значит, нет и собранных по нему тайлов.
        cache.set(TILE_GENERATION_KEY, 1, None)


def invalidate_caches() -> None:
    """Сбросить кеш сводок после загрузки или изменения данных."""
    for key in ("dashboard:summary", "district:profiles", "traffic:latest_ids"):
        cache.delete(key)
    invalidate_tiles()
