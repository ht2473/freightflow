"""Проверки загрузки статистических рядов Росстата.

Книги публикации входят в поставку проекта, поэтому проверки идут на настоящих
данных и без обращения к сети. Числа в утверждениях взяты из самих книг: если
разбор листа собьётся на колонку или на строку, проверка это покажет.
"""

from __future__ import annotations

from datetime import date

import pytest
from core.choices import DataOrigin, FlowDirection, FlowScope, PeriodType
from core.models import FreightFlowStat
from etl.pipeline import Context, run
from etl.rosstat import (
    FreightStatisticsPipeline,
    RosstatError,
    SeriesSpec,
    read_series,
    strip_footnote,
)


class TestFootnotes:
    """Подписи строк и колонок несут номера сносок."""

    def test_footnote_is_stripped(self):
        assert strip_footnote("Российская Федерация1)") == "Российская Федерация"

    def test_year_footnote_is_stripped(self):
        assert strip_footnote("20253)") == "2025"

    def test_plain_label_is_untouched(self):
        assert strip_footnote("г. Москва") == "г. Москва"

    def test_empty_value(self):
        assert strip_footnote(None) == ""


CARRIAGE = SeriesSpec(
    file="avto-perev_2025.xlsx", sheet="1", measure="volume_tons", scope="all",
    unit="млн тонн", factor=1_000_000, title="Перевозка грузов",
)
TURNOVER = SeriesSpec(
    file="avto-gruz_2025.xlsx", sheet="1", measure="turnover_ton_km", scope="all",
    unit="млн тонно-км", factor=1_000_000, title="Грузооборот",
)
TERRITORIES = {"moscow": "г. Москва", "russia": "Российская Федерация"}


class TestReadSeries:
    """Чтение листа публикации."""

    def test_moscow_series_is_read(self):
        series = read_series(CARRIAGE, TERRITORIES)
        assert series[("moscow", 2000)] == pytest.approx(50_100_000)

    def test_units_are_converted_to_tons(self):
        """В книге миллионы тонн; в базе — тонны, иначе ряды несопоставимы."""
        series = read_series(CARRIAGE, TERRITORIES)
        assert series[("russia", 2000)] == pytest.approx(5_878_400_000)

    def test_series_covers_published_years(self):
        years = {year for _, year in read_series(CARRIAGE, TERRITORIES)}
        assert min(years) == 2000
        assert 2025 in years

    def test_turnover_book_is_read(self):
        series = read_series(TURNOVER, TERRITORIES)
        assert series[("moscow", 2000)] == pytest.approx(2_414_000_000)

    def test_only_named_territories_are_taken(self):
        series = read_series(CARRIAGE, {"moscow": "г. Москва"})
        assert {code for code, _ in series} == {"moscow"}

    def test_missing_book_is_reported(self):
        spec = SeriesSpec(
            file="нет-такой-книги.xlsx", sheet="1", measure="volume_tons",
            scope="all", unit="млн тонн", factor=1, title="",
        )
        with pytest.raises(RosstatError, match="отсутствует"):
            read_series(spec, TERRITORIES)

    def test_missing_sheet_is_reported(self):
        spec = SeriesSpec(
            file="avto-perev_2025.xlsx", sheet="42", measure="volume_tons",
            scope="all", unit="млн тонн", factor=1, title="",
        )
        with pytest.raises(RosstatError, match="нет листа"):
            read_series(spec, TERRITORIES)


@pytest.fixture
def loaded(db):
    return run(FreightStatisticsPipeline())


class TestLoad:
    """Наполнение ряда."""

    def test_records_are_created(self, loaded):
        assert loaded.created == FreightFlowStat.objects.count()
        assert loaded.rejected == 0

    def test_moscow_series_depth(self, loaded):
        series = FreightFlowStat.objects.filter(
            territory="г. Москва", scope=FlowScope.ALL
        )
        assert series.count() == 26

    def test_commercial_series_is_separate(self, loaded):
        assert FreightFlowStat.objects.filter(
            territory="г. Москва", scope=FlowScope.COMMERCIAL
        ).count() == 16

    def test_both_measures_land_in_one_record(self, loaded):
        record = FreightFlowStat.objects.get(
            territory="г. Москва", scope=FlowScope.ALL, period_date=date(2000, 1, 1)
        )
        assert record.volume_tons == pytest.approx(50_100_000, rel=1e-9)
        assert record.turnover_ton_km == pytest.approx(2_414_000_000, rel=1e-9)

    def test_period_is_annual(self, loaded):
        record = FreightFlowStat.objects.filter(territory="г. Москва").first()
        assert record.period_type == PeriodType.YEAR
        assert record.period_date.month == 1

    def test_direction_is_total(self, loaded):
        """Ведомственная статистика направление перевозки не разделяет."""
        assert set(
            FreightFlowStat.objects.values_list("direction", flat=True)
        ) == {FlowDirection.TOTAL}

    def test_values_are_marked_measured(self, loaded):
        assert set(
            FreightFlowStat.objects.values_list("origin", flat=True)
        ) == {DataOrigin.MEASURED}

    def test_source_is_registered(self, loaded):
        record = FreightFlowStat.objects.first()
        assert record.source.code == "rosstat"

    def test_average_haul_is_derived(self, loaded):
        record = FreightFlowStat.objects.get(
            territory="г. Москва", scope=FlowScope.ALL, period_date=date(2000, 1, 1)
        )
        assert record.average_haul_km == pytest.approx(48.2, abs=0.1)


class TestIncremental:
    """Повторная загрузка ряда не создаёт вторых записей."""

    def test_repeat_run_changes_nothing(self, loaded):
        report = run(FreightStatisticsPipeline())
        assert report.created == 0
        assert report.updated == 0
        assert report.unchanged == loaded.created

    def test_record_count_is_stable(self, loaded):
        run(FreightStatisticsPipeline())
        assert FreightFlowStat.objects.count() == loaded.created


class TestSummary:
    """Глубина ряда сообщается: от неё зависит применимость прогноза."""

    def test_depth_is_reported(self, loaded):
        assert any(
            "г. Москва" in line and "наблюдений" in line for line in loaded.details
        )

    def test_no_gaps_in_moscow_series(self, loaded):
        years = sorted(
            FreightFlowStat.objects.filter(
                territory="г. Москва", scope=FlowScope.ALL
            ).values_list("period_date__year", flat=True)
        )
        assert years == list(range(years[0], years[-1] + 1))


class TestDryRun:
    """Проверочный проход ничего не записывает."""

    def test_nothing_is_written(self, db):
        report = run(FreightStatisticsPipeline(), Context(dry_run=True))
        assert report.created > 0
        assert FreightFlowStat.objects.count() == 0
