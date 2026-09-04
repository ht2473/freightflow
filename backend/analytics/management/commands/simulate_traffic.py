"""Расчёт дорожной обстановки имитационной моделью.

Примеры::

    python manage.py simulate_traffic                  # сутки назад, каждый час
    python manage.py simulate_traffic --days 14        # две недели наблюдений
    python manage.py simulate_traffic --step 3         # шаг три часа

Команда заполняет таблицу оценок обстановки расчётными значениями. Данных
о загруженности дорог Москвы в открытом доступе не существует, поэтому
значения помечаются как смоделированные и такими доходят до пользователя.
"""

from __future__ import annotations

from datetime import timedelta

from core.choices import DataOrigin
from core.models import (
    DataSource,
    InfrastructureObject,
    RestrictionZone,
    RoadSegment,
    TrafficCondition,
)
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from geo.geometry import haversine_km

from analytics.simulation import RoadContext, estimate

#: Радиус, в котором логистические объекты считаются грузогенерирующими
#: для магистрали, км.
FREIGHT_RADIUS_KM = 5.0

#: Код источника расчётных значений в реестре.
SOURCE_CODE = "model"


class Command(BaseCommand):
    help = "Рассчитать дорожную обстановку имитационной моделью"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--days", type=int, default=14,
                            help="Глубина расчёта в сутках (по умолчанию 14)")
        parser.add_argument("--step", type=int, default=1,
                            help="Шаг расчёта в часах (по умолчанию 1)")
        parser.add_argument("--replace", action="store_true",
                            help="Удалить прежние расчётные оценки")

    def handle(self, *args, **options) -> None:
        started_at = timezone.now()
        source = self._ensure_source()
        roads = list(RoadSegment.objects.exclude(geom__isnull=True))
        if not roads:
            self.stdout.write(self.style.WARNING("Реестр магистралей пуст"))
            return

        contexts = self._build_contexts(roads)
        moments = self._moments(options["days"], options["step"])

        removed = 0
        with transaction.atomic():
            if options["replace"]:
                removed, _ = TrafficCondition.objects.all().delete()
                self.stdout.write(f"Удалено прежних оценок: {removed}")

            records = []
            for road in roads:
                context = contexts[road.pk]
                for moment in moments:
                    # Профиль нагрузки привязан к распорядку дня города,
                    # поэтому расчёт ведётся по местному времени, а хранится
                    # отметка в UTC.
                    result = estimate(context, timezone.localtime(moment))
                    records.append(
                        TrafficCondition(
                            recorded_at=moment,
                            road=road,
                            congestion_level=result.congestion_level,
                            avg_speed_kmh=result.avg_speed_kmh,
                            origin=DataOrigin.MODELLED,
                            model_explanation=result.explanation[:300],
                            source=source,
                        )
                    )
            TrafficCondition.objects.bulk_create(records, batch_size=2000)

        self._journal(
            source, started_at, len(records), removed,
            f"глубина {options['days']} сут., шаг {options['step']} ч",
        )

        self.stdout.write(self.style.SUCCESS(
            f"\nРассчитано оценок: {len(records)} "
            f"({len(roads)} магистралей, {len(moments)} моментов)"
        ))
        self.stdout.write(
            "Происхождение: смоделировано. Данных о загруженности в открытом "
            "доступе не существует; значения получены моделью по измеряемым "
            "характеристикам сети."
        )

    # ------------------------------------------------------------------ детали

    @staticmethod
    def _ensure_source() -> DataSource:
        from core.choices import SourceType

        source, _ = DataSource.objects.update_or_create(
            code=SOURCE_CODE,
            defaults={
                "name": "Имитационная модель загруженности",
                "source_type": SourceType.MODEL,
                "url": "",
                # Регламента у расчёта нет: он выполняется по требованию,
                # а не по расписанию, и объявлять периодичность значило бы
                # обещать обновление, которого не происходит.
                "update_frequency": "",
                "is_active": True,
            },
        )
        return source

    @staticmethod
    def _journal(source: DataSource, started_at, written: int, removed: int,
                 note: str) -> None:
        """Записать расчёт в журнал наравне с загрузками из внешних служб.

        Расчёт наполняет таблицу системы, поэтому и виден он должен быть там же,
        где загрузки: иначе часть записей появлялась бы в базе без следа
        о том, когда и чем она получена.
        """
        from core.choices import EtlStatus, EtlTrigger
        from core.models import EtlRun

        EtlRun.objects.create(
            source=source,
            pipeline="model.traffic",
            target_table="traffic_conditions",
            trigger=EtlTrigger.CLI,
            status=EtlStatus.SUCCESS if written else EtlStatus.FAILED,
            started_at=started_at,
            finished_at=timezone.now(),
            records_created=written,
            records_loaded=written,
            records_removed=removed,
            parameters=note,
        )

    def _moments(self, days: int, step: int):
        """Моменты расчёта: назад от текущего часа с заданным шагом."""
        end = timezone.now().replace(minute=0, second=0, microsecond=0)
        count = max(1, days * 24 // max(1, step))
        return [end - timedelta(hours=step * index) for index in range(count)]

    def _build_contexts(self, roads: list[RoadSegment]) -> dict[int, RoadContext]:
        """Собрать исходные данные модели для каждой магистрали."""
        zones = list(RestrictionZone.objects.exclude(geom__isnull=True).order_by("level"))
        objects = [
            obj.geom.coordinates
            for obj in InfrastructureObject.objects.exclude(geom__isnull=True).only("geom")
        ]

        contexts: dict[int, RoadContext] = {}
        for road in roads:
            # Представительные точки магистрали: полный перебор вершин на
            # реестре из тысячи объектов и сотни магистралей обошёлся бы
            # в миллиарды сравнений, а положение магистрали в городе
            # достаточно описывается двумя десятками точек.
            points = road.geom.points
            sample = points[:: max(1, len(points) // 20)] or points[:1]

            innermost = None
            for zone in zones:
                if any(zone.contains(lon, lat) for lon, lat in sample):
                    innermost = zone.code

            nearby = sum(
                1
                for lon, lat in objects
                if any(
                    haversine_km((lon, lat), point) <= FREIGHT_RADIUS_KM
                    for point in sample
                )
            )

            contexts[road.pk] = RoadContext(
                road_class=road.road_class,
                lanes=road.lanes,
                speed_limit_kmh=road.speed_limit_kmh,
                innermost_zone=innermost,
                freight_objects_nearby=nearby,
            )
        return contexts
