"""Проверки подложки карты: водные поверхности и зелёные массивы.

Разбор ведётся на подготовленных элементах выгрузки, без обращения к службе:
проверяется отнесение к виду территории, порог различимости и то, что
площадь измеряется по контуру, а не выводится из чего-либо ещё.
"""

from __future__ import annotations

import math

import pytest
from core.choices import NaturalKind
from core.models import NaturalArea
from etl.osm.natural import (
    MIN_GREEN_AREA_SQ_M,
    MIN_WATER_AREA_SQ_M,
    NaturalAreasPipeline,
    classify_kind,
)
from etl.pipeline import Context, Extract, run

#: Сторона квадрата в градусах широты, дающая заданную площадь.
#: На широте Москвы градус долготы короче градуса широты примерно вдвое,
#: поэтому множитель учитывает косинус широты.
_LAT_METERS = 111_320.0
_LAT = 55.75


def square(identifier: int, area_sq_m: float, **tags) -> dict:
    """Элемент выгрузки: замкнутый контур заданной площади."""
    side_lat = math.sqrt(area_sq_m) / _LAT_METERS
    side_lon = side_lat / math.cos(math.radians(_LAT))
    lon, lat = 37.60, _LAT
    ring = [
        {"lon": lon, "lat": lat},
        {"lon": lon + side_lon, "lat": lat},
        {"lon": lon + side_lon, "lat": lat + side_lat},
        {"lon": lon, "lat": lat + side_lat},
        {"lon": lon, "lat": lat},
    ]
    return {"type": "way", "id": identifier, "geometry": ring, "tags": tags}


class StubNatural(NaturalAreasPipeline):
    """Конвейер, получающий выгрузку из подготовленного списка."""

    def __init__(self, elements):
        self.elements = elements

    def extract(self, context: Context) -> Extract:
        return Extract(records=self.elements, count=len(self.elements), fetched_at=None)


class TestKind:
    """Отнесение элемента разметки к виду природной территории."""

    @pytest.mark.parametrize(
        "tags",
        [
            {"natural": "water"},
            {"water": "river"},
            {"waterway": "riverbank"},
        ],
    )
    def test_water_tags(self, tags):
        assert classify_kind(tags) == NaturalKind.WATER

    @pytest.mark.parametrize("tags", [{"leisure": "park"}, {"landuse": "forest"}])
    def test_green_tags(self, tags):
        assert classify_kind(tags) == NaturalKind.GREEN

    def test_water_wins_over_park(self):
        """Пойменный парк остаётся водой: препятствием его делает река."""
        assert classify_kind({"leisure": "park", "natural": "water"}) == NaturalKind.WATER

    def test_unrelated_markup_is_not_natural(self):
        assert classify_kind({"building": "warehouse"}) is None


class TestSelection:
    """Порог различимости и требование контура."""

    def test_large_water_is_accepted(self, db):
        report = run(StubNatural([square(1, MIN_WATER_AREA_SQ_M * 4, natural="water")]))
        assert report.created == 1
        assert NaturalArea.objects.get().kind == NaturalKind.WATER

    def test_small_water_is_filtered_out(self, db):
        report = run(StubNatural([square(1, MIN_WATER_AREA_SQ_M / 10, natural="water")]))
        assert report.filtered == 1
        assert NaturalArea.objects.count() == 0

    def test_green_threshold_is_higher_than_water(self, db):
        """Сквер мельче двух гектаров на карте города не читается."""
        assert MIN_GREEN_AREA_SQ_M > MIN_WATER_AREA_SQ_M
        report = run(
            StubNatural(
                [
                    square(1, MIN_GREEN_AREA_SQ_M / 4, leisure="park", name="Сквер"),
                    square(2, MIN_GREEN_AREA_SQ_M * 3, leisure="park", name="Парк"),
                ]
            )
        )
        assert report.created == 1
        assert NaturalArea.objects.get().name == "Парк"

    def test_line_without_contour_is_filtered_out(self, db):
        """Русло, размеченное незамкнутой линией, площади не имеет."""
        element = square(1, MIN_WATER_AREA_SQ_M * 4, waterway="riverbank")
        element["geometry"] = element["geometry"][:3]
        assert run(StubNatural([element])).filtered == 1

    def test_unrelated_markup_is_filtered_out(self, db):
        assert run(StubNatural([square(1, 100_000, building="warehouse")])).filtered == 1


class TestRecord:
    """Состав записи реестра."""

    def test_area_is_measured_by_contour(self, db):
        run(StubNatural([square(1, 40_000, natural="water", name="Пруд")]))
        area = NaturalArea.objects.get()
        assert float(area.area_sq_m) == pytest.approx(40_000, rel=0.02)
        assert area.geom.geom_type == "MULTIPOLYGON"

    def test_source_element_is_traceable(self, db):
        run(StubNatural([square(7, 40_000, natural="water")]))
        area = NaturalArea.objects.get()
        assert (area.osm_type, area.osm_id) == ("way", 7)
        assert area.source is not None

    def test_repeated_load_updates_the_record(self, db):
        elements = [square(1, 40_000, natural="water", name="Пруд")]
        run(StubNatural(elements))
        report = run(StubNatural([square(1, 40_000, natural="water", name="Пруд Новый")]))
        assert (report.created, report.updated) == (0, 1)
        assert NaturalArea.objects.count() == 1

    def test_missing_area_is_removed_by_prune(self, db):
        run(StubNatural([square(1, 40_000, natural="water"), square(2, 40_000, leisure="park")]))
        report = run(StubNatural([square(1, 40_000, natural="water")]), Context(prune=True))
        assert report.removed == 1
        assert NaturalArea.objects.count() == 1
