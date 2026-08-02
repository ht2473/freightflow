"""Модульные тесты доменных моделей.

Метод: модульное тестирование производных свойств моделей. Проверяется
логика вычисляемых характеристик, не затрагивающая представления.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


class TestDistrict:
    """Административный округ."""

    def test_density_calculated(self, districts):
        """Плотность населения считается как отношение к площади."""
        district = districts[0]
        expected = float(district.population) / float(district.area_sq_km)
        assert district.density == pytest.approx(expected)

    def test_density_none_without_population(self, districts):
        """При отсутствии данных о населении плотность не определена."""
        district = districts[0]
        district.population = None
        assert district.density is None

    def test_map_center_uses_center(self, districts):
        """Центр для карты берётся из заполненного поля координат."""
        lon, lat = districts[0].map_center
        assert lon == pytest.approx(37.6208)
        assert lat == pytest.approx(55.7539)

    def test_map_center_none_when_empty(self, db):
        """Без координат и границ центр не определён."""
        from core.models import District

        district = District.objects.create(name="Пустой", short_name="ПУС")
        assert district.map_center is None

    def test_absolute_url(self, districts):
        """Адрес карточки округа формируется корректно."""
        assert districts[0].get_absolute_url() == f"/districts/{districts[0].pk}/"


class TestCargoCategory:
    """Категория груза."""

    def test_non_hazardous(self, cargo_categories):
        """Нулевой класс означает неопасный груз."""
        assert cargo_categories[0].is_hazardous is False

    def test_hazardous(self, cargo_categories):
        """Ненулевой класс означает опасный груз."""
        assert cargo_categories[1].is_hazardous is True

    def test_hazard_label(self, cargo_categories):
        """Класс опасности расшифровывается словесно."""
        assert "3" in cargo_categories[1].hazard_label


class TestInfrastructureObject:
    """Объект логистической инфраструктуры."""

    def test_round_the_clock_detection(self, objects):
        """Круглосуточный режим распознаётся по тексту поля."""
        assert objects[0].is_round_the_clock is True
        assert objects[2].is_round_the_clock is False

    def test_utilization_hint(self, objects):
        """Удельная мощность — отношение ёмкости к площади."""
        obj = objects[0]
        expected = float(obj.capacity_tons) / float(obj.area_sq_m)
        assert obj.utilization_hint == pytest.approx(expected)

    def test_utilization_none_without_area(self, objects):
        """Без площади удельная мощность не рассчитывается."""
        obj = objects[0]
        obj.area_sq_m = None
        assert obj.utilization_hint is None

    def test_queryset_filters(self, objects, districts, infrastructure_types):
        """Методы выборки накладывают ожидаемые условия."""
        from core.models import InfrastructureObject

        in_district = InfrastructureObject.objects.in_district(districts[0].pk)
        assert all(o.district_id == districts[0].pk for o in in_district)

        of_type = InfrastructureObject.objects.of_type(infrastructure_types[1].pk)
        assert all(o.type_id == infrastructure_types[1].pk for o in of_type)

    def test_search_by_name(self, objects):
        """Поиск находит записи по подстроке в наименовании."""
        from core.models import InfrastructureObject

        found = InfrastructureObject.objects.search("Терминал")
        assert found.count() == 2

    def test_located_excludes_without_geometry(self, objects):
        """Выборка для карты исключает записи без координат."""
        from core.models import InfrastructureObject

        assert InfrastructureObject.objects.located().count() == 4

    def test_empty_search_returns_all(self, objects):
        """Пустой поисковый запрос не сужает выборку."""
        from core.models import InfrastructureObject

        assert InfrastructureObject.objects.search("").count() == 5


class TestRoadSegment:
    """Участок дорожной сети."""

    def test_capacity_index(self, roads):
        """Условная пропускная способность — произведение полос на скорость."""
        road = roads[0]
        assert road.capacity_index == pytest.approx(road.lanes * road.speed_limit_kmh)

    def test_capacity_none_without_lanes(self, roads):
        """Без числа полос показатель не рассчитывается."""
        road = roads[0]
        road.lanes = None
        assert road.capacity_index is None

    def test_latest_condition(self, roads, conditions):
        """Возвращается последний по времени замер."""
        latest = roads[0].latest_condition
        assert latest is not None
        assert latest.congestion_level == 5


class TestTrafficCondition:
    """Замер дорожной обстановки."""

    def test_state_mapping(self, conditions):
        """Балл загруженности отображается в состояние движения."""
        condition = conditions[0]
        code, label, tone = condition.state
        assert code in {"free", "light", "moderate", "heavy", "jam"}
        # Подпись — отложенная строка перевода, а не обычная str: приводим
        # её к строке в момент проверки, как это делает шаблон при выводе.
        assert str(label)

    def test_speed_ratio(self, conditions, roads):
        """Отношение скоростей вычисляется относительно разрешённой."""
        condition = conditions[0]
        expected = float(condition.avg_speed_kmh) / float(condition.road.speed_limit_kmh)
        assert condition.speed_ratio == pytest.approx(expected)

    def test_speed_ratio_none_without_limit(self, conditions):
        """Без разрешённой скорости отношение не определено."""
        condition = conditions[0]
        condition.road.speed_limit_kmh = None
        assert condition.speed_ratio is None


class TestTrafficIncident:
    """Дорожный инцидент."""

    def test_open_incident(self, incidents):
        """Незакрытое событие помечается как открытое."""
        assert incidents[0].is_open is True

    def test_closed_incident(self, incidents):
        """Событие с отметкой устранения считается закрытым."""
        assert incidents[1].is_open is False

    def test_duration_for_closed(self, incidents):
        """Длительность закрытого события положительна и конечна."""
        assert incidents[1].duration_hours > 0

    def test_duration_for_open_uses_now(self, incidents):
        """Для открытого события длительность отсчитывается до текущего момента."""
        assert incidents[0].duration_hours > 0

    def test_severity_state(self, incidents):
        """Уровень серьёзности получает словесную оценку и тон."""
        label, tone = incidents[2].severity_state
        assert label and tone in {"ok", "warn", "alert", "crit", "muted"}

    def test_open_queryset(self, incidents):
        """Выборка открытых событий возвращает только незакрытые."""
        from core.models import TrafficIncident

        assert TrafficIncident.objects.open().count() == 2

    def test_cargo_queryset(self, incidents):
        """Выборка по влиянию на грузовой транспорт корректна."""
        from core.models import TrafficIncident

        assert TrafficIncident.objects.affecting_cargo().count() == 2


class TestCargoRoute:
    """Грузовой маршрут."""

    def test_average_speed(self, routes):
        """Средняя скорость — отношение расстояния ко времени."""
        route = routes[0]
        expected = float(route.distance_km) / float(route.avg_duration_h)
        assert route.avg_speed_kmh == pytest.approx(expected)

    def test_daily_load_index(self, routes):
        """Транспортная работа — произведение интенсивности на расстояние."""
        route = routes[0]
        expected = route.truck_count_day * float(route.distance_km)
        assert route.daily_load_index == pytest.approx(expected)

    def test_speed_none_without_duration(self, routes):
        """Без времени в пути скорость не рассчитывается."""
        route = routes[0]
        route.avg_duration_h = None
        assert route.avg_speed_kmh is None


class TestFreightFlowStat:
    """Показатель грузопотока."""

    def test_average_load(self, flows):
        """Средняя загрузка рейса — отношение объёма к числу рейсов."""
        flow = flows[0]
        expected = float(flow.volume_tons) / flow.vehicle_count
        assert flow.avg_load_per_vehicle == pytest.approx(expected)

    def test_average_load_none_without_vehicles(self, flows):
        """Без данных о рейсах загрузка не определяется."""
        flow = flows[0]
        flow.vehicle_count = None
        assert flow.avg_load_per_vehicle is None


class TestEtlRun:
    """Запись журнала загрузки."""

    def test_duration_minutes(self, db, data_source):
        """Длительность считается в минутах."""
        from core.models import EtlRun

        started = timezone.now() - timedelta(minutes=15)
        run = EtlRun.objects.create(
            started_at=started, finished_at=started + timedelta(minutes=15),
            source=data_source, target_table="objects",
            records_loaded=100, records_errors=0, status="success",
        )
        assert run.duration_minutes == pytest.approx(15, abs=0.1)

    def test_error_rate(self, db, data_source):
        """Доля ошибок считается от общего числа обработанных записей."""
        from core.models import EtlRun

        run = EtlRun.objects.create(
            source=data_source, target_table="objects",
            records_loaded=90, records_errors=10, status="partial",
        )
        assert run.error_rate == pytest.approx(10.0)

    def test_error_rate_zero_when_empty(self, db, data_source):
        """При отсутствии обработанных записей доля ошибок равна нулю."""
        from core.models import EtlRun

        run = EtlRun.objects.create(
            source=data_source, target_table="objects",
            records_loaded=0, records_errors=0, status="success",
        )
        assert run.error_rate == 0.0
