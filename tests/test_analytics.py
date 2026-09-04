"""Тесты аналитического ядра.

Методы: модульное тестирование расчётных функций и интеграционное
тестирование сценариев, обращающихся к базе данных.
"""

from __future__ import annotations

import math

import pytest
from analytics import services


class TestNormalization:
    """Приведение разноразмерных показателей к сопоставимому виду."""

    def test_min_max_range(self):
        """Ряд отображается на отрезок от нуля до единицы."""
        result = services.min_max_normalize([10, 20, 30, 40])
        assert result[0] == 0.0
        assert result[-1] == 1.0

    def test_min_max_preserves_order(self):
        """Нормирование не меняет порядок значений."""
        result = services.min_max_normalize([5, 1, 9, 3])
        assert result[2] == max(result)
        assert result[1] == min(result)

    def test_min_max_degenerate(self):
        """Вырожденный ряд не приводит к делению на ноль."""
        assert services.min_max_normalize([7, 7, 7]) == [0.5, 0.5, 0.5]

    def test_min_max_empty(self):
        """Пустой ряд обрабатывается без исключения."""
        assert services.min_max_normalize([]) == []

    def test_z_scores_zero_mean(self):
        """Стандартизованный ряд имеет нулевое среднее."""
        result = services.z_scores([10, 20, 30, 40, 50])
        assert sum(result) == pytest.approx(0, abs=1e-9)

    def test_z_scores_unit_deviation(self):
        """Стандартизованный ряд имеет единичное отклонение."""
        result = services.z_scores([10, 20, 30, 40, 50])
        variance = sum(v ** 2 for v in result) / len(result)
        assert math.sqrt(variance) == pytest.approx(1.0)

    def test_z_scores_degenerate(self):
        """Ряд из одинаковых значений даёт нули."""
        assert services.z_scores([4, 4, 4]) == [0.0, 0.0, 0.0]


class TestKMeans:
    """Кластеризация методом k-средних."""

    def test_separates_distinct_groups(self):
        """Две удалённые группы точек разделяются корректно."""
        points = [[0, 0], [0.1, 0.1], [0.2, 0], [10, 10], [10.1, 10], [9.9, 10.2]]
        result = services.k_means(points, k=2, seed=1)
        assert result.labels[0] == result.labels[1] == result.labels[2]
        assert result.labels[3] == result.labels[4] == result.labels[5]
        assert result.labels[0] != result.labels[3]

    def test_deterministic_with_fixed_seed(self):
        """При фиксированном зерне результат воспроизводим."""
        points = [[i, i * 2] for i in range(12)]
        first = services.k_means(points, k=3, seed=42)
        second = services.k_means(points, k=3, seed=42)
        assert first.labels == second.labels

    def test_centroid_count(self):
        """Число центров совпадает с заданным."""
        points = [[i, -i] for i in range(10)]
        assert len(services.k_means(points, k=4).centroids) == 4

    def test_k_capped_by_sample_size(self):
        """Число групп не превышает числа наблюдений."""
        result = services.k_means([[1, 1], [2, 2]], k=5)
        assert len(result.centroids) <= 2

    def test_empty_input(self):
        """Пустой набор не приводит к отказу."""
        result = services.k_means([], k=3)
        assert result.labels == [] and result.centroids == []

    def test_inertia_non_negative(self):
        """Сумма внутригрупповых расстояний неотрицательна."""
        points = [[i, i] for i in range(8)]
        assert services.k_means(points, k=2).inertia >= 0


class TestLinearRegression:
    """Оценка параметров линейной модели."""

    def test_perfect_fit(self):
        """На точных данных коэффициент детерминации равен единице."""
        xs = [0, 1, 2, 3, 4]
        ys = [2, 5, 8, 11, 14]
        intercept, slope, r_squared = services.linear_regression(xs, ys)
        assert slope == pytest.approx(3.0)
        assert intercept == pytest.approx(2.0)
        assert r_squared == pytest.approx(1.0)

    def test_negative_trend(self):
        """Убывающий ряд даёт отрицательный наклон."""
        _, slope, _ = services.linear_regression([0, 1, 2, 3], [10, 8, 6, 4])
        assert slope == pytest.approx(-2.0)

    def test_flat_series(self):
        """Постоянный ряд даёт нулевой наклон."""
        _, slope, _ = services.linear_regression([0, 1, 2, 3], [5, 5, 5, 5])
        assert slope == pytest.approx(0.0)

    def test_single_point(self):
        """Единственное наблюдение не приводит к исключению."""
        intercept, slope, r_squared = services.linear_regression([1], [7])
        assert intercept == 7 and slope == 0.0


@pytest.mark.django_db
class TestLoadIndex:
    """Композитный индекс логистической нагрузки."""

    def test_weights_sum_to_one(self):
        """Сумма весов составляющих равна единице."""
        assert sum(services.INDEX_WEIGHTS.values()) == pytest.approx(1.0)

    def test_index_computed_for_all_districts(self, full_dataset, districts):
        """Индекс рассчитывается для каждого округа выборки."""
        rows = services.load_index()
        assert len(rows) == len(districts)

    def test_score_within_scale(self, full_dataset):
        """Значения индекса не выходят за пределы стобалльной шкалы."""
        for row in services.load_index():
            assert 0 <= row["score"] <= 100

    def test_ranking_is_descending(self, full_dataset):
        """Записи упорядочены по убыванию индекса."""
        scores = [row["score"] for row in services.load_index()]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_are_sequential(self, full_dataset):
        """Ранги нумеруются подряд начиная с единицы."""
        ranks = [row["rank"] for row in services.load_index()]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_components_present(self, full_dataset):
        """Каждая запись содержит все составляющие индекса."""
        row = services.load_index()[0]
        assert set(row["components"]) == set(services.INDEX_WEIGHTS)

    def test_summary_statistics(self, full_dataset):
        """Сводка содержит среднее, размах и коэффициент вариации."""
        summary = services.index_summary()
        assert summary["count"] > 0
        assert "mean" in summary and "spread" in summary and "variation" in summary

    def test_empty_database(self, db):
        """Без данных расчёт возвращает пустой результат, а не ошибку."""
        assert services.load_index() == []


@pytest.mark.django_db
class TestComponents:
    """Состав индекса: только измеренные и только удельные величины."""

    def test_every_component_relies_on_measured_data(self):
        """Ни одна составляющая не опирается на модельную величину."""
        from core.choices import DataOrigin

        assert all(item.origin == DataOrigin.MEASURED for item in services.COMPONENTS)

    def test_congestion_is_not_a_component(self):
        """Расчётная загруженность в индекс не входит."""
        assert "congestion" not in services.INDEX_WEIGHTS

    def test_every_component_is_described(self):
        """У каждой составляющей есть единица, формула и источник."""
        for item in services.COMPONENTS:
            assert item.unit and item.formula and item.source and item.meaning

    def test_metrics_are_specific(self, full_dataset, districts):
        """Величины отнесены к размеру округа, а не взяты валовыми."""
        metrics = {row["district"].short_name: row["values"] for row in services.district_metrics()}
        for district in districts:
            values = metrics[district.short_name]
            expected = district.population / float(district.area_sq_km)
            assert values["residential"] == pytest.approx(expected)

    def test_unmeasured_component_is_not_a_zero(self, full_dataset, districts):
        """Округ без площади не получает нулевую плотность застройки."""
        from core.models import District

        District.objects.filter(pk=districts[0].pk).update(area_sq_km=None)
        services.invalidate()
        row = next(
            row for row in services.load_index()
            if row["district"].pk == districts[0].pk
        )
        assert row["measured"]["residential"] is False
        assert row["components"]["residential"] is None

    def test_inverse_component_lowers_the_score(self, full_dataset):
        """Обратная составляющая входит в индекс со сменой направления."""
        rows = services.load_index()
        best = max(rows, key=lambda row: row["raw"]["network"] or 0)
        assert best["components"]["network"] == pytest.approx(0.0)


@pytest.mark.django_db
class TestTypology:
    """Типология округов."""

    def test_clusters_created(self, full_dataset):
        """Формируется хотя бы одна группа."""
        result = services.typology(k=2)
        assert len(result["clusters"]) >= 1

    def test_every_district_assigned(self, full_dataset, districts):
        """Каждый округ попадает ровно в одну группу."""
        result = services.typology(k=2)
        members = sum(cluster["size"] for cluster in result["clusters"])
        assert members == len(districts)

    def test_clusters_ordered_by_load(self, full_dataset):
        """Группы упорядочены по возрастанию средней нагрузки."""
        clusters = services.typology(k=2)["clusters"]
        indexes = [cluster["index"] for cluster in clusters]
        assert indexes == sorted(indexes)

    def test_cluster_names_assigned(self, full_dataset):
        """Каждая группа получает содержательное наименование."""
        for cluster in services.typology(k=2)["clusters"]:
            assert cluster["name"]


@pytest.mark.django_db
class TestForecast:
    """Прогнозирование объёма грузопотока."""

    def test_forecast_available_with_history(self, full_dataset):
        """При достаточной истории прогноз формируется."""
        assert services.forecast_flow(horizon=6)["available"] is True

    def test_horizon_respected(self, full_dataset):
        """Число прогнозных точек соответствует горизонту."""
        result = services.forecast_flow(horizon=6)
        assert len(result["forecast"]) == 6

    def test_forecast_values_non_negative(self, full_dataset):
        """Прогнозные объёмы не отрицательны."""
        for row in services.forecast_flow(horizon=6)["forecast"]:
            assert row["value"] >= 0

    def test_forecast_months_are_sequential(self, full_dataset):
        """Прогнозные периоды следуют друг за другом без пропусков."""
        months = [row["month"] for row in services.forecast_flow(horizon=4)["forecast"]]
        for earlier, later in zip(months, months[1:], strict=False):
            assert later > earlier

    def test_quality_metrics_present(self, full_dataset):
        """Результат содержит показатели качества аппроксимации."""
        result = services.forecast_flow(horizon=6)
        assert "r_squared" in result and "mape" in result and result["quality"]

    def test_insufficient_history(self, db):
        """При недостатке наблюдений прогноз не строится."""
        result = services.forecast_flow(horizon=6)
        assert result["available"] is False and result["reason"]


@pytest.mark.django_db
class TestScenario:
    """Сценарное моделирование."""

    def test_baseline_unchanged(self, full_dataset):
        """Нулевые изменения не сдвигают индекс."""
        result = services.scenario(None)
        assert all(abs(row["delta"]) < 0.05 for row in result["rows"])

    def test_change_applies_to_the_chosen_district(self, full_dataset, districts):
        """Условия меняются только в выбранном округе."""
        target = districts[0]
        result = services.scenario(target.id, storage=50)
        assert result["district"] == target
        assert sum(1 for row in result["rows"] if row["target"]) == 1

    def test_growth_in_one_district_raises_its_index(self, full_dataset):
        """Прирост складских площадей повышает индекс именно этого округа."""
        rows = services.load_index()
        concentration = [row["raw"]["storage"] for row in rows if row["raw"]["storage"]]
        outsider = min(rows, key=lambda row: row["raw"]["storage"] or 0.0)
        # Прирост берётся заведомо превосходящим разброс: у отстающего округа
        # доля равна нулю, и меньший прирост оставил бы его на том же месте
        # шкалы, ничего не изменив.
        growth = max(concentration) / min(concentration) * 200

        result = services.scenario(outsider["district"].id, storage=growth)
        assert result["subject"]["score"] > outsider["score"]
        assert result["subject"]["rank"] < outsider["rank"]

    def test_scores_within_scale(self, full_dataset, districts):
        """Сценарные значения остаются в пределах шкалы."""
        for row in services.scenario(districts[0].id, storage=30, network=10)["rows"]:
            assert 0 <= row["score"] <= 100

    def test_levers_are_index_components(self):
        """Каждый рычаг сценария — составляющая индекса."""
        assert set(services.SCENARIO_LEVERS) <= set(services.INDEX_WEIGHTS)
        assert set(services.SCENARIO_HINTS) == set(services.SCENARIO_LEVERS)

    def test_unavailable_without_data(self, db):
        """Без исходных данных расчёт помечается недоступным."""
        assert services.scenario(None, storage=10)["available"] is False


@pytest.mark.django_db
class TestComparison:
    """Сопоставление округов."""

    def test_compare_returns_selected(self, full_dataset, districts):
        """В результат попадают только запрошенные округа."""
        ids = [districts[0].pk, districts[1].pk]
        result = services.compare_districts(ids)
        assert len(result["rows"]) == 2

    def test_compare_identifies_extremes(self, full_dataset, districts):
        """Определяются округа с наибольшим и наименьшим индексом."""
        result = services.compare_districts([d.pk for d in districts])
        assert result["best"]["score"] >= result["worst"]["score"]

    def test_compare_empty_selection(self, full_dataset):
        """Пустой перечень идентификаторов обрабатывается корректно."""
        assert services.compare_districts([])["available"] is False
