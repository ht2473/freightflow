"""Тесты раздела допуска грузового транспорта.

Метод: функциональное тестирование — проверяется, что расчёт условий въезда
доходит до страницы в том виде, в каком его выдаёт нормативный модуль, что
точку можно задать и координатой, и объектом реестра, и что реестр зон
показывает условия постановления, а не пересказ.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from core.models import RestrictionZone
from django.urls import reverse
from geo import Geometry

pytestmark = pytest.mark.django_db

# Точка внутри всех трёх зон — Театральная площадь.
CENTRE = (37.6186, 55.7602)

# Точка за пределами колец — вне зон ограничения.
OUTSIDE = (37.2000, 55.5000)


@pytest.fixture
def zones(db):
    """Три вложенные зоны с границами в виде вложенных квадратов."""

    def square(half: float) -> Geometry:
        lon, lat = CENTRE
        ring = [
            [lon - half, lat - half], [lon + half, lat - half],
            [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half],
        ]
        return Geometry("MULTIPOLYGON", [[ring]])

    return [
        RestrictionZone.objects.create(
            code="mkad", name="Зона МКАД", short_name="МКАД", level=1,
            permit_required_from_tons=Decimal("12.00"),
            min_ecological_class=2, fine_rubles=5000,
            legal_basis="ПП Москвы № 379-ПП от 22.08.2011",
            geom=square(0.30), area_sq_km=Decimal("868.20"),
            perimeter_km=Decimal("108.40"),
        ),
        RestrictionZone.objects.create(
            code="ttk", name="Зона Третьего транспортного кольца", short_name="ТТК",
            level=2, permit_required_from_tons=Decimal("3.50"),
            min_ecological_class=3, seasonal_limit_tons=Decimal("12.00"),
            fine_rubles=7500, legal_basis="ПП Москвы № 379-ПП от 22.08.2011",
            geom=square(0.10), area_sq_km=Decimal("84.40"),
            perimeter_km=Decimal("34.90"),
        ),
        RestrictionZone.objects.create(
            code="sk", name="Зона Садового кольца", short_name="СК", level=3,
            permit_required_from_tons=Decimal("1.00"),
            min_ecological_class=3, fine_rubles=7500,
            legal_basis="ПП Москвы № 379-ПП от 22.08.2011",
            geom=square(0.03), area_sq_km=Decimal("18.80"),
            perimeter_km=Decimal("15.60"),
        ),
    ]


class TestZoneRegistry:
    """Реестр зон ограничения."""

    def test_list_shows_all_zones(self, client, zones):
        """Перечень содержит все зоны в порядке вложенности."""
        response = client.get(reverse("core:zone_list"))
        assert response.status_code == 200
        assert [item.code for item in response.context["zones"]] == ["mkad", "ttk", "sk"]

    def test_list_counts_objects_inside(self, client, zones, objects):
        """Рядом с зоной показано число объектов реестра внутри неё."""
        response = client.get(reverse("core:zone_list"))
        inside = response.context["inside"]
        # Внешняя зона объемлет внутренние, поэтому не может содержать меньше.
        assert inside[zones[0].pk] >= inside[zones[2].pk]

    def test_detail_lists_nesting(self, client, zones):
        """Карточка называет объемлющие и вложенные зоны."""
        response = client.get(reverse("core:zone_detail", args=[zones[1].pk]))
        assert response.status_code == 200
        assert [item.code for item in response.context["outer"]] == ["mkad"]
        assert [item.code for item in response.context["inner"]] == ["sk"]

    def test_detail_shows_legal_basis(self, client, zones):
        """Нормативное основание выведено на страницу."""
        page = client.get(reverse("core:zone_detail", args=[zones[0].pk])).content.decode()
        assert "379-ПП" in page


class TestPermitCheck:
    """Расчёт условий допуска."""

    def test_page_opens_without_parameters(self, client, zones):
        """Страница открывается до расчёта и заключения не показывает."""
        response = client.get(reverse("core:permit_check"))
        assert response.status_code == 200
        assert response.context["verdict"] is None

    def test_point_outside_zones(self, client, zones):
        """Точка вне колец не требует пропуска."""
        response = client.get(
            f"{reverse('core:permit_check')}?mass=40&lon={OUTSIDE[0]}&lat={OUTSIDE[1]}"
        )
        verdict = response.context["verdict"]
        assert verdict.zones == []
        assert not verdict.permit_needed

    def test_innermost_zone_defines_permit(self, client, zones):
        """Требуется пропуск самой внутренней из достигаемых зон."""
        response = client.get(
            f"{reverse('core:permit_check')}?mass=40&eco=5&lon={CENTRE[0]}&lat={CENTRE[1]}"
        )
        verdict = response.context["verdict"]
        assert len(verdict.zones) == 3
        assert verdict.required_permit.code == "sk"
        assert verdict.fine_rubles == 7500

    def test_light_vehicle_needs_no_permit(self, client, zones):
        """Малотоннажный автомобиль въезжает без пропуска."""
        response = client.get(
            f"{reverse('core:permit_check')}?mass=0.9&eco=5&lon={CENTRE[0]}&lat={CENTRE[1]}"
        )
        assert not response.context["verdict"].permit_needed

    def test_ecological_class_prohibits_entry(self, client, zones):
        """Экологический класс ниже требуемого запрещает въезд."""
        response = client.get(
            f"{reverse('core:permit_check')}?mass=20&eco=1&lon={CENTRE[0]}&lat={CENTRE[1]}"
        )
        verdict = response.context["verdict"]
        assert verdict.prohibitions
        assert not verdict.is_allowed

    def test_object_of_registry_sets_the_point(self, client, zones, objects):
        """Выбор объекта реестра задаёт точку расчёта."""
        target = objects[0]
        response = client.get(
            f"{reverse('core:permit_check')}?mass=20&object={target.pk}"
        )
        assert response.context["target"] == target
        assert response.context["verdict"] is not None

    def test_comma_in_mass_is_accepted(self, client, zones):
        """Масса принимается и с десятичной запятой."""
        response = client.get(
            f"{reverse('core:permit_check')}?mass=12,5&lon={CENTRE[0]}&lat={CENTRE[1]}"
        )
        assert response.context["form"]["mass"] == Decimal("12.5")

    def test_unreadable_mass_falls_back_to_default(self, client, zones):
        """Негодное значение массы заменяется умолчанием, а не ошибкой."""
        response = client.get(f"{reverse('core:permit_check')}?mass=abc")
        assert response.status_code == 200
        assert response.context["form"]["mass"] == Decimal("3.5")

    def test_seasonal_limit_reported_for_summer_weekend(self, client, zones):
        """В летний выходной сезонное ограничение объявляется действующим."""
        response = client.get(f"{reverse('core:permit_check')}?date=2026-07-04")
        assert response.context["seasonal_today"] is True

    def test_seasonal_limit_not_reported_in_winter(self, client, zones):
        """Зимой сезонное ограничение не действует."""
        response = client.get(f"{reverse('core:permit_check')}?date=2026-01-10")
        assert response.context["seasonal_today"] is False


class TestNavigation:
    """Раздел встроен в навигацию системы."""

    def test_section_present_in_menu(self, client, db):
        """Пункт «Допуск» есть в главном меню."""
        keys = {item.key for item in client.get(reverse("core:home")).context["MAIN_NAV"]}
        assert "access" in keys

    def test_sitemap_lists_permit_page(self, client, db):
        """Карта сайта содержит страницу условий допуска."""
        page = client.get(reverse("core:sitemap")).content.decode()
        assert reverse("core:permit_check") in page
