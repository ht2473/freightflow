"""Проверки пространственного анализа: сетка, зоны обслуживания, картограмма.

Метод: модульное и интеграционное тестирование. Расчёт ведётся на округе
с известными границами, поэтому ожидаемые величины выводятся из условия,
а не из предыдущего прогона.
"""

from __future__ import annotations

import math

import pytest
from analytics import spatial
from core.models import District, InfrastructureObject
from geo import Geometry

pytestmark = pytest.mark.django_db


@pytest.fixture
def square_district(db):
    """Округ в виде квадрата со стороной около одиннадцати километров.

    Прямоугольник в градусах даёт по широте 0,1° ≈ 11,1 км, по долготе
    столько же с поправкой на косинус широты. Ровные границы позволяют
    считать ожидаемые величины вручную.
    """
    return District.objects.create(
        name="Опытный", short_name="ОПЫ",
        area_sq_km=125, population=100_000,
        geom=Geometry("MULTIPOLYGON", [[[
            [37.50, 55.70], [37.68, 55.70], [37.68, 55.80], [37.50, 55.80],
            [37.50, 55.70],
        ]]]),
    )


@pytest.fixture
def single_object(square_district, infrastructure_types, data_source):
    """Единственный объект реестра — в углу округа."""
    return InfrastructureObject.objects.create(
        name="Склад", type=infrastructure_types[0], district=square_district,
        geom=Geometry.point(37.51, 55.71), source=data_source,
    )


class TestGrid:
    """Построение сетки по территории."""

    def test_cells_cover_the_district(self, single_object):
        """Сетка покрывает округ и не выходит за его границы."""
        cells = spatial.build_grid()
        assert cells
        for cell in cells:
            assert 37.50 <= cell.lon <= 37.68
            assert 55.70 <= cell.lat <= 55.80

    def test_cells_belong_to_a_district(self, single_object, square_district):
        """Каждая ячейка отнесена к округу, в котором лежит её центр."""
        assert {cell.district_id for cell in spatial.build_grid()} == {square_district.id}

    def test_grid_area_matches_the_district(self, single_object, square_district):
        """Площадь сетки близка к площади округа.

        Расхождение неизбежно: ячейка входит в сетку целиком или не входит
        вовсе. При шаге в два километра на округе в сотню квадратных
        километров это единицы процентов.
        """
        area = len(spatial.build_grid()) * spatial.GRID_STEP_KM**2
        assert area == pytest.approx(float(square_district.area_sq_km), rel=0.2)

    def test_empty_registry_leaves_distances_undefined(self, square_district):
        """Без объектов расстояние до ближайшего не существует."""
        assert all(cell.distance_km == math.inf for cell in spatial.build_grid())

    def test_no_districts_no_grid(self, db):
        """Без границ округов строить сетку не по чему."""
        assert spatial.build_grid() == []


class TestCoverage:
    """Обеспеченность территории объектами."""

    def test_share_within_radius(self, single_object):
        """Доля покрытой территории лежит в пределах шкалы."""
        result = spatial.coverage(radius_km=5.0)
        assert result["available"] is True
        assert 0 <= result["share"] <= 100

    def test_larger_radius_covers_more(self, single_object):
        """Больший радиус не может покрыть меньшую долю территории."""
        near = spatial.coverage(radius_km=2.0)["share"]
        far = spatial.coverage(radius_km=10.0)["share"]
        assert far >= near

    def test_gaps_are_beyond_the_radius(self, single_object):
        """В перечень необеспеченных попадают точки за пределами радиуса."""
        result = spatial.coverage(radius_km=3.0)
        assert all(gap["distance_km"] > 3.0 for gap in result["gaps"])

    def test_gaps_ordered_by_distance(self, single_object):
        """Перечень начинается с наиболее удалённой точки."""
        distances = [gap["distance_km"] for gap in spatial.coverage(2.0)["gaps"]]
        assert distances == sorted(distances, reverse=True)

    def test_district_breakdown(self, single_object, square_district):
        """Разрез по округам содержит каждый округ сетки."""
        rows = spatial.coverage(radius_km=5.0)["districts"]
        assert [row["district"] for row in rows] == [square_district]
        assert rows[0]["mean_distance"] is not None

    def test_covered_and_gaps_sum_to_the_grid(self, single_object):
        """Покрытая и непокрытая части в сумме дают всю сетку."""
        result = spatial.coverage(radius_km=3.0)
        assert result["covered"] + result["gap_count"] == result["cells"]

    def test_unavailable_without_districts(self, db):
        """Без границ округов расчёт помечается недоступным."""
        assert spatial.coverage()["available"] is False


class TestAccessibilityLayer:
    """Слой доступности для карты."""

    def test_layer_is_a_feature_collection(self, single_object):
        layer = spatial.accessibility_layer(radius_km=3.0)
        assert layer["type"] == "FeatureCollection"
        assert layer["count"] == len(layer["features"])

    def test_features_carry_distance_and_verdict(self, single_object):
        feature = spatial.accessibility_layer(radius_km=3.0)["features"][0]
        assert feature["geometry"]["type"] == "Point"
        assert "distance_km" in feature["properties"]
        assert isinstance(feature["properties"]["covered"], bool)


class TestChoropleth:
    """Картограмма округов."""

    def test_shapes_built_for_every_district(self, square_district):
        chart = spatial.choropleth({square_district.id: 50.0})
        assert chart["available"] is True
        assert len(chart["shapes"]) == 1
        assert chart["shapes"][0]["rings"]

    def test_unmeasured_district_is_left_unfilled(self, square_district):
        """Округ без измеренного значения не закрашивается."""
        chart = spatial.choropleth({square_district.id: None})
        assert chart["shapes"][0]["fill"] == "none"

    def test_scale_runs_from_low_to_high(self, districts):
        """Наименьшее значение получает начало шкалы, наибольшее — конец."""
        for district in districts:
            district.geom = Geometry("MULTIPOLYGON", [[[
                [37.5, 55.7], [37.6, 55.7], [37.6, 55.8], [37.5, 55.8], [37.5, 55.7],
            ]]])
            district.save()
        values = {district.id: float(index) for index, district in enumerate(districts)}

        chart = spatial.choropleth(values)
        shades = {shape["district"].id: shape["fill"] for shape in chart["shapes"]}
        assert shades[districts[0].id] == spatial.CHOROPLETH_SCALE[0]
        assert shades[districts[-1].id] == spatial.CHOROPLETH_SCALE[-1]

    def test_drawing_is_bounded(self, square_district):
        """Рисунок вписан в заданный размер по большей стороне."""
        chart = spatial.choropleth({square_district.id: 1.0})
        assert max(chart["width"], chart["height"]) == pytest.approx(1000.0, abs=1)

    def test_unavailable_without_boundaries(self, db):
        assert spatial.choropleth({})["available"] is False


class TestMetricValues:
    """Показатели, доступные картограмме."""

    def test_every_metric_resolves(self, full_dataset):
        """Каждый объявленный показатель возвращает значения и единицу."""
        for code in spatial.CHOROPLETH_METRICS:
            values, unit = spatial.metric_values(code)
            assert values and unit

    def test_unknown_metric_falls_back_to_the_index(self, full_dataset):
        """Неизвестное обозначение приводит к индексу, а не к отказу."""
        unknown, _ = spatial.metric_values("несуществующий")
        index, _ = spatial.metric_values("score")
        assert unknown == index
