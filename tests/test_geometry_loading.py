"""Исключение крупной геометрии из выборок.

Метод: модульное тестирование, проверка нефункционального требования.

Граница административного округа — мультиполигон в тысячи вершин, который
хранится текстом и разбирается при чтении. Соединение со справочником округов
присоединяет эту колонку к каждой строке выборки: реестр в тысячу объектов
заставляет разобрать одну и ту же границу тысячу раз.

Издержка не видна ни в числе запросов, ни в объёме ответа, поэтому обычные
проверки её не улавливают. Здесь она закрепляется прямо: выборка обязана
не содержать поля геометрии, пока его не запросили явно.
"""

from __future__ import annotations

import pytest
from core.models import District, InfrastructureObject

pytestmark = pytest.mark.django_db


def deferred_fields(instance) -> set[str]:
    """Поля, которые выборка не загрузила."""
    return instance.get_deferred_fields()


class TestDistrictManager:
    """Справочник округов отдаётся без границ."""

    def test_geometry_is_deferred_by_default(self, districts):
        """Обычное обращение к справочнику границ не загружает."""
        district = District.objects.first()
        assert "geom" in deferred_fields(district)

    def test_geometry_available_on_request(self, districts):
        """Метод with_geometry возвращает границы."""
        district = District.objects.with_geometry().first()
        assert "geom" not in deferred_fields(district)

    def test_other_fields_are_loaded(self, districts):
        """Всё, кроме границ, доступно без дополнительных запросов."""
        district = District.objects.first()
        deferred = deferred_fields(district)
        assert deferred == {"geom"}
        # Обращение к этим полям не должно порождать запросов.
        assert district.name and district.short_name

    def test_deferred_geometry_still_accessible(self, districts):
        """Границы остаются доступны, хотя и ценой отдельного запроса.

        Исключение поля из выборки не должно ломать код, которому оно
        понадобилось: Django загрузит его по обращению.
        """
        district = District.objects.first()
        assert district.geom is None or district.geom.geom_type


class TestInfrastructureQuerySet:
    """Реестр объектов не тянет за собой границы округов."""

    def test_with_refs_defers_district_geometry(self, objects):
        """Выборка со справочниками исключает границы округа и контур."""
        facility = InfrastructureObject.objects.with_refs().first()
        assert "footprint" in deferred_fields(facility)
        assert "geom" in deferred_fields(facility.district)

    def test_with_footprint_keeps_object_outline(self, objects):
        """Карточка объекта получает его контур, но не границы округа."""
        facility = InfrastructureObject.objects.with_footprint().first()
        assert "footprint" not in deferred_fields(facility)
        assert "geom" in deferred_fields(facility.district)

    def test_refs_are_prefetched(self, django_assert_num_queries, objects):
        """Справочники подтянуты одним запросом: защита от N+1 сохранена.

        Исключение полей не должно превратиться в отказ от соединения:
        обращение к типу и округу обязано обходиться без новых запросов.
        """
        facilities = list(InfrastructureObject.objects.with_refs()[:5])
        with django_assert_num_queries(0):
            for facility in facilities:
                assert facility.type.name
                assert facility.district.short_name


class TestRelatedQuerySets:
    """Прочие выборки, соединённые со справочником округов."""

    def test_incidents_defer_district_geometry(self, incidents):
        """Реестр дорожных событий не загружает границы округов."""
        from core.models import TrafficIncident

        incident = TrafficIncident.objects.with_refs().first()
        assert "geom" in deferred_fields(incident.road.district)
