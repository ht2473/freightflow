"""Проверки тайловой сетки и двоичной упаковки тайла.

Собранный тайл разбирается независимым читателем (``tests/mvt_reader.py``),
поэтому проверяется именно содержимое ответа, а не согласованность кода
с самим собой.
"""

from __future__ import annotations

import math

import pytest
from geo import Geometry
from geo.mvt import TILE_SPEC_VERSION, TileFeature, render_tile
from geo.tiles import (
    KIND_LINE,
    KIND_POINT,
    KIND_POLYGON,
    TILE_EXTENT,
    TileError,
    clip_line,
    clip_ring,
    project,
    simplify_points,
    tile_bounds,
    tile_of,
    to_tile_geometry,
)
from mvt_reader import read_tile

# Центр Москвы: точка, по которой удобно проверять попадание в сетку.
MOSCOW = (37.6156, 55.7522)


# ---------------------------------------------------------------------------
#  Сетка
# ---------------------------------------------------------------------------


class TestGrid:
    """Переход между градусами и номерами тайлов."""

    def test_tile_of_matches_bounds(self):
        z = 10
        x, y = tile_of(*MOSCOW, z)
        min_lon, min_lat, max_lon, max_lat = tile_bounds(z, x, y)
        assert min_lon <= MOSCOW[0] <= max_lon
        assert min_lat <= MOSCOW[1] <= max_lat

    def test_zero_zoom_covers_world(self):
        min_lon, min_lat, max_lon, max_lat = tile_bounds(0, 0, 0)
        assert (min_lon, max_lon) == (-180.0, 180.0)
        assert math.isclose(max_lat, 85.0511, abs_tol=1e-3)
        assert math.isclose(min_lat, -85.0511, abs_tol=1e-3)

    def test_neighbours_touch(self):
        """Соседние тайлы стыкуются без зазора и без перекрытия."""
        z, x, y = 10, 619, 321
        _, _, max_lon, _ = tile_bounds(z, x, y)
        min_lon, _, _, _ = tile_bounds(z, x + 1, y)
        assert math.isclose(max_lon, min_lon, abs_tol=1e-12)

    def test_buffer_expands_bounds(self):
        plain = tile_bounds(12, 2476, 1284)
        buffered = tile_bounds(12, 2476, 1284, buffer_units=64)
        assert buffered[0] < plain[0] and buffered[2] > plain[2]
        assert buffered[1] < plain[1] and buffered[3] > plain[3]

    def test_project_puts_centre_in_the_middle(self):
        z = 12
        x, y = tile_of(*MOSCOW, z)
        min_lon, min_lat, max_lon, max_lat = tile_bounds(z, x, y)
        centre_lon = (min_lon + max_lon) / 2
        centre_lat = (min_lat + max_lat) / 2
        px, py = project(centre_lon, centre_lat, z, x, y)
        assert abs(px - TILE_EXTENT / 2) < 2
        assert abs(py - TILE_EXTENT / 2) < 2

    @pytest.mark.parametrize("z,x,y", [(-1, 0, 0), (30, 0, 0), (10, 1024, 0), (10, 0, -1)])
    def test_tile_outside_grid_is_rejected(self, z, x, y):
        with pytest.raises(TileError):
            tile_bounds(z, x, y)


# ---------------------------------------------------------------------------
#  Прореживание и обрезка
# ---------------------------------------------------------------------------


class TestSimplify:
    """Прореживание вершин по методу Дугласа — Пекера."""

    def test_straight_line_keeps_only_ends(self):
        points = [(float(i), 0.0) for i in range(20)]
        assert simplify_points(points, 4.0) == [(0.0, 0.0), (19.0, 0.0)]

    def test_deviation_above_tolerance_survives(self):
        points = [(0.0, 0.0), (10.0, 40.0), (20.0, 0.0)]
        assert simplify_points(points, 4.0) == points

    def test_deviation_below_tolerance_disappears(self):
        points = [(0.0, 0.0), (10.0, 1.0), (20.0, 0.0)]
        assert simplify_points(points, 4.0) == [(0.0, 0.0), (20.0, 0.0)]

    def test_two_points_are_returned_as_is(self):
        points = [(0.0, 0.0), (5.0, 5.0)]
        assert simplify_points(points, 100.0) == points


class TestClip:
    """Обрезка по границам тайла."""

    def test_line_crossing_the_edge_is_cut(self):
        parts = clip_line([(-100.0, 50.0), (100.0, 50.0)], 0.0, 200.0)
        assert parts == [[(0.0, 50.0), (100.0, 50.0)]]

    def test_line_outside_disappears(self):
        assert clip_line([(-50.0, -50.0), (-10.0, -10.0)], 0.0, 200.0) == []

    def test_line_leaving_and_returning_splits(self):
        parts = clip_line(
            [(10.0, 10.0), (10.0, -50.0), (60.0, -50.0), (60.0, 10.0)], 0.0, 200.0
        )
        assert len(parts) == 2
        assert parts[0][0] == (10.0, 10.0)
        assert parts[1][-1] == (60.0, 10.0)

    def test_ring_larger_than_tile_becomes_the_tile(self):
        ring = [(-10.0, -10.0), (210.0, -10.0), (210.0, 210.0), (-10.0, 210.0), (-10.0, -10.0)]
        clipped = clip_ring(ring, 0.0, 200.0)
        assert set(clipped) == {(0.0, 0.0), (200.0, 0.0), (200.0, 200.0), (0.0, 200.0)}
        assert clipped[0] == clipped[-1]

    def test_ring_outside_disappears(self):
        ring = [(-90.0, -90.0), (-50.0, -90.0), (-50.0, -50.0), (-90.0, -90.0)]
        assert clip_ring(ring, 0.0, 200.0) == []


# ---------------------------------------------------------------------------
#  Приведение геометрии к сетке
# ---------------------------------------------------------------------------


class TestTileGeometry:
    """Разбор геометрии по тайлам."""

    def setup_method(self):
        self.z = 12
        self.x, self.y = tile_of(*MOSCOW, self.z)

    def test_point_inside_is_kept(self):
        geometry = to_tile_geometry(Geometry.point(*MOSCOW), self.z, self.x, self.y)
        assert geometry is not None
        assert geometry.kind == KIND_POINT
        assert len(geometry.parts[0]) == 1

    def test_point_outside_is_dropped(self):
        far = Geometry.point(30.0, 50.0)
        assert to_tile_geometry(far, self.z, self.x, self.y) is None

    def test_line_is_cut_by_tile(self):
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        line = Geometry.line([(min_lon - 1, min_lat), (max_lon + 1, max_lat)])
        geometry = to_tile_geometry(line, self.z, self.x, self.y)
        assert geometry is not None and geometry.kind == KIND_LINE
        for part in geometry.parts:
            for px, py in part:
                assert -64 <= px <= TILE_EXTENT + 64
                assert -64 <= py <= TILE_EXTENT + 64

    def test_outer_ring_is_wound_clockwise(self):
        """Внешнее кольцо получает положительную площадь, внутреннее — отрицательную."""
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        step_lon = (max_lon - min_lon) / 4
        step_lat = (max_lat - min_lat) / 4
        outer = [
            (min_lon + step_lon, min_lat + step_lat),
            (max_lon - step_lon, min_lat + step_lat),
            (max_lon - step_lon, max_lat - step_lat),
            (min_lon + step_lon, max_lat - step_lat),
            (min_lon + step_lon, min_lat + step_lat),
        ]
        hole = [
            (min_lon + 1.8 * step_lon, min_lat + 1.8 * step_lat),
            (max_lon - 1.8 * step_lon, min_lat + 1.8 * step_lat),
            (max_lon - 1.8 * step_lon, max_lat - 1.8 * step_lat),
            (min_lon + 1.8 * step_lon, max_lat - 1.8 * step_lat),
            (min_lon + 1.8 * step_lon, min_lat + 1.8 * step_lat),
        ]
        polygon = Geometry("POLYGON", [outer, hole])
        geometry = to_tile_geometry(polygon, self.z, self.x, self.y)
        assert geometry is not None and geometry.kind == KIND_POLYGON
        assert len(geometry.parts) == 2
        assert _signed_area(geometry.parts[0]) > 0
        assert _signed_area(geometry.parts[1]) < 0

    def test_polygon_covering_the_tile_survives(self):
        """Округ, целиком накрывающий тайл, отдаётся квадратом тайла."""
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        wide = Geometry(
            "POLYGON",
            [
                [
                    (min_lon - 1, min_lat - 1),
                    (max_lon + 1, min_lat - 1),
                    (max_lon + 1, max_lat + 1),
                    (min_lon - 1, max_lat + 1),
                    (min_lon - 1, min_lat - 1),
                ]
            ],
        )
        geometry = to_tile_geometry(wide, self.z, self.x, self.y)
        assert geometry is not None
        assert _signed_area(geometry.parts[0]) > 0


def _signed_area(ring) -> float:
    """Площадь кольца со знаком в координатах тайла."""
    return sum(
        ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)
    ) / 2.0


# ---------------------------------------------------------------------------
#  Двоичная упаковка
# ---------------------------------------------------------------------------


class TestRenderTile:
    """Сборка тайла и его разбор независимым читателем."""

    def setup_method(self):
        self.z = 12
        self.x, self.y = tile_of(*MOSCOW, self.z)

    def render(self, layers):
        return render_tile(self.z, self.x, self.y, layers)

    def test_empty_tile_is_empty(self):
        assert self.render({"objects": []}) == b""

    def test_tile_outside_data_is_empty(self):
        far = TileFeature(Geometry.point(30.0, 50.0), {"name": "вне города"})
        assert self.render({"objects": [far]}) == b""

    def test_layer_carries_version_and_extent(self):
        data = self.render({"objects": [TileFeature(Geometry.point(*MOSCOW))]})
        layer = read_tile(data)["objects"]
        assert layer["version"] == TILE_SPEC_VERSION
        assert layer["extent"] == TILE_EXTENT

    def test_point_lands_where_projected(self):
        feature = TileFeature(Geometry.point(*MOSCOW), {}, feature_id=17)
        layer = read_tile(self.render({"objects": [feature]}))["objects"]
        assert layer["features"][0]["id"] == 17
        assert layer["features"][0]["type"] == KIND_POINT
        expected = project(*MOSCOW, self.z, self.x, self.y)
        actual = layer["features"][0]["parts"][0][0]
        assert abs(actual[0] - expected[0]) <= 1
        assert abs(actual[1] - expected[1]) <= 1

    def test_property_types_survive(self):
        properties = {
            "name": "Склад «Восток»",
            "objects": 42,
            "share": 0.5,
            "cargo": True,
            "delta": -7,
            "missing": None,
        }
        feature = TileFeature(Geometry.point(*MOSCOW), properties)
        layer = read_tile(self.render({"objects": [feature]}))["objects"]
        decoded = layer["features"][0]["properties"]
        assert decoded["name"] == "Склад «Восток»"
        assert decoded["objects"] == 42
        assert decoded["share"] == pytest.approx(0.5)
        assert decoded["cargo"] is True
        assert decoded["delta"] == -7
        # Неизмеренная величина не записывается вовсе: клиент отличит
        # отсутствие свойства от нуля.
        assert "missing" not in decoded

    def test_repeated_value_is_stored_once(self):
        """Одинаковые свойства объектов попадают в словарь слоя один раз."""
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        features = [
            TileFeature(
                Geometry.point(
                    min_lon + (max_lon - min_lon) * (index + 1) / 12,
                    min_lat + (max_lat - min_lat) / 2,
                ),
                {"type": "Склад"},
            )
            for index in range(10)
        ]
        data = self.render({"objects": features})
        layer = read_tile(data)["objects"]
        assert len(layer["features"]) == 10
        assert data.count("Склад".encode()) == 1

    def test_several_layers_in_one_tile(self):
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        line = Geometry.line([(min_lon, min_lat), (max_lon, max_lat)])
        layers = read_tile(
            self.render(
                {
                    "objects": [TileFeature(Geometry.point(*MOSCOW))],
                    "roads": [TileFeature(line, {"name": "МКАД"})],
                }
            )
        )
        assert set(layers) == {"objects", "roads"}
        assert layers["roads"]["features"][0]["type"] == KIND_LINE

    def test_polygon_ring_is_closed(self):
        min_lon, min_lat, max_lon, max_lat = tile_bounds(self.z, self.x, self.y)
        step_lon = (max_lon - min_lon) / 4
        step_lat = (max_lat - min_lat) / 4
        ring = [
            (min_lon + step_lon, min_lat + step_lat),
            (max_lon - step_lon, min_lat + step_lat),
            (max_lon - step_lon, max_lat - step_lat),
            (min_lon + step_lon, max_lat - step_lat),
            (min_lon + step_lon, min_lat + step_lat),
        ]
        feature = TileFeature(Geometry("POLYGON", [ring]), {"name": "ЦАО"})
        layer = read_tile(self.render({"districts": [feature]}))["districts"]
        part = layer["features"][0]["parts"][0]
        assert layer["features"][0]["type"] == KIND_POLYGON
        assert part[0] == part[-1]
