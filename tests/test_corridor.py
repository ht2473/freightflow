"""Тесты разбора грузового коридора.

Метод: функциональное тестирование расчёта — проверяется, что полоса разбора
действительно отбирает по расстоянию, что протяжённость раскладывается по
округам и что невозможность разбора называется причиной, а не пустотой.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from analytics import corridors
from core.choices import DataOrigin, IncidentType, RouteType
from core.models import CargoRoute, District, RestrictionZone, TrafficIncident
from django.urls import reverse
from django.utils import timezone
from geo import Geometry

pytestmark = pytest.mark.django_db


@pytest.fixture
def corridor(db, data_source):
    """Коридор с осью, проходящей через центр по меридиану."""
    return CargoRoute.objects.create(
        name="М-2 «Крым»",
        route_type=RouteType.INBOUND,
        distance_km=Decimal("24.00"),
        truck_count_day=1800,
        geom=Geometry.line([[37.620, 55.700], [37.620, 55.760], [37.620, 55.820]]),
        source=data_source,
    )


@pytest.fixture
def bounded_districts(db):
    """Два округа, делящие ось коридора пополам по широте."""
    def square(south: float, north: float) -> Geometry:
        ring = [
            [37.55, south], [37.70, south], [37.70, north], [37.55, north],
            [37.55, south],
        ]
        return Geometry("MULTIPOLYGON", [[ring]])

    return [
        District.objects.create(name="Южный", short_name="ЮАО", geom=square(55.68, 55.76)),
        District.objects.create(name="Северный", short_name="САО", geom=square(55.76, 55.84)),
    ]


class TestBand:
    """Полоса разбора."""

    def test_near_object_is_included(self, corridor, objects):
        """Объект рядом с осью попадает в полосу."""
        result = corridors.analyze(corridor, band_km=3.0)
        names = {item.name for item in result["objects"]}
        assert "Складской комплекс «Юг»" in names

    def test_distant_object_is_excluded(self, corridor, objects):
        """Объект дальше полосы в разбор не попадает."""
        narrow = corridors.analyze(corridor, band_km=1.0)
        wide = corridors.analyze(corridor, band_km=10.0)
        assert narrow["object_count"] < wide["object_count"]

    def test_distance_is_recorded(self, corridor, objects):
        """У каждой записи полосы указано расстояние до оси."""
        result = corridors.analyze(corridor, band_km=5.0)
        assert all(item.corridor_distance_km is not None for item in result["objects"])

    def test_objects_sorted_by_distance(self, corridor, objects):
        """Ближайшие к оси показываются первыми."""
        result = corridors.analyze(corridor, band_km=10.0)
        distances = [item.corridor_distance_km for item in result["objects"]]
        assert distances == sorted(distances)

    def test_area_sums_measured_values(self, corridor, objects):
        """Суммарная площадь складывается из измеренных значений."""
        result = corridors.analyze(corridor, band_km=10.0)
        expected = sum(float(item.area_sq_m) for item in result["objects"] if item.area_sq_m)
        assert result["area_sq_m"] == expected


class TestReaches:
    """Разрез по округам."""

    def test_length_split_between_districts(self, corridor, bounded_districts):
        """Протяжённость раскладывается по округам прохождения."""
        result = corridors.analyze(corridor)
        assert {reach.district.short_name for reach in result["reaches"]} == {"ЮАО", "САО"}

    def test_lengths_do_not_exceed_total(self, corridor, bounded_districts):
        """Сумма участков не превышает протяжённости коридора."""
        result = corridors.analyze(corridor)
        assert sum(reach.length_km for reach in result["reaches"]) <= result["length_km"] + 0.1

    def test_reaches_sorted_by_length(self, corridor, bounded_districts):
        """Округа перечислены в порядке убывания протяжённости."""
        result = corridors.analyze(corridor)
        lengths = [reach.length_km for reach in result["reaches"]]
        assert lengths == sorted(lengths, reverse=True)


class TestZones:
    """Зоны ограничения на пути коридора."""

    @pytest.fixture
    def zone(self, db):
        ring = [
            [37.60, 55.74], [37.65, 55.74], [37.65, 55.78], [37.60, 55.78],
            [37.60, 55.74],
        ]
        return RestrictionZone.objects.create(
            code="ttk", name="Зона ТТК", short_name="ТТК", level=2,
            permit_required_from_tons=Decimal("3.50"), fine_rubles=7500,
            geom=Geometry("MULTIPOLYGON", [[ring]]),
        )

    def test_crossed_zone_is_reported(self, corridor, zone):
        """Зона, которую коридор задевает, попадает в разбор."""
        result = corridors.analyze(corridor)
        assert [item.code for item in result["zones"]] == ["ttk"]
        assert result["permit_zone"].code == "ttk"

    def test_no_zones_outside(self, corridor, db):
        """Коридор вне зон о пропусках не сообщает."""
        result = corridors.analyze(corridor)
        assert result["zones"] == []
        assert result["permit_zone"] is None


class TestIncidents:
    """События в полосе разбора."""

    def test_open_incident_in_band(self, corridor, districts, data_source):
        """Открытое событие рядом с осью попадает в разбор."""
        TrafficIncident.objects.create(
            reported_at=timezone.now(),
            incident_type=IncidentType.ROADWORKS,
            severity=4,
            district=districts[0],
            affects_cargo=True,
            origin=DataOrigin.MEASURED,
            source=data_source,
            geom=Geometry.point(37.621, 55.761),
        )
        assert len(corridors.analyze(corridor)["incidents"]) == 1

    def test_resolved_incident_ignored(self, corridor, districts, data_source):
        """Устранённое событие движению больше не мешает."""
        TrafficIncident.objects.create(
            reported_at=timezone.now(),
            resolved_at=timezone.now(),
            incident_type=IncidentType.ROADWORKS,
            severity=4,
            district=districts[0],
            origin=DataOrigin.MEASURED,
            source=data_source,
            geom=Geometry.point(37.621, 55.761),
        )
        assert corridors.analyze(corridor)["incidents"] == []


class TestRefusal:
    """Отказ разбора называется причиной."""

    def test_corridor_without_geometry(self, db, data_source):
        """Коридор без геометрии разобрать нельзя, и об этом сказано."""
        route = CargoRoute.objects.create(
            name="Без геометрии", route_type=RouteType.TRANSIT, source=data_source
        )
        result = corridors.analyze(route)
        assert result["available"] is False
        assert result["reason"]


class TestPage:
    """Страница разбора."""

    def test_page_opens(self, client, corridor, objects):
        """Страница открывается и берёт первый коридор реестра."""
        response = client.get(reverse("analytics:corridor"))
        assert response.status_code == 200
        assert response.context["route"] == corridor

    def test_band_read_from_query(self, client, corridor):
        """Ширина полосы читается из строки запроса."""
        response = client.get(f"{reverse('analytics:corridor')}?band=5")
        assert response.context["band_km"] == 5.0

    def test_unknown_band_falls_back(self, client, corridor):
        """Значение вне перечня заменяется умолчанием."""
        response = client.get(f"{reverse('analytics:corridor')}?band=42")
        assert response.context["band_km"] == corridors.DEFAULT_BAND_KM

    def test_section_present_in_menu(self, client, db):
        """Разбор коридора доступен из меню грузопотоков."""
        page = client.get(reverse("core:home")).content.decode()
        assert reverse("analytics:corridor") in page
