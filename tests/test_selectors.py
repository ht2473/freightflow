"""Тесты слоя агрегированных выборок.

Метод: интеграционное тестирование — проверяется корректность агрегатов,
вычисляемых средствами СУБД, и поведение механизма кеширования.
"""

from __future__ import annotations

import pytest
from core import selectors

pytestmark = pytest.mark.django_db


class TestDashboardSummary:
    """Сводка для главной страницы."""

    def test_counts_match_data(self, full_dataset, objects, districts):
        """Счётчики соответствуют фактическому наполнению базы."""
        summary = selectors.dashboard_summary()
        assert summary["object_count"] == len(objects)
        assert summary["district_count"] == len(districts)

    def test_capacity_is_sum(self, full_dataset, objects):
        """Суммарная мощность равна сумме по всем объектам."""
        expected = sum(float(row.capacity_tons or 0) for row in objects)
        assert selectors.dashboard_summary()["capacity_tons"] == pytest.approx(expected)

    def test_congestion_within_scale(self, full_dataset):
        """Средняя загруженность не выходит за пределы шкалы."""
        assert 0 <= selectors.dashboard_summary()["congestion_avg"] <= 10

    def test_open_incidents_counted(self, full_dataset, incidents):
        """Число открытых событий подсчитывается верно."""
        expected = sum(1 for row in incidents if row.resolved_at is None)
        assert selectors.dashboard_summary()["incidents_open"] == expected

    def test_empty_database(self, db):
        """На пустой базе сводка формируется без исключений."""
        summary = selectors.dashboard_summary()
        assert summary["object_count"] == 0
        assert summary["congestion_avg"] == 0


class TestLatestConditions:
    """Последние замеры дорожной обстановки."""

    def test_one_record_per_road(self, full_dataset, roads):
        """Возвращается ровно один замер на каждый участок."""
        rows = selectors.latest_conditions()
        assert len(rows) == len(roads)
        assert len({row.road_id for row in rows}) == len(roads)

    def test_returns_newest(self, full_dataset, conditions):
        """Выбирается замер с наибольшей отметкой времени."""
        rows = {row.road_id: row for row in selectors.latest_conditions()}
        for road_id, condition in rows.items():
            newest = max(
                c.recorded_at for c in conditions if c.road_id == road_id
            )
            assert condition.recorded_at == newest

    def test_related_objects_prefetched(self, full_dataset, django_assert_num_queries):
        """Связанные участки загружаются одним запросом, без N+1."""
        rows = selectors.latest_conditions()
        with django_assert_num_queries(0):
            # Обращение к связанной записи не порождает дополнительных запросов.
            [row.road.name for row in rows]


class TestDistrictProfiles:
    """Агрегированные профили округов."""

    def test_profile_for_each_district(self, full_dataset, districts):
        """Профиль формируется для каждого округа."""
        assert len(selectors.district_profiles()) == len(districts)

    def test_object_counts(self, full_dataset, objects, districts):
        """Число объектов в профиле соответствует фактическому."""
        profiles = {p["district"].id: p for p in selectors.district_profiles()}
        for district in districts:
            expected = sum(1 for row in objects if row.district_id == district.id)
            assert profiles[district.id]["object_count"] == expected

    def test_sorted_by_volume(self, full_dataset):
        """Профили упорядочены по убыванию грузопотока."""
        volumes = [p["volume_tons"] for p in selectors.district_profiles()]
        assert volumes == sorted(volumes, reverse=True)

    def test_congestion_tone_assigned(self, full_dataset):
        """Каждому профилю присвоен модификатор оформления."""
        for profile in selectors.district_profiles():
            assert profile["congestion_tone"] in {"ok", "warn", "alert", "crit", "muted"}


class TestFlowSeries:
    """Помесячный ряд грузопотока."""

    def test_series_length(self, full_dataset):
        """Ряд содержит по одной точке на каждый месяц наблюдений."""
        assert len(selectors.flow_timeseries()) == 12

    def test_months_are_ordered(self, full_dataset):
        """Точки ряда упорядочены по возрастанию периода."""
        months = [row["month"] for row in selectors.flow_timeseries()]
        assert months == sorted(months)

    def test_district_filter(self, full_dataset, districts):
        """Отбор по округу сужает ряд, не нарушая его структуру."""
        rows = selectors.flow_timeseries(district_id=districts[0].pk)
        assert all(row["volume"] > 0 for row in rows)

    def test_empty_for_unknown_district(self, full_dataset):
        """Несуществующий округ даёт пустой ряд."""
        assert selectors.flow_timeseries(district_id=999999) == []


class TestCaching:
    """Кеширование сводок."""

    def test_second_call_uses_cache(self, full_dataset, django_assert_num_queries):
        """Повторный вызов не обращается к базе."""
        selectors.dashboard_summary()
        with django_assert_num_queries(0):
            selectors.dashboard_summary()

    def test_invalidate_forces_recompute(self, full_dataset, objects,
                                         infrastructure_types, districts):
        """После сброса кеша сводка отражает изменения данных."""
        from decimal import Decimal

        from core.models import InfrastructureObject

        before = selectors.dashboard_summary()["object_count"]
        InfrastructureObject.objects.create(
            name="Новый объект", type=infrastructure_types[0],
            district=districts[0], capacity_tons=Decimal(1000),
        )
        selectors.invalidate_caches()
        assert selectors.dashboard_summary()["object_count"] == before + 1


class TestDataCoverage:
    """Показатели полноты данных."""

    def test_reports_all_tables(self, full_dataset):
        """Отчёт охватывает основные таблицы предметной области."""
        rows = selectors.data_coverage()
        assert len(rows) >= 5
        assert all("table" in row and "count" in row for row in rows)

    def test_geocoding_share(self, full_dataset, objects):
        """Число геокодированных записей подсчитывается верно."""
        rows = {row["table"]: row for row in selectors.data_coverage()}
        expected = sum(1 for row in objects if row.geom is not None)
        assert rows["infrastructure_objects"]["geo"] == expected


class TestEtlHealth:
    """Состояние процедур загрузки."""

    def test_empty_journal(self, db):
        """Пустой журнал не приводит к делению на ноль."""
        health = selectors.etl_health()
        assert health["total_runs"] == 0
        assert health["success_rate"] == 0

    def test_success_rate(self, db, data_source):
        """Доля успешных запусков рассчитывается корректно."""
        from core.models import EtlRun

        for status in ("success", "success", "success", "failed"):
            EtlRun.objects.create(
                source=data_source, target_table="objects",
                records_loaded=10, records_errors=0, status=status,
            )
        assert selectors.etl_health()["success_rate"] == pytest.approx(75.0)
