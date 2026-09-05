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


class TestRankCorrelation:
    """Коэффициенты корреляции."""

    def test_identical_rankings(self):
        """Совпадающие ранжирования дают единицу."""
        assert services.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0

    def test_reversed_rankings(self):
        """Противоположные ранжирования дают минус единицу."""
        assert services.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)

    def test_single_element(self):
        """Ранжирование из одного элемента сравнивать не с чем."""
        assert services.spearman([1], [1]) == 1.0

    def test_pearson_on_linear_relation(self):
        """Линейно связанные ряды дают единицу."""
        assert services.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)

    def test_pearson_on_constant_column(self):
        """Постоянный ряд ни с чем не связан."""
        assert services.pearson([1, 2, 3], [5, 5, 5]) == 0.0


@pytest.mark.django_db
class TestSensitivity:
    """Устойчивость ранжирования к выбору весов."""

    def test_expert_scheme_matches_itself(self, full_dataset):
        """Действующий набор весов совпадает с самим собой в точности."""
        expert = next(
            scheme for scheme in services.sensitivity()["schemes"]
            if scheme["code"] == "expert"
        )
        assert expert["correlation"] == 1.0
        assert expert["max_shift"] == 0

    def test_every_scheme_sums_to_one(self, full_dataset):
        """Каждый набор весов приведён к единичной сумме."""
        for scheme in services.sensitivity()["schemes"]:
            assert sum(scheme["weights"].values()) == pytest.approx(1.0)

    def test_entropy_weights_favour_the_telling_component(self, full_dataset):
        """Различающая округа составляющая получает больший вес.

        Составляющая, равная у всех, о них ничего не сообщает: её вес
        по методу энтропии обязан оказаться наименьшим.
        """
        rows = services.load_index()
        for row in rows:
            row["shares"]["residential"] = 0.5

        weights = services.entropy_weights(rows)
        assert weights["residential"] == min(weights.values())

    def test_perturbation_covers_both_directions(self, full_dataset):
        """Вес каждой составляющей испытывается в обе стороны."""
        result = services.sensitivity()
        keys = [row["component"].key for row in result["perturbations"]]
        assert len(keys) == len(services.COMPONENTS) * 2
        assert set(keys) == set(services.INDEX_WEIGHTS)

    def test_correlations_cover_every_pair(self, full_dataset):
        """Взаимная связь проверяется у каждой пары составляющих."""
        count = len(services.COMPONENTS)
        assert len(services.sensitivity()["correlations"]) == count * (count - 1) // 2

    def test_unavailable_without_data(self, db):
        """Без индекса анализ помечается недоступным."""
        assert services.sensitivity()["available"] is False


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


class TestClusterNaming:
    """Название группы выводится из её положения в признаковом пространстве."""

    def test_leading_trait_gives_the_name(self):
        """Группу называет составляющая, по которой она выделяется."""
        keys = [item.key for item in services.COMPONENTS]
        centroid = [2.0 if key == "restrictions" else 0.0 for key in keys]
        assert services.cluster_name(centroid) == services.CLUSTER_TRAITS["restrictions"]

    def test_uniformly_low_group(self):
        """Группа, отстающая по всем составляющим, названа так и есть."""
        low = [-1.0] * len(services.COMPONENTS)
        assert "низкой нагрузки" in str(services.cluster_name(low))

    def test_uniformly_high_group(self):
        """То же для группы, ведущей по всем составляющим."""
        high = [1.0] * len(services.COMPONENTS)
        assert "высокой нагрузки" in str(services.cluster_name(high))

    def test_group_without_a_trait(self):
        """Группе без отклонений черта не приписывается."""
        flat = [0.05] * len(services.COMPONENTS)
        assert "без выраженного профиля" in str(services.cluster_name(flat))


class TestSilhouette:
    """Силуэт как мера обоснованности разбиения."""

    def test_separated_groups_score_high(self):
        """Далеко разнесённые группы дают силуэт, близкий к единице."""
        points = [[0, 0], [0.1, 0], [10, 10], [10.1, 10]]
        assert services.silhouette(points, [0, 0, 1, 1]) > 0.9

    def test_mixed_labels_score_low(self):
        """Разбиение поперёк настоящих групп силуэта не получает."""
        points = [[0, 0], [0.1, 0], [10, 10], [10.1, 10]]
        assert services.silhouette(points, [0, 1, 0, 1]) < 0

    def test_single_group_is_not_measurable(self):
        """Одна группа сравнивать себя не с чем."""
        assert services.silhouette([[0, 0], [1, 1], [2, 2]], [0, 0, 0]) == 0.0

    def test_verdict_reports_weak_structure(self):
        """Слабая структура называется слабой, а не выдаётся за группы."""
        assert "не обнаружено" in str(services.silhouette_verdict(0.1))
        assert "слабо" in str(services.silhouette_verdict(0.3))


@pytest.mark.django_db
class TestClusterQuality:
    """Обоснование числа групп."""

    def test_every_size_is_examined(self, full_dataset):
        """Перебираются все допустимые числа групп."""
        steps = services.cluster_quality()["steps"]
        assert [step["k"] for step in steps] == list(services.CLUSTER_RANGE)

    def test_inertia_never_grows(self, full_dataset):
        """Разброс внутри групп с их числом только убывает.

        Свойство и есть причина, по которой одной суммой расстояний число
        групп обосновать нельзя.
        """
        inertia = [step["inertia"] for step in services.cluster_quality()["steps"]]
        assert inertia == sorted(inertia, reverse=True)

    def test_recommended_size_has_the_best_silhouette(self, full_dataset):
        """Предлагается число групп с наибольшим силуэтом."""
        result = services.cluster_quality()
        best = max(result["steps"], key=lambda step: step["silhouette"])
        assert result["recommended"] == best["k"]
        assert best["recommended"] is True

    def test_group_sizes_cover_the_sample(self, full_dataset, districts):
        """Размеры групп в сумме дают всю выборку."""
        for step in services.cluster_quality()["steps"]:
            assert sum(step["sizes"]) == len(districts)


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
        """Результат содержит меры ошибки на отложенной выборке."""
        result = services.forecast_flow(horizon=6)
        assert result["mae"] is not None and result["rmse"] is not None
        assert result["quality"]

    def test_insufficient_history(self, db):
        """При недостатке наблюдений прогноз не строится."""
        result = services.forecast_flow(horizon=6)
        assert result["available"] is False and result["reason"]


class TestErrorMeasures:
    """Меры ошибки прогноза."""

    def test_absolute_error(self):
        assert services.mean_absolute_error([10, 20], [12, 18]) == 2.0

    def test_squared_error_weights_large_misses(self):
        """Корень из средней квадратичной не меньше средней абсолютной."""
        actual, fitted = [10, 20, 30], [10, 20, 45]
        assert services.root_mean_squared_error(actual, fitted) > \
            services.mean_absolute_error(actual, fitted)

    def test_percentage_error(self):
        assert services.mean_absolute_percentage_error([100, 200], [110, 180]) == \
            pytest.approx(10.0)

    def test_percentage_error_undefined_on_zero_series(self):
        """Процентная ошибка на нулевом ряде не определена, а не равна нулю."""
        assert services.mean_absolute_percentage_error([0, 0], [1, 2]) is None


class TestForecastModels:
    """Отдельные модели прогноза."""

    RISING = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]

    def test_naive_repeats_the_last_observation(self):
        predict = services._fit_naive(self.RISING, None)
        assert predict(10) == 20.0
        assert predict(100) == 20.0

    def test_drift_continues_the_slope(self):
        """Дрейф продолжает ряд с шагом между первым и последним значением."""
        predict = services._fit_drift(self.RISING, None)
        assert predict(len(self.RISING)) == pytest.approx(22.0)

    def test_moving_average_has_no_trend(self):
        """Скользящее среднее даёт постоянное значение на любом горизонте."""
        predict = services._fit_moving_average(self.RISING, None)
        assert predict(6) == predict(20)

    def test_linear_trend_fits_a_line(self):
        """На строго линейном ряде тренд восстанавливается точно."""
        predict = services._fit_linear(self.RISING, None)
        assert predict(6) == pytest.approx(22.0)

    def test_holt_follows_a_linear_series(self):
        """Сглаживание Хольта на линейном ряде близко к его продолжению."""
        predict = services._fit_holt(self.RISING, None)
        assert predict(6) == pytest.approx(22.0, rel=0.1)

    def test_seasonal_model_reproduces_the_profile(self):
        """Сезонная поправка воспроизводит размах повторяющегося профиля."""
        values = [10.0, 20.0] * 6
        predict = services._fit_seasonal(values, 2)
        assert predict(13) - predict(12) == pytest.approx(10.0, abs=0.5)

    def test_seasonal_model_without_a_profile_matches_the_trend(self):
        """Ряд без внутрицикловых колебаний сезонной поправки не получает."""
        values = [float(value) for value in range(10, 22)]
        seasonal = services._fit_seasonal(values, 2)
        linear = services._fit_linear(values, None)
        assert seasonal(12) == pytest.approx(linear(12))


@pytest.mark.django_db
class TestModelComparison:
    """Отбор модели сопоставлением на отложенной выборке."""

    @staticmethod
    def series(values, period_type="month"):
        """Ряд наблюдений в том виде, в каком его отдаёт выборка."""
        from datetime import date

        return [
            {
                "period": date(2020 + index // 12, index % 12 + 1, 1),
                "period_type": period_type,
                "volume": float(value),
            }
            for index, value in enumerate(values)
        ]

    def test_every_applicable_model_is_measured(self):
        """Каждая применимая модель получает все три меры ошибки."""
        result = services.compare_forecast_models(self.series(range(10, 30)))
        assert result["outcomes"]
        for outcome in result["outcomes"]:
            assert outcome["mae"] >= 0 and outcome["rmse"] >= 0

    def test_ordered_by_absolute_error(self):
        """Модели упорядочены по возрастанию абсолютной ошибки."""
        outcomes = services.compare_forecast_models(
            self.series(range(10, 30))
        )["outcomes"]
        errors = [outcome["mae"] for outcome in outcomes]
        assert errors == sorted(errors)
        assert outcomes[0]["best"] is True

    def test_naive_is_the_reference_point(self):
        """Наивная модель участвует в сопоставлении и не имеет выигрыша."""
        result = services.compare_forecast_models(self.series(range(10, 30)))
        naive = next(
            item for item in result["outcomes"] if item["model"].code == "naive"
        )
        assert naive["gain"] == 0.0

    def test_trend_beats_naive_on_a_rising_series(self):
        """На ряде с устойчивым ростом тренд точнее продолжения последним."""
        result = services.compare_forecast_models(self.series(range(10, 40)))
        linear = next(
            item for item in result["outcomes"] if item["model"].code == "linear"
        )
        assert linear["gain"] > 0

    def test_seasonal_model_rejected_on_annual_series(self):
        """Сезонная модель к годовому ряду не применяется."""
        result = services.compare_forecast_models(
            self.series(range(10, 30), period_type="year")
        )
        rejected = {item["model"].code for item in result["rejected"]}
        assert "seasonal" in rejected

    def test_seasonal_model_rejected_on_short_monthly_series(self):
        """Помесячный ряд короче двух циклов сезонности не даёт."""
        result = services.compare_forecast_models(self.series(range(10, 30)))
        reasons = {item["model"].code: item["reason"] for item in result["rejected"]}
        assert "цикл" in reasons.get("seasonal", "")

    def test_seasonal_model_accepted_on_long_monthly_series(self):
        """При двух полных циклах сезонная модель в сопоставление входит."""
        result = services.compare_forecast_models(self.series(range(10, 50)))
        codes = {item["model"].code for item in result["outcomes"]}
        assert "seasonal" in codes

    def test_holdout_is_taken_from_the_end(self):
        """Проверочная часть — хвост ряда, а не случайная выборка."""
        result = services.compare_forecast_models(self.series(range(10, 30)))
        assert result["train"] + result["holdout"] == 20


@pytest.mark.django_db
class TestForecastRefusal:
    """Явный отказ от прогноза при непригодном ряде."""

    def test_empty_series(self):
        assert "ни одного наблюдения" in services.forecast_refusal([])

    def test_short_series(self):
        rows = TestModelComparison.series(range(4))
        reason = services.forecast_refusal(rows)
        assert str(services.MIN_OBSERVATIONS) in reason

    def test_constant_series(self):
        """Ряд из одинаковых значений продолжать нечем."""
        rows = TestModelComparison.series([7] * 20)
        assert "совпадают" in services.forecast_refusal(rows)

    def test_usable_series_is_not_refused(self):
        assert services.forecast_refusal(TestModelComparison.series(range(10, 30))) == ""

    def test_refusal_names_the_shortfall(self, db):
        """Отказ сообщает, сколько наблюдений есть и сколько нужно."""
        result = services.forecast_flow(horizon=6)
        assert result["available"] is False
        assert "наблюден" in result["reason"]


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

    def test_single_condition_needs_no_breakdown(self, full_dataset, districts):
        """Разбор не строится там, где условие одно: разбирать нечего."""
        assert services.scenario(districts[0].id, storage=30)["variants"] == []

    def test_breakdown_covers_every_condition(self, full_dataset, districts):
        """Каждое заданное условие прикладывается отдельно, и все — вместе."""
        result = services.scenario(districts[0].id, storage=30, network=10)
        codes = [item["code"] for item in result["variants"]]
        assert codes == ["storage", "network", "all"]
        assert result["variants"][-1]["is_combined"] is True

    def test_combined_variant_matches_main_result(self, full_dataset, districts):
        """Совокупный вариант разбора совпадает с итогом сценария."""
        result = services.scenario(districts[0].id, storage=30, network=10)
        combined = result["variants"][-1]
        assert combined["score"] == result["subject"]["score"]
        assert combined["rank"] == result["subject"]["rank"]

    def test_breakdown_keeps_sum_of_parts(self, full_dataset, districts):
        """Сумма отдельных сдвигов приводится рядом с совокупным."""
        result = services.scenario(districts[0].id, storage=30, network=10)
        parts = result["variants"][:-1]
        assert result["variants"][-1]["sum_of_parts"] == round(
            sum(item["delta"] for item in parts), 1
        )


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
