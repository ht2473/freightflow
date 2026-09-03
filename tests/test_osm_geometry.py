"""Извлечение геометрии из элементов OpenStreetMap.

Метод: модульное тестирование, проверка граничных случаев разметки.

Разбор геометрии — то место, где ошибка не проявляется явно: объект просто
окажется не там или получит неверную площадь, и обнаружится это лишь при
взгляде на карту. Поэтому проверяются и сборка колец из разрозненных участков,
и расчёт площади против аналитически известных значений.
"""

from __future__ import annotations

import math

import pytest
from etl.osm.geometry import assemble_rings, extract
from geo.geometry import EARTH_RADIUS_KM, Geometry


def ring(*points: tuple[float, float]) -> list[list[float]]:
    """Замкнутое кольцо по перечисленным вершинам."""
    coords = [[float(x), float(y)] for x, y in points]
    return [*coords, coords[0]]


def nodes(*points: tuple[float, float]) -> list[dict]:
    """Вершины в том виде, в каком их отдаёт Overpass."""
    return [{"lon": x, "lat": y} for x, y in points]


class TestNodes:
    """Точечные элементы."""

    def test_node_gives_point_without_area(self):
        """У объекта, размеченного точкой, площади нет.

        Отсутствие площади сохраняется как отсутствие, а не подменяется
        нулём или оценкой: величина неизвестна, и это часть данных.
        """
        result = extract({"type": "node", "lon": 37.6156, "lat": 55.7522})
        assert result.point.geom_type == "POINT"
        assert result.point.coordinates == [37.6156, 55.7522]
        assert result.footprint is None
        assert result.area_sq_m is None

    def test_node_without_coordinates(self):
        """Точка без координат не вызывает ошибки."""
        result = extract({"type": "node"})
        assert not result.is_located


class TestWays:
    """Линейные элементы."""

    def test_closed_way_becomes_footprint(self):
        """Замкнутая линия описывает контур площадного объекта."""
        element = {
            "type": "way",
            "geometry": nodes((37.60, 55.75), (37.61, 55.75), (37.61, 55.76),
                              (37.60, 55.76), (37.60, 55.75)),
        }
        result = extract(element)
        assert result.footprint.geom_type == "MULTIPOLYGON"
        assert result.area_sq_m > 0
        # Точка объекта — центр контура.
        assert 37.60 < result.point.coordinates[0] < 37.61

    def test_open_way_has_no_area(self):
        """Незамкнутая линия — линейный объект: положение есть, площади нет."""
        element = {
            "type": "way",
            "geometry": nodes((37.60, 55.75), (37.65, 55.76), (37.70, 55.77)),
        }
        result = extract(element)
        assert result.is_located
        assert result.footprint is None
        assert result.area_sq_m is None

    def test_falls_back_to_center(self):
        """При выгрузке без геометрии используется вычисленный центр.

        Запрос ``out center`` отдаёт только центр; загрузчик должен принимать
        и такой ответ, теряя площадь, но не положение.
        """
        result = extract({"type": "way", "center": {"lon": 37.5, "lat": 55.7}})
        assert result.point.coordinates == [37.5, 55.7]
        assert result.footprint is None


class TestRelations:
    """Отношения: контур собирается из разрозненных участков."""

    def test_segments_are_assembled(self):
        """Участки контура стыкуются в замкнутое кольцо."""
        element = {
            "type": "relation",
            "members": [
                {"role": "outer", "geometry": nodes((37.60, 55.75), (37.61, 55.75))},
                {"role": "outer", "geometry": nodes((37.61, 55.75), (37.61, 55.76))},
                {"role": "outer", "geometry": nodes((37.61, 55.76), (37.60, 55.76))},
                {"role": "outer", "geometry": nodes((37.60, 55.76), (37.60, 55.75))},
            ],
        }
        result = extract(element)
        assert result.footprint is not None
        assert result.area_sq_m > 0

    def test_reversed_segments_are_assembled(self):
        """Участки, размеченные в обратную сторону, тоже стыкуются.

        Направление обхода в OpenStreetMap не нормировано: части контура
        приходят как есть, и сборка обязана их разворачивать.
        """
        element = {
            "type": "relation",
            "members": [
                {"role": "outer", "geometry": nodes((37.60, 55.75), (37.61, 55.75))},
                # Следующий участок размечен от конца к началу.
                {"role": "outer", "geometry": nodes((37.61, 55.76), (37.61, 55.75))},
                {"role": "outer", "geometry": nodes((37.61, 55.76), (37.60, 55.76))},
                {"role": "outer", "geometry": nodes((37.60, 55.75), (37.60, 55.76))},
            ],
        }
        assert extract(element).footprint is not None

    def test_inner_ring_is_subtracted(self):
        """Внутреннее кольцо уменьшает площадь."""
        outer = {"role": "outer", "geometry": nodes(
            (37.60, 55.75), (37.62, 55.75), (37.62, 55.77), (37.60, 55.77), (37.60, 55.75))}
        inner = {"role": "inner", "geometry": nodes(
            (37.605, 55.755), (37.610, 55.755), (37.610, 55.760), (37.605, 55.760),
            (37.605, 55.755))}

        solid = extract({"type": "relation", "members": [outer]})
        holed = extract({"type": "relation", "members": [outer, inner]})
        assert holed.area_sq_m < solid.area_sq_m

    def test_unclosed_segments_are_discarded(self):
        """Незамкнутый контур площади не даёт.

        Достраивать разорванный контур догадкой нельзя: получилась бы
        измеренная величина, выведенная из вымысла.
        """
        element = {
            "type": "relation",
            "members": [
                {"role": "outer", "geometry": nodes((37.60, 55.75), (37.61, 55.75))},
                {"role": "outer", "geometry": nodes((37.70, 55.80), (37.71, 55.80))},
            ],
        }
        assert extract(element).footprint is None

    def test_members_without_role_are_outer(self):
        """Участок без указанной роли считается внешним контуром."""
        element = {
            "type": "relation",
            "members": [
                {"geometry": nodes((37.60, 55.75), (37.61, 55.75), (37.61, 55.76),
                                   (37.60, 55.76), (37.60, 55.75))},
            ],
        }
        assert extract(element).footprint is not None


class TestRingAssembly:
    """Сборка колец как самостоятельная операция."""

    def test_two_independent_rings(self):
        """Несвязанные наборы участков дают два отдельных кольца."""
        first = [[[0.0, 0.0], [1.0, 0.0]], [[1.0, 0.0], [1.0, 1.0]],
                 [[1.0, 1.0], [0.0, 0.0]]]
        second = [[[10.0, 10.0], [11.0, 10.0]], [[11.0, 10.0], [11.0, 11.0]],
                  [[11.0, 11.0], [10.0, 10.0]]]
        assert len(assemble_rings(first + second)) == 2

    def test_empty_input(self):
        assert assemble_rings([]) == []

    def test_single_point_segments_ignored(self):
        """Участок из одной вершины кольца не образует."""
        assert assemble_rings([[[0.0, 0.0]]]) == []


class TestArea:
    """Расчёт площади по формуле сферического избытка."""

    def test_matches_analytic_value(self):
        """Площадь малого прямоугольника совпадает с аналитической.

        Для прямоугольника со сторонами Δλ и Δφ на широте φ площадь равна
        R²·Δλ·Δφ·cos φ. На размерах городского объекта расхождение формулы
        с этим выражением пренебрежимо мало.
        """
        lat, delta = 55.75, 0.001
        polygon = Geometry("POLYGON", [ring(
            (37.60, lat), (37.60 + delta, lat),
            (37.60 + delta, lat + delta), (37.60, lat + delta))])

        radius_m = EARTH_RADIUS_KM * 1000.0
        expected = (
            math.radians(delta) * radius_m
            * math.radians(delta) * radius_m * math.cos(math.radians(lat + delta / 2))
        )
        assert polygon.area_sq_m == pytest.approx(expected, rel=1e-4)

    def test_degree_square_at_equator(self):
        """Градусный квадрат на экваторе — около 12 364 км²."""
        polygon = Geometry("POLYGON", [ring((0, 0), (1, 0), (1, 1), (0, 1))])
        assert polygon.area_sq_m / 1e6 == pytest.approx(12364.3, rel=1e-3)

    def test_orientation_does_not_matter(self):
        """Направление обхода кольца на площадь не влияет."""
        clockwise = Geometry("POLYGON", [ring((0, 0), (0, 1), (1, 1), (1, 0))])
        counter = Geometry("POLYGON", [ring((0, 0), (1, 0), (1, 1), (0, 1))])
        assert clockwise.area_sq_m == pytest.approx(counter.area_sq_m)

    def test_multipolygon_sums_parts(self):
        """Площадь мультиполигона — сумма площадей частей."""
        part = ring((0, 0), (1, 0), (1, 1), (0, 1))
        single = Geometry("POLYGON", [part])
        double = Geometry("MULTIPOLYGON", [[part], [part]])
        assert double.area_sq_m == pytest.approx(2 * single.area_sq_m)

    @pytest.mark.parametrize(
        "geometry",
        [
            Geometry.point(37.6, 55.75),
            Geometry.line([[37.6, 55.75], [37.7, 55.80]]),
        ],
    )
    def test_non_areal_geometry_has_zero_area(self, geometry):
        """У точки и ломаной площади нет."""
        assert geometry.area_sq_m == 0.0

    def test_degenerate_ring(self):
        """Кольцо из двух вершин площади не образует."""
        assert Geometry("POLYGON", [[[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]]).area_sq_m == 0.0
