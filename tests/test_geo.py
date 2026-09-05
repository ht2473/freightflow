"""Модульные тесты геометрического слоя.

Метод: модульное тестирование (unit testing). Проверяются чистые функции
разбора и преобразования геометрии без обращения к базе данных.
"""

from __future__ import annotations

import pytest
from geo.geometry import Geometry, GeometryError, haversine_km


class TestWktParsing:
    """Разбор представления WKT."""

    def test_point_parsing(self):
        """Точка разбирается с сохранением координат."""
        geometry = Geometry.from_wkt("POINT(37.620800 55.753900)")
        assert geometry.geom_type == "POINT"
        assert geometry.lon == pytest.approx(37.6208)
        assert geometry.lat == pytest.approx(55.7539)

    def test_linestring_parsing(self):
        """Ломаная сохраняет число и порядок вершин."""
        geometry = Geometry.from_wkt(
            "LINESTRING(37.60 55.75, 37.62 55.78, 37.65 55.80)"
        )
        assert geometry.geom_type == "LINESTRING"
        assert len(geometry.points) == 3
        assert geometry.points[0] == (37.60, 55.75)
        assert geometry.points[-1] == (37.65, 55.80)

    def test_ewkt_with_srid(self):
        """Расширенный WKT с указанием системы координат распознаётся."""
        geometry = Geometry.from_wkt("SRID=4326;POINT(37.62 55.75)")
        assert geometry.srid == 4326
        assert geometry.lon == pytest.approx(37.62)

    def test_polygon_parsing(self):
        """Полигон разбирается вместе с внешним контуром."""
        geometry = Geometry.from_wkt(
            "POLYGON((37.6 55.7, 37.7 55.7, 37.7 55.8, 37.6 55.8, 37.6 55.7))"
        )
        assert geometry.geom_type == "POLYGON"
        assert len(geometry.points) == 5

    @pytest.mark.parametrize(
        "value",
        ["не геометрия", "POINT", "CIRCLE(1 2)", "", "POINT(только_один)"],
    )
    def test_invalid_input_raises(self, value):
        """Некорректный ввод приводит к явной ошибке, а не к тихому сбою."""
        with pytest.raises(GeometryError):
            Geometry.from_wkt(value)

    def test_non_string_raises(self):
        """Передача не строки отклоняется."""
        with pytest.raises(GeometryError):
            Geometry.from_wkt(12345)


class TestWktRendering:
    """Формирование представления WKT."""

    def test_roundtrip_point(self):
        """Разбор и обратное формирование не искажают координаты."""
        source = "POINT(37.6208 55.7539)"
        assert Geometry.from_wkt(source).wkt == source

    def test_roundtrip_linestring(self):
        """Ломаная восстанавливается без потери вершин."""
        source = "LINESTRING(37.6 55.7, 37.62 55.78, 37.65 55.8)"
        restored = Geometry.from_wkt(Geometry.from_wkt(source).wkt)
        assert len(restored.points) == 3

    def test_trailing_zeros_removed(self):
        """Незначащие нули в координатах не выводятся."""
        assert Geometry.point(37.500000, 55.700000).wkt == "POINT(37.5 55.7)"

    def test_ewkt_includes_srid(self):
        """Расширенный формат содержит код системы координат."""
        assert Geometry.point(37.6, 55.7).ewkt.startswith("SRID=4326;")


class TestGeoJson:
    """Преобразование в GeoJSON и обратно."""

    def test_point_to_geojson(self):
        """Точка отдаётся в структуре, принятой в GeoJSON."""
        payload = Geometry.point(37.62, 55.75).geojson
        assert payload == {"type": "Point", "coordinates": [37.62, 55.75]}

    def test_linestring_to_geojson(self):
        """Тип ломаной записывается в нотации GeoJSON."""
        geometry = Geometry.line([(37.6, 55.7), (37.7, 55.8)])
        assert geometry.geojson["type"] == "LineString"

    def test_from_geojson(self):
        """Геометрия восстанавливается из объекта GeoJSON."""
        geometry = Geometry.from_geojson(
            {"type": "Point", "coordinates": [37.62, 55.75]}
        )
        assert geometry.lon == pytest.approx(37.62)

    def test_from_feature_wrapper(self):
        """Допускается передача объекта Feature вместо самой геометрии."""
        geometry = Geometry.from_geojson(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [37.5, 55.5]},
                "properties": {},
            }
        )
        assert geometry.lat == pytest.approx(55.5)

    def test_as_feature_carries_properties(self):
        """Атрибуты переносятся в объект Feature без изменений."""
        feature = Geometry.point(37.6, 55.7).as_feature({"name": "Склад"})
        assert feature["type"] == "Feature"
        assert feature["properties"]["name"] == "Склад"


class TestMetrics:
    """Метрические характеристики геометрии."""

    def test_bounds(self):
        """Габаритный прямоугольник охватывает все вершины."""
        geometry = Geometry.line([(37.6, 55.7), (37.8, 55.9), (37.5, 55.6)])
        assert geometry.bounds == (37.5, 55.6, 37.8, 55.9)

    def test_centroid_of_point_is_itself(self):
        """Центр точки совпадает с самой точкой."""
        assert Geometry.point(37.62, 55.75).centroid == (37.62, 55.75)

    def test_centroid_of_line(self):
        """Центр ломаной — среднее арифметическое её вершин."""
        geometry = Geometry.line([(37.0, 55.0), (39.0, 57.0)])
        assert geometry.centroid == (38.0, 56.0)

    def test_length_of_point_is_zero(self):
        """Длина точки равна нулю."""
        assert Geometry.point(37.6, 55.7).length_km == 0

    def test_length_positive(self):
        """Длина ломаной положительна и соответствует порядку величины."""
        geometry = Geometry.line([(37.6, 55.7), (37.7, 55.8)])
        assert 10 < geometry.length_km < 15


class TestHaversine:
    """Расчёт расстояния по ортодромии."""

    def test_zero_distance(self):
        """Расстояние точки до самой себя равно нулю."""
        assert haversine_km((37.62, 55.75), (37.62, 55.75)) == pytest.approx(0)

    def test_known_distance(self):
        """Расстояние Москва — Санкт-Петербург соответствует известному."""
        distance = haversine_km((37.6173, 55.7558), (30.3351, 59.9343))
        assert 630 < distance < 640

    def test_symmetry(self):
        """Расстояние не зависит от порядка аргументов."""
        first = haversine_km((37.6, 55.7), (30.3, 59.9))
        second = haversine_km((30.3, 59.9), (37.6, 55.7))
        assert first == pytest.approx(second)

    def test_one_degree_latitude(self):
        """Один градус широты соответствует примерно 111 километрам."""
        distance = haversine_km((37.6, 55.0), (37.6, 56.0))
        assert 110 < distance < 112

    def test_triangle_inequality(self):
        """Соблюдается неравенство треугольника."""
        a, b, c = (37.0, 55.0), (38.0, 56.0), (39.0, 57.0)
        assert haversine_km(a, c) <= haversine_km(a, b) + haversine_km(b, c) + 1e-6


class TestGeometryField:
    """Поле модели, хранящее геометрию."""

    def test_prep_value_from_geometry(self):
        """Объект геометрии приводится к строке WKT."""
        from geo.fields import PointField

        field = PointField()
        assert field.get_prep_value(Geometry.point(37.6, 55.7)) == "POINT(37.6 55.7)"

    def test_prep_value_from_tuple(self):
        """Пара координат принимается как точка."""
        from geo.fields import PointField

        assert PointField().get_prep_value((37.6, 55.7)) == "POINT(37.6 55.7)"

    def test_prep_value_none(self):
        """Пустое значение сохраняется как NULL."""
        from geo.fields import PointField

        assert PointField().get_prep_value(None) is None

    def test_db_type_depends_on_vendor(self):
        """Тип колонки различается для PostgreSQL и SQLite."""
        from types import SimpleNamespace

        from geo.fields import PointField

        field = PointField()
        assert field.db_type(SimpleNamespace(vendor="postgresql")) == "geometry(Point,4326)"
        assert field.db_type(SimpleNamespace(vendor="sqlite")) == "text"

    def test_placeholder_wraps_for_postgis(self):
        """При записи в PostGIS значение оборачивается конструктором."""
        from types import SimpleNamespace

        from geo.fields import PointField

        placeholder = PointField().get_placeholder(
            None, None, SimpleNamespace(vendor="postgresql")
        )
        assert "ST_GeomFromText" in placeholder

    def test_corrupted_value_returns_none(self):
        """Повреждённая геометрия не приводит к отказу страницы."""
        from types import SimpleNamespace

        from geo.fields import PointField

        result = PointField().from_db_value(
            "МУСОР", None, SimpleNamespace(vendor="sqlite")
        )
        assert result is None
