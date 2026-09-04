"""Проверки событийного слоя: дорожные работы из разметки OpenStreetMap.

Разбор ведётся на подготовленных элементах выгрузки, без обращения к службе:
проверяется отбор участков, отнесение к грузовому движению, шкала серьёзности
и то, что время регистрации берётся из источника, а не из момента загрузки.
"""

from __future__ import annotations

import pytest
from core.choices import DataOrigin, IncidentType
from core.models import RoadSegment, TrafficIncident
from etl.osm.incidents import RoadworksPipeline
from etl.pipeline import Context, Extract, RunReport, run


def way(identifier: int, construction: str = "secondary", *, name: str = "",
        timestamp: str = "2026-05-18T14:14:03Z", lon: float = 37.62,
        lat: float = 55.75, **tags) -> dict:
    """Элемент выгрузки: участок, закрытый на реконструкцию."""
    element = {
        "type": "way",
        "id": identifier,
        "timestamp": timestamp,
        "center": {"lon": lon, "lat": lat},
        "tags": {"highway": "construction", "construction": construction, **tags},
    }
    if name:
        element["tags"]["name"] = name
    return element


class StubRoadworks(RoadworksPipeline):
    """Конвейер, получающий выгрузку из подготовленного списка."""

    def __init__(self, elements):
        self.elements = elements

    def extract(self, context: Context) -> Extract:
        return Extract(records=self.elements, count=len(self.elements),
                       fetched_at=None)


@pytest.fixture
def magistral(db):
    return RoadSegment.objects.create(
        name="Каширское шоссе", road_class="arterial", in_freight_frame=True,
    )


@pytest.fixture
def bounded_district(db):
    """Округ с границами: квадрат вокруг координаты по умолчанию."""
    from core.models import District
    from geo import Geometry

    return District.objects.create(
        name="Центральный", short_name="ЦАО",
        geom=Geometry("MULTIPOLYGON", [[[
            [37.60, 55.73], [37.65, 55.73], [37.65, 55.77], [37.60, 55.77], [37.60, 55.73],
        ]]]),
    )


class TestSelection:
    """В реестр попадают только участки автомобильной сети."""

    def test_footway_is_filtered_out(self, db):
        report = run(StubRoadworks([way(1, "footway")]))
        assert report.filtered == 1
        assert TrafficIncident.objects.count() == 0

    def test_road_is_accepted(self, db):
        report = run(StubRoadworks([way(1, "secondary")]))
        assert report.created == 1

    def test_unmarked_class_is_filtered_out(self, db):
        element = way(1)
        del element["tags"]["construction"]
        assert run(StubRoadworks([element])).filtered == 1


class TestClassification:
    """Признаки события выводятся из разметки по объявленным правилам."""

    def test_type_is_roadworks(self, db):
        run(StubRoadworks([way(1)]))
        assert TrafficIncident.objects.get().incident_type == IncidentType.ROADWORKS

    def test_severity_follows_road_class(self, db):
        run(StubRoadworks([way(1, "motorway"), way(2, "residential")]))
        severity = dict(
            TrafficIncident.objects.values_list("external_key", "severity")
        )
        assert severity["way/1"] == 5
        assert severity["way/2"] == 1

    def test_freight_classes_are_marked(self, db):
        run(StubRoadworks([way(1, "trunk"), way(2, "residential")]))
        affected = dict(
            TrafficIncident.objects.values_list("external_key", "affects_cargo")
        )
        assert affected["way/1"] is True
        assert affected["way/2"] is False

    def test_freight_frame_road_is_marked(self, db, magistral):
        """Работы на магистрали каркаса затрагивают грузовое движение."""
        run(StubRoadworks([way(1, "residential", name="Каширское шоссе")]))
        event = TrafficIncident.objects.get()
        assert event.road == magistral
        assert event.affects_cargo is True

    def test_origin_is_measured(self, db):
        run(StubRoadworks([way(1)]))
        assert TrafficIncident.objects.get().origin == DataOrigin.MEASURED


class TestTerritory:
    """Событие относится к округу по своей координате."""

    def test_point_inside_boundary_locates_district(self, bounded_district):
        run(StubRoadworks([way(1, lon=37.62, lat=55.75)]))
        assert TrafficIncident.objects.get().district == bounded_district

    def test_point_outside_boundary_stays_unlocated(self, bounded_district):
        run(StubRoadworks([way(1, lon=37.90, lat=55.90)]))
        assert TrafficIncident.objects.get().district is None

    def test_district_does_not_depend_on_road_registry(self, bounded_district):
        """Работы на улице вне реестра магистралей всё равно попадают в округ."""
        run(StubRoadworks([way(1, name="Улица без реестра", lon=37.62, lat=55.75)]))
        event = TrafficIncident.objects.get()
        assert event.road is None
        assert event.district == bounded_district


class TestTime:
    """Время события берётся из источника."""

    def test_reported_at_comes_from_element(self, db):
        run(StubRoadworks([way(1, timestamp="2024-03-11T08:00:00Z")]))
        assert TrafficIncident.objects.get().reported_at.year == 2024

    def test_element_without_time_is_rejected(self, db):
        element = way(1)
        del element["timestamp"]
        report = run(StubRoadworks([element]))

        assert report.rejected == 1
        assert TrafficIncident.objects.count() == 0

    def test_event_stays_open(self, db):
        """Признака устранения источник не публикует, и выдумывать его нельзя."""
        run(StubRoadworks([way(1)]))
        assert TrafficIncident.objects.get().resolved_at is None


class TestDescription:
    """Пояснение собирается только из размеченных сведений."""

    def test_opening_date_is_quoted(self, db):
        run(StubRoadworks([way(1, opening_date="2026-12-01")]))
        assert "2026-12-01" in TrafficIncident.objects.get().description

    def test_class_is_named(self, db):
        run(StubRoadworks([way(1, "primary")]))
        assert "primary" in TrafficIncident.objects.get().description


class TestIncremental:
    """Повторная загрузка обновляет событие, а не добавляет второе."""

    def test_repeat_run_does_not_duplicate(self, db):
        elements = [way(1), way(2, "tertiary")]
        run(StubRoadworks(elements))
        report = run(StubRoadworks(elements))

        assert TrafficIncident.objects.count() == 2
        assert report.unchanged == 2

    def test_changed_class_updates_severity(self, db):
        run(StubRoadworks([way(1, "tertiary")]))
        run(StubRoadworks([way(1, "motorway")]))

        assert TrafficIncident.objects.get().severity == 5

    def test_prune_removes_finished_works(self, db):
        run(StubRoadworks([way(1), way(2)]))
        report = run(StubRoadworks([way(1)]), Context(prune=True))

        assert report.removed == 1
        assert list(TrafficIncident.objects.values_list("external_key", flat=True)) == [
            "way/1"
        ]

    def test_prune_spares_records_of_other_sources(self, db, data_source):
        from django.utils import timezone

        TrafficIncident.objects.create(
            reported_at=timezone.now(), incident_type=IncidentType.ACCIDENT,
            source=data_source,
        )
        run(StubRoadworks([way(1)]), Context(prune=True))
        assert TrafficIncident.objects.filter(source=data_source).count() == 1


class TestSummary:
    """Свойства источника сообщаются, а не сглаживаются."""

    def test_stale_marking_is_reported(self, db):
        report = run(StubRoadworks([way(1, timestamp="2015-01-01T00:00:00Z")]))
        assert any("старше трёх лет" in line for line in report.details)

    def test_cargo_share_is_reported(self, db):
        report = run(StubRoadworks([way(1, "trunk"), way(2, "residential")]))
        assert any("грузовое движение" in line for line in report.details)


class TestChecks:
    """Проверки применяются к каждому кандидату."""

    def test_element_without_coordinates_is_rejected(self, db):
        element = way(1)
        del element["center"]
        report = run(StubRoadworks([element]))

        assert report.rejected == 1
        assert report.by_check["required.geom"] == 1

    def test_report_is_a_run_report(self, db):
        assert isinstance(run(StubRoadworks([])), RunReport)


class TestAnnouncement:
    """Оповещение подписчиков по итогу загрузки."""

    @pytest.fixture
    def subscription(self, users):
        """Подписка на серьёзные события грузового движения по всему городу."""
        from accounts.models import IncidentSubscription, Role

        return IncidentSubscription.objects.create(
            user=users[Role.VIEWER], min_severity=3, cargo_only=True
        )

    def test_new_events_reach_subscriber(self, subscription):
        """Загрузка, принёсшая подходящее событие, оповещает подписчика."""
        from accounts.models import Notification

        report = run(StubRoadworks([way(1, "trunk")]))
        assert Notification.objects.filter(user=subscription.user).count() == 1
        assert any("оповещено подписчиков" in line for line in report.details)

    def test_repeat_load_does_not_repeat_notice(self, subscription):
        """Повторное подтверждение источником события не порождает."""
        from accounts.models import Notification

        run(StubRoadworks([way(1, "trunk")]))
        run(StubRoadworks([way(1, "trunk")]))
        assert Notification.objects.filter(user=subscription.user).count() == 1

    def test_dry_run_announces_nothing(self, subscription):
        """Пробный проход не оповещает: он ничего не изменил."""
        from accounts.models import Notification

        run(StubRoadworks([way(1, "trunk")]), Context(dry_run=True))
        assert not Notification.objects.exists()

    def test_quarantine_reaches_operators(self, users):
        """Отложенные проверками записи доходят до тех, кто ведёт карантин."""
        from accounts.models import Notification, Role

        element = way(1)
        del element["center"]
        run(StubRoadworks([element]))

        assert Notification.objects.filter(user=users[Role.OPERATOR]).exists()
