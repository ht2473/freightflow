"""Профили грузового транспорта для маршрутизации.

Маршрут грузового автомобиля определяется не только длиной пути. Габариты
и масса закрывают часть сети: под путепроводом высотой 3,5 м фура не
пройдёт, а на улице с ограничением 3,5 т ей не место независимо от того,
что дорога соединяет нужные адреса. Поэтому маршрутизатор получает
характеристики конкретного транспортного средства, а не абстрактный
«грузовой» режим.

Значения профилей взяты из технического регламента Таможенного союза
018/2011 и приложения 3 к Правилам дорожного движения: предельные габариты
транспортного средства без специального разрешения — высота 4,0 м, ширина
2,55 м, длина одиночного автомобиля 12 м, автопоезда — 20 м.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class TruckProfile:
    """Характеристики транспортного средства, существенные для маршрута.

    Атрибуты:
        code: обозначение профиля;
        title: название для интерфейса;
        mass_tons: разрешённая максимальная масса, т;
        axle_load_tons: нагрузка на ось, т;
        height_m, width_m, length_m: габариты, м;
        hazmat: перевозка опасного груза.
    """

    code: str
    title: str
    mass_tons: Decimal
    axle_load_tons: Decimal
    height_m: Decimal
    width_m: Decimal
    length_m: Decimal
    hazmat: bool = False

    def costing_options(self) -> dict:
        """Параметры расчёта в том виде, в каком их принимает маршрутизатор.

        Единицы измерения заданы службой: масса — тонны, габариты — метры.
        """
        return {
            "truck": {
                "height": float(self.height_m),
                "width": float(self.width_m),
                "length": float(self.length_m),
                "weight": float(self.mass_tons),
                "axle_load": float(self.axle_load_tons),
                "hazmat": self.hazmat,
                # Штраф за разворот и за использование дорог, не предназначенных
                # для грузового движения: маршрут по дворовым проездам формально
                # короче, но проехать по нему фура не может.
                "use_highways": 1.0,
                "top_speed": 90,
            }
        }


#: Профили, отражающие основные классы городского грузового транспорта.
#: Порог 3,5 т разделяет транспорт, которому пропуск в зоны ограничения
#: не нужен, и тот, которому он требуется, поэтому лёгкий профиль
#: существует отдельно.
PROFILES: tuple[TruckProfile, ...] = (
    TruckProfile(
        code="light",
        title=_("Малотоннажный, до 3,5 т"),
        mass_tons=Decimal("3.5"),
        axle_load_tons=Decimal("2.5"),
        height_m=Decimal("2.7"),
        width_m=Decimal("2.1"),
        length_m=Decimal("6.0"),
    ),
    TruckProfile(
        code="medium",
        title=_("Среднетоннажный, до 12 т"),
        mass_tons=Decimal("12.0"),
        axle_load_tons=Decimal("6.0"),
        height_m=Decimal("3.5"),
        width_m=Decimal("2.5"),
        length_m=Decimal("9.0"),
    ),
    TruckProfile(
        code="semi",
        title=_("Седельный тягач с полуприцепом, 40 т"),
        mass_tons=Decimal("40.0"),
        axle_load_tons=Decimal("9.0"),
        height_m=Decimal("4.0"),
        width_m=Decimal("2.55"),
        length_m=Decimal("16.5"),
    ),
)

#: Профиль по умолчанию: седельный тягач — расчётное транспортное средство
#: для складской логистики и самое ограниченное в правах проезда.
DEFAULT_PROFILE = "semi"


def get(code: str | None) -> TruckProfile:
    """Профиль по обозначению; при неизвестном — профиль по умолчанию."""
    lookup = {profile.code: profile for profile in PROFILES}
    return lookup.get(code or "", lookup[DEFAULT_PROFILE])


def choices() -> list[tuple[str, str]]:
    """Перечень профилей для формы выбора."""
    return [(profile.code, str(profile.title)) for profile in PROFILES]


__all__ = ["DEFAULT_PROFILE", "PROFILES", "TruckProfile", "choices", "get"]
