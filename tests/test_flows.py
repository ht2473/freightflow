"""Проверки представления рядов перевозок.

Ряды разных территорий вложены один в другой, а ряды разного круга
перевозчиков описывают разные совокупности. Ни те, ни другие складывать
нельзя, и здесь проверяется, что система этого не делает.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from analytics import services
from core import selectors
from core.choices import DataOrigin, FlowDirection, FlowScope, PeriodType
from core.models import FreightFlowStat
from django.urls import reverse

pytestmark = pytest.mark.django_db


def observation(territory: str, year: int, volume: float, turnover: float | None = None,
                scope: str = FlowScope.ALL, source=None) -> FreightFlowStat:
    return FreightFlowStat.objects.create(
        period_date=date(year, 1, 1),
        period_type=PeriodType.YEAR,
        territory=territory,
        direction=FlowDirection.TOTAL,
        scope=scope,
        volume_tons=Decimal(str(volume)),
        turnover_ton_km=Decimal(str(turnover)) if turnover is not None else None,
        origin=DataOrigin.MEASURED,
        source=source,
    )


@pytest.fixture
def statistics(db, data_source):
    """Годовые ряды по городу и охватывающим территориям."""
    rows = []
    for offset in range(10):
        year = 2016 + offset
        rows.append(observation("г. Москва", year, 40_000_000 + offset * 1_000_000,
                                8_000_000_000 + offset * 500_000_000, source=data_source))
        rows.append(observation("Российская Федерация", year, 5_000_000_000 + offset * 10_000_000,
                                source=data_source))
        rows.append(observation("Московская область", year, 60_000_000, source=data_source))
        rows.append(observation("г. Москва", year, 20_000_000 + offset * 500_000,
                                scope=FlowScope.COMMERCIAL, source=data_source))
    return rows


class TestSeries:
    """Ряд строится по одной территории."""

    def test_series_is_limited_to_the_territory(self, statistics):
        series = selectors.flow_timeseries("г. Москва", FlowScope.ALL)
        assert len(series) == 10
        assert series[0]["volume"] == 40_000_000

    def test_scopes_are_not_mixed(self, statistics):
        city_all = selectors.flow_timeseries("г. Москва", FlowScope.ALL)
        commercial = selectors.flow_timeseries("г. Москва", FlowScope.COMMERCIAL)
        assert city_all[0]["volume"] != commercial[0]["volume"]

    def test_intra_city_series_is_separate(self, statistics, flows):
        """Ряд по округам не смешивается с ведомственным рядом по городу."""
        intra = selectors.flow_timeseries()
        assert all(row["volume"] < 1_000_000 for row in intra)

    def test_average_haul_is_derived(self, statistics):
        series = selectors.flow_timeseries("г. Москва", FlowScope.ALL)
        assert series[0]["haul"] == pytest.approx(200.0, abs=0.1)

    def test_change_is_computed(self, statistics):
        series = selectors.flow_timeseries("г. Москва", FlowScope.ALL)
        assert "change_pct" not in series[0]
        assert series[1]["change_pct"] == pytest.approx(2.5, abs=0.1)

    def test_series_without_turnover_has_no_haul(self, statistics):
        series = selectors.flow_timeseries("Российская Федерация", FlowScope.ALL)
        assert series[0]["haul"] is None


class TestTerritories:
    """Перечень территорий и их сопоставление."""

    def test_city_comes_first(self, statistics):
        assert selectors.flow_territories()[0]["is_city"]

    def test_depth_is_reported(self, statistics):
        moscow = selectors.flow_territories()[0]
        assert moscow["count"] == 20
        assert moscow["first"].year == 2016

    def test_share_only_for_containing_territories(self, statistics):
        rows = {row["territory"]: row for row in selectors.flow_comparison()}
        assert rows["Российская Федерация"]["city_share"] is not None
        assert rows["Московская область"]["city_share"] is None

    def test_share_is_a_ratio_of_the_city(self, statistics):
        rows = {row["territory"]: row for row in selectors.flow_comparison()}
        russia = rows["Российская Федерация"]
        city = rows["г. Москва"]
        assert russia["city_share"] == pytest.approx(
            city["volume"] / russia["volume"] * 100, abs=0.01
        )


class TestSummary:
    """Сводка главной страницы."""

    def test_volume_is_the_latest_observation(self, statistics):
        selectors.invalidate_caches()
        summary = selectors.dashboard_summary()
        assert summary["volume_tons"] == 49_000_000
        assert summary["volume_period"].year == 2025

    def test_territories_are_not_summed(self, statistics):
        """Сумма всех строк таблицы превышает объём города на порядки."""
        selectors.invalidate_caches()
        assert selectors.dashboard_summary()["volume_tons"] < 100_000_000

    def test_city_series_falls_back_to_districts(self, flows):
        """Без ведомственного ряда берётся внутригородской."""
        selectors.invalidate_caches()
        assert selectors.city_flow_series()


class TestPage:
    """Страница статистики перевозок."""

    def test_page_opens(self, client, statistics):
        response = client.get(reverse("core:flow_overview"))
        assert response.status_code == 200
        assert response.context["territory"] == "г. Москва"

    def test_territory_can_be_chosen(self, client, statistics):
        response = client.get(
            reverse("core:flow_overview"), {"territory": "Российская Федерация"}
        )
        assert response.context["territory"] == "Российская Федерация"

    def test_unknown_territory_falls_back_to_the_city(self, client, statistics):
        response = client.get(reverse("core:flow_overview"), {"territory": "Атлантида"})
        assert response.context["territory"] == "г. Москва"

    def test_scope_switches_the_series(self, client, statistics):
        response = client.get(reverse("core:flow_overview"), {"scope": "commercial"})
        assert response.context["latest"]["volume"] == 24_500_000

    def test_empty_series_does_not_break_the_page(self, client, db):
        assert client.get(reverse("core:flow_overview")).status_code == 200


class TestForecast:
    """Прогноз по ряду территории."""

    def test_short_series_is_refused(self, db, data_source):
        for year in range(2020, 2024):
            observation("г. Москва", year, 40_000_000, source=data_source)
        result = services.forecast_flow("г. Москва")

        assert result["available"] is False
        assert "наблюдений" in str(result["reason"])

    def test_annual_series_is_forecast_by_years(self, statistics):
        result = services.forecast_flow("г. Москва", horizon=3)

        assert result["available"] is True
        assert result["granularity"] == PeriodType.YEAR
        assert [row["period"].year for row in result["forecast"]] == [2026, 2027, 2028]

    def test_annual_series_has_no_seasonal_component(self, statistics):
        """На годовом ряде внутригодового профиля нет и оценивать его нечем."""
        result = services.forecast_flow("г. Москва")
        assert result["seasonal_model"] is False
        rejected = {item["model"].code for item in result["comparison"]["rejected"]}
        assert "seasonal" in rejected

    def test_model_is_chosen_by_comparison(self, statistics):
        """Ряд продолжает модель, показавшая наименьшую ошибку на проверке."""
        result = services.forecast_flow("г. Москва")
        outcomes = result["comparison"]["outcomes"]
        assert result["model"].code == outcomes[0]["model"].code
        assert result["mae"] == round(min(item["mae"] for item in outcomes), 1)

    def test_named_model_overrides_the_comparison(self, statistics):
        """Модель можно назвать явно, не полагаясь на отбор."""
        result = services.forecast_flow("г. Москва", model_code="naive")
        assert result["model"].code == "naive"
        assert result["forecast"][0]["value"] == result["history"][-1]["volume"]

    def test_quality_is_measured_on_held_out_observations(self, statistics):
        result = services.forecast_flow("г. Москва")
        assert result["holdout"] == 2
        assert result["mape"] is not None

    def test_forecast_follows_the_trend(self, statistics):
        result = services.forecast_flow("г. Москва", horizon=1)
        assert result["forecast"][0]["value"] > result["history"][-1]["volume"]

    def test_page_opens(self, client, statistics):
        response = client.get(reverse("analytics:forecast"))
        assert response.status_code == 200
        assert response.context["result"]["available"] is True

    def test_page_reports_refusal(self, client, db):
        response = client.get(reverse("analytics:forecast"))
        assert response.status_code == 200
        assert response.context["result"]["available"] is False
