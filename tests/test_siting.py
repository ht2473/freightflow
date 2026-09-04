"""Тесты подбора площадки под требования перевозчика.

Метод: функциональное тестирование расчёта — проверяется, что требования
отсекают непригодное, что порядок площадок отвечает измеренным величинам
и что оценка объявлена относительной там, где она относительна.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from analytics import siting
from core.models import RestrictionZone
from django.urls import reverse
from geo import Geometry

pytestmark = pytest.mark.django_db


@pytest.fixture
def frame_road(db, districts, data_source):
    """Магистраль грузового каркаса, проходящая рядом с первым объектом."""
    from core.models import RoadSegment

    return RoadSegment.objects.create(
        name="Каширское шоссе",
        road_class="arterial",
        district=districts[0],
        in_freight_frame=True,
        geom=Geometry.line([[37.620, 55.735], [37.620, 55.760]]),
        source=data_source,
    )


@pytest.fixture
def prepared(objects, frame_road):
    """Реестр с измеренными площадями и сброшенным кешем опорных точек."""
    siting.invalidate()
    return objects


class TestRequirements:
    """Требования отсекают непригодное."""

    def test_area_requirement(self, prepared):
        """Площадка меньше требуемой площади в отбор не попадает."""
        result = siting.select(siting.Requirements(min_area_sq_m=20000))
        assert result["matched"] < result["considered"]
        assert all(item.values["area"] >= 20000 for item in result["candidates"])

    def test_district_requirement(self, prepared, districts):
        """Отбор по округу оставляет только его площадки."""
        result = siting.select(siting.Requirements(district_id=districts[0].pk))
        assert result["candidates"]
        assert all(
            item.obj.district_id == districts[0].pk for item in result["candidates"]
        )

    def test_type_requirement(self, prepared, infrastructure_types):
        """Отбор по типу оставляет только объекты этого типа."""
        wanted = infrastructure_types[1]
        result = siting.select(siting.Requirements(type_id=wanted.pk))
        assert all(item.obj.type_id == wanted.pk for item in result["candidates"])

    def test_frame_distance_requirement(self, prepared):
        """Предельное удаление от каркаса отсекает дальние площадки."""
        near = siting.select(siting.Requirements(max_frame_km=1.0))
        far = siting.select(siting.Requirements(max_frame_km=100.0))
        assert near["matched"] < far["matched"]

    def test_objects_without_coordinates_excluded(self, prepared):
        """Площадка без координат в подбор не попадает: расстояние не измерить."""
        names = {item.obj.name for item in siting.select(siting.Requirements())["candidates"]}
        assert "Склад без координат" not in names


class TestRanking:
    """Сопоставление площадок."""

    def test_area_alone_orders_by_area(self, prepared):
        """При единственной значимой составляющей порядок задаёт она."""
        weights = {code: 0.0 for code in siting.DEFAULT_WEIGHTS}
        weights["area"] = 1.0
        result = siting.select(siting.Requirements(weights=weights))
        areas = [item.values["area"] for item in result["candidates"]]
        assert areas == sorted(areas, reverse=True)

    def test_frame_alone_orders_by_distance(self, prepared):
        """Составляющая «меньше — лучше» упорядочивает по возрастанию."""
        weights = {code: 0.0 for code in siting.DEFAULT_WEIGHTS}
        weights["frame"] = 1.0
        result = siting.select(siting.Requirements(weights=weights))
        distances = [item.values["frame"] for item in result["candidates"]]
        assert distances == sorted(distances)

    def test_score_is_within_scale(self, prepared):
        """Оценка выражена в стобалльной шкале."""
        result = siting.select(siting.Requirements())
        assert all(0 <= item.total <= 100 for item in result["candidates"])

    def test_best_and_worst_bound_the_scale(self, prepared):
        """Нормирование по выборке ставит крайние площадки на границы шкалы."""
        weights = {code: 0.0 for code in siting.DEFAULT_WEIGHTS}
        weights["area"] = 1.0
        candidates = siting.select(siting.Requirements(weights=weights))["candidates"]
        assert candidates[0].total == 100.0
        assert candidates[-1].total == 0.0

    def test_shortlist_is_capped(self, prepared):
        """Итог ограничен обозримым числом площадок."""
        result = siting.select(siting.Requirements())
        assert len(result["candidates"]) <= siting.SHORTLIST


class TestPermitBurden:
    """Разрешительная нагрузка площадки."""

    @pytest.fixture
    def zone(self, db):
        """Зона, накрывающая центр города, с порогом пропуска в 3,5 тонны."""
        ring = [
            [37.60, 55.73], [37.65, 55.73], [37.65, 55.77], [37.60, 55.77],
            [37.60, 55.73],
        ]
        return RestrictionZone.objects.create(
            code="ttk", name="Зона ТТК", short_name="ТТК", level=2,
            permit_required_from_tons=Decimal("3.50"), fine_rubles=7500,
            geom=Geometry("MULTIPOLYGON", [[ring]]),
        )

    def test_heavy_vehicle_needs_permit(self, prepared, zone):
        """Тяжёлой машине для площадки внутри зоны нужен пропуск."""
        result = siting.select(siting.Requirements(mass_tons=Decimal("20")))
        inside = [item for item in result["candidates"] if item.zones]
        assert inside and inside[0].permit_zone.code == "ttk"

    def test_light_vehicle_carries_no_burden(self, prepared, zone):
        """Машине ниже порога зона разрешительной нагрузки не создаёт."""
        result = siting.select(siting.Requirements(mass_tons=Decimal("2")))
        assert all(not item.zones for item in result["candidates"])


class TestPage:
    """Страница подбора."""

    def test_page_opens(self, client, prepared):
        """Страница открывается и без заданных требований."""
        response = client.get(reverse("analytics:siting"))
        assert response.status_code == 200
        assert response.context["result"]["candidates"]

    def test_requirements_read_from_query(self, client, prepared):
        """Требования читаются из строки запроса, поэтому набор адресуем."""
        response = client.get(f"{reverse('analytics:siting')}?area=20000&mass=12,5")
        requirements = response.context["requirements"]
        assert requirements.min_area_sq_m == 20000
        assert requirements.mass_tons == Decimal("12.5")

    def test_zero_weight_excludes_criterion(self, client, prepared):
        """Нулевой вес исключает составляющую из оценки."""
        response = client.get(f"{reverse('analytics:siting')}?w_area=0")
        assert response.context["requirements"].weights["area"] == 0.0

    def test_weight_is_bounded(self, client, prepared):
        """Вес ограничен допустимыми значениями."""
        response = client.get(f"{reverse('analytics:siting')}?w_area=99")
        assert response.context["requirements"].weights["area"] == 3.0

    def test_section_present_in_menu(self, client, db):
        """Подбор площадки доступен из меню раздела инфраструктуры."""
        page = client.get(reverse("core:home")).content.decode()
        assert reverse("analytics:siting") in page
