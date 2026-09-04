"""Определение условий въезда грузового транспорта в зоны ограничения.

Метод: модульное тестирование, позитивные и негативные сценарии,
проверка граничных значений.

Расчёт воспроизводит требования постановления Правительства Москвы
№ 379-ПП от 22.08.2011. Проверяется каждое из них по отдельности и во
взаимодействии: ошибка здесь приводит не к неверному числу на экране,
а к неверному совету, за которым стоит штраф.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from core.models import RestrictionZone
from core.permits import Vehicle, evaluate, is_seasonal_period, zones_at
from geo.geometry import Geometry

pytestmark = pytest.mark.django_db


def square(lon: float, lat: float, half: float) -> Geometry:
    """Квадратная зона со стороной ``2 × half`` вокруг точки."""
    ring = [
        [lon - half, lat - half], [lon + half, lat - half],
        [lon + half, lat + half], [lon - half, lat + half],
        [lon - half, lat - half],
    ]
    return Geometry("MULTIPOLYGON", [[ring]])


@pytest.fixture
def zones(db):
    """Три вложенные зоны с условиями постановления."""
    common = {
        "permit_required_from_tons": Decimal("3.5"),
        "min_ecological_class": 2,
        "seasonal_limit_tons": Decimal("12"),
        "fine_rubles": 7500,
        "legal_basis": "Постановление Правительства Москвы № 379-ПП",
    }
    return [
        RestrictionZone.objects.create(
            code="mkad", name="Зона МКАД", short_name="МКАД", level=1,
            geom=square(37.6, 55.75, 0.30), **common,
        ),
        RestrictionZone.objects.create(
            code="ttk", name="Зона ТТК", short_name="ТТК", level=2,
            geom=square(37.6, 55.75, 0.10), **common,
        ),
        RestrictionZone.objects.create(
            code="sk", name="Зона СК", short_name="СК", level=3,
            geom=square(37.6, 55.75, 0.03), **common,
        ),
    ]


#: Точки внутри соответствующих зон.
CENTRE = (37.60, 55.75)          # внутри всех трёх
BETWEEN_TTK_SK = (37.68, 55.75)  # внутри МКАД и ТТК
BETWEEN_MKAD_TTK = (37.85, 55.75)  # только внутри МКАД
OUTSIDE = (38.50, 55.75)         # вне всех зон


class TestZoneDetection:
    """Определение зон, в которые попадает точка."""

    def test_centre_is_in_all_zones(self, zones):
        assert [z.short_name for z in zones_at(*CENTRE, zones)] == ["МКАД", "ТТК", "СК"]

    def test_between_ttk_and_sk(self, zones):
        assert [z.short_name for z in zones_at(*BETWEEN_TTK_SK, zones)] == ["МКАД", "ТТК"]

    def test_between_mkad_and_ttk(self, zones):
        assert [z.short_name for z in zones_at(*BETWEEN_MKAD_TTK, zones)] == ["МКАД"]

    def test_outside_all_zones(self, zones):
        assert zones_at(*OUTSIDE, zones) == []


class TestPermitRequirement:
    """Требование пропуска в зависимости от массы."""

    def test_light_vehicle_needs_no_permit(self, zones):
        """Транспорт до 3,5 т ограничению не подлежит."""
        verdict = evaluate(Vehicle(Decimal("3.0"), 5), *CENTRE, zones=zones)
        assert not verdict.permit_needed
        assert verdict.is_allowed

    def test_boundary_mass_needs_no_permit(self, zones):
        """Граничное значение 3,5 т включено в разрешённое: ограничение
        установлено для массы «свыше» 3,5 тонны."""
        verdict = evaluate(Vehicle(Decimal("3.5"), 5), *CENTRE, zones=zones)
        assert not verdict.permit_needed

    def test_just_above_boundary_needs_permit(self, zones):
        verdict = evaluate(Vehicle(Decimal("3.51"), 5), *CENTRE, zones=zones)
        assert verdict.permit_needed

    def test_innermost_zone_determines_permit(self, zones):
        """Требуется пропуск самой внутренней из достигаемых зон.

        Пропуск во внутреннюю зону действует и во внешних, поэтому именно
        он снимает вопрос целиком; пропуск внешней зоны во внутренней
        недействителен.
        """
        assert evaluate(Vehicle(Decimal("10"), 5), *CENTRE,
                        zones=zones).required_permit.short_name == "СК"
        assert evaluate(Vehicle(Decimal("10"), 5), *BETWEEN_TTK_SK,
                        zones=zones).required_permit.short_name == "ТТК"
        assert evaluate(Vehicle(Decimal("10"), 5), *BETWEEN_MKAD_TTK,
                        zones=zones).required_permit.short_name == "МКАД"

    def test_outside_zones_needs_nothing(self, zones):
        verdict = evaluate(Vehicle(Decimal("40"), 2), *OUTSIDE, zones=zones)
        assert not verdict.permit_needed
        assert verdict.is_allowed
        assert "вне зон" in verdict.summary().lower()

    def test_fine_is_reported(self, zones):
        verdict = evaluate(Vehicle(Decimal("20"), 5), *CENTRE, zones=zones)
        assert verdict.fine_rubles == 7500


class TestEcologicalClass:
    """Требование к экологическому классу."""

    def test_low_class_is_prohibited(self, zones):
        """Класс ниже требуемого закрывает въезд независимо от пропуска."""
        verdict = evaluate(Vehicle(Decimal("10"), 1), *CENTRE, zones=zones)
        assert not verdict.is_allowed
        assert "Евро" in verdict.prohibitions[0]

    def test_boundary_class_is_allowed(self, zones):
        verdict = evaluate(Vehicle(Decimal("10"), 2), *CENTRE, zones=zones)
        assert verdict.is_allowed

    def test_unknown_class_is_noted_not_prohibited(self, zones):
        """Неуказанный класс не основание для запрета, но требует внимания.

        Отсутствие сведений и несоответствие требованию — разные состояния,
        и смешивать их нельзя: первое устраняется уточнением данных.
        """
        verdict = evaluate(Vehicle(Decimal("10"), None), *CENTRE, zones=zones)
        assert verdict.is_allowed
        assert any("класс не указан" in note.lower() for note in verdict.notes)

    def test_prohibition_survives_permit(self, zones):
        """Пропуск не снимает запрета по экологическому классу."""
        verdict = evaluate(Vehicle(Decimal("20"), 1), *CENTRE, zones=zones)
        assert verdict.permit_needed
        assert not verdict.is_allowed


class TestSeasonalLimit:
    """Сезонное ограничение по массе."""

    @pytest.mark.parametrize(
        "day,expected",
        [
            (date(2026, 7, 3), True),    # пятница в сезон
            (date(2026, 7, 4), True),    # суббота в сезон
            (date(2026, 7, 5), True),    # воскресенье в сезон
            (date(2026, 7, 6), False),   # понедельник в сезон
            (date(2026, 3, 6), False),   # пятница вне сезона
            (date(2026, 12, 5), False),  # суббота вне сезона
        ],
    )
    def test_period_detection(self, day, expected):
        assert is_seasonal_period(day) is expected

    @pytest.mark.parametrize("day", [date(2026, 5, 1), date(2026, 10, 1)])
    def test_period_boundaries_are_included(self, day):
        """Границы периода — 1 мая и 1 октября — входят в него."""
        # Проверяется только попадание в период: день недели задаётся датой.
        from core.permits import SEASONAL_END, SEASONAL_START

        assert SEASONAL_START <= (day.month, day.day) <= SEASONAL_END

    def test_heavy_vehicle_prohibited_in_season(self, zones):
        """Транспорт тяжелее 12 т в выходной день сезона не допускается."""
        verdict = evaluate(Vehicle(Decimal("20"), 5), *CENTRE,
                           moment=date(2026, 7, 4), zones=zones)
        assert not verdict.is_allowed
        assert "Сезонное ограничение" in verdict.prohibitions[0]

    def test_heavy_vehicle_allowed_on_weekday(self, zones):
        """В будний день сезонное ограничение не действует."""
        verdict = evaluate(Vehicle(Decimal("20"), 5), *CENTRE,
                           moment=date(2026, 7, 6), zones=zones)
        assert verdict.is_allowed

    def test_light_vehicle_unaffected(self, zones):
        """Ограничение касается только транспорта тяжелее 12 т."""
        verdict = evaluate(Vehicle(Decimal("8"), 5), *CENTRE,
                           moment=date(2026, 7, 4), zones=zones)
        assert verdict.is_allowed

    def test_without_date_limit_is_a_note(self, zones):
        """Без указания даты ограничение сообщается, но не применяется.

        Расчёт не должен утверждать о конкретном дне того, чего не знает.
        """
        verdict = evaluate(Vehicle(Decimal("20"), 5), *CENTRE, zones=zones)
        assert verdict.is_allowed
        assert any("1 мая" in note for note in verdict.notes)


class TestVehicle:
    """Характеристики транспортного средства."""

    def test_negative_mass_rejected(self):
        with pytest.raises(ValueError):
            Vehicle(Decimal("-1"))


class TestSummary:
    """Краткое заключение."""

    def test_prohibition_wins(self, zones):
        verdict = evaluate(Vehicle(Decimal("20"), 1), *CENTRE, zones=zones)
        assert verdict.summary() == "Въезд запрещён"

    def test_permit_named(self, zones):
        verdict = evaluate(Vehicle(Decimal("20"), 5), *BETWEEN_MKAD_TTK, zones=zones)
        assert "МКАД" in verdict.summary()

    def test_allowed_without_permit(self, zones):
        verdict = evaluate(Vehicle(Decimal("2"), 5), *CENTRE, zones=zones)
        assert "без пропуска" in verdict.summary()
