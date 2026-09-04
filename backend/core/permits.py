"""Определение условий въезда грузового транспорта в зоны ограничения.

Модуль отвечает на вопрос, ради которого строятся зоны: что требуется
транспортному средству с заданными характеристиками, чтобы законно оказаться
в заданной точке города.

Основание — постановление Правительства Москвы № 379-ПП от 22.08.2011.
Условия зон хранятся в справочнике :class:`core.models.RestrictionZone`,
а этот модуль применяет их к конкретному транспортному средству. Разделение
намеренное: изменение нормативного акта правит данные, а не расчёт.

Правила, которые модуль воспроизводит:

* зоны вложены, и пропуск во внутреннюю зону действует во всех внешних —
  поэтому требуется пропуск самой внутренней из достигаемых зон;
* ограничение по разрешённой максимальной массе действует круглосуточно;
* въезд запрещён транспорту ниже установленного экологического класса,
  и пропуск такого запрета не снимает;
* с 1 мая по 1 октября в выходные и предпраздничные дни с 06:00 до 24:00
  действует дополнительное ограничение по массе.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from .models import RestrictionZone

#: Период действия сезонного ограничения: с 1 мая по 1 октября включительно.
SEASONAL_START = (5, 1)
SEASONAL_END = (10, 1)

#: Часы действия сезонного ограничения.
SEASONAL_FROM = time(6, 0)
SEASONAL_UNTIL = time(23, 59, 59)

#: Дни недели, в которые действует сезонное ограничение: пятница, суббота,
#: воскресенье. Нумерация Python: понедельник — 0.
SEASONAL_WEEKDAYS = frozenset({4, 5, 6})


@dataclass(frozen=True)
class Vehicle:
    """Характеристики транспортного средства, существенные для допуска.

    Атрибуты:
        mass_tons: разрешённая максимальная масса, тонн;
        ecological_class: экологический класс «Евро»; ``None`` — не установлен.
    """

    mass_tons: Decimal
    ecological_class: int | None = None

    def __post_init__(self) -> None:
        if self.mass_tons < 0:
            raise ValueError("Разрешённая максимальная масса не может быть отрицательной")


@dataclass
class Verdict:
    """Заключение о допуске транспортного средства в точку города.

    Атрибуты:
        zones: зоны, в которые попадает точка, от внешней к внутренней;
        required_permit: зона, пропуск в которую требуется, либо ``None``;
        prohibitions: обстоятельства, при которых въезд запрещён и пропуск
            положения не исправляет;
        notes: пояснения, не влияющие на допуск.
    """

    zones: list[RestrictionZone] = field(default_factory=list)
    required_permit: RestrictionZone | None = None
    prohibitions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_allowed(self) -> bool:
        """Допущено ли транспортное средство при наличии требуемого пропуска."""
        return not self.prohibitions

    @property
    def permit_needed(self) -> bool:
        return self.required_permit is not None

    @property
    def fine_rubles(self) -> int | None:
        """Ответственность за въезд без пропуска."""
        return self.required_permit.fine_rubles if self.required_permit else None

    def summary(self) -> str:
        """Краткое заключение одной строкой."""
        if self.prohibitions:
            return str(_("Въезд запрещён"))
        if self.required_permit:
            return str(_("Требуется пропуск: зона «%(zone)s»")) % {
                "zone": self.required_permit.short_name
            }
        if self.zones:
            return str(_("Въезд без пропуска разрешён"))
        return str(_("Вне зон ограничения"))


def is_seasonal_period(moment: date) -> bool:
    """Действует ли сезонное ограничение в указанный день.

    Ограничение установлено на период с 1 мая по 1 октября и применяется
    по пятницам, выходным и предпраздничным дням. Перечень нерабочих
    праздничных дней здесь не учитывается: он устанавливается ежегодно
    и в систему не загружается, поэтому заключение о таких днях было бы
    основано на догадке.
    """
    month_day = (moment.month, moment.day)
    if not (SEASONAL_START <= month_day <= SEASONAL_END):
        return False
    return moment.weekday() in SEASONAL_WEEKDAYS


def zones_at(lon: float, lat: float,
             zones: list[RestrictionZone] | None = None) -> list[RestrictionZone]:
    """Зоны ограничения, в которые попадает точка, от внешней к внутренней."""
    catalogue = zones if zones is not None else list(
        RestrictionZone.objects.exclude(geom__isnull=True).order_by("level")
    )
    return [zone for zone in catalogue if zone.contains(lon, lat)]


def zones_along(points: Sequence[Sequence[float]],
                zones: list[RestrictionZone] | None = None) -> list[RestrictionZone]:
    """Зоны ограничения, которые задевает маршрут, от внешней к внутренней.

    Маршрут проверяется по вершинам ломаной. Зона, пройденная насквозь между
    двумя далеко отстоящими вершинами, при таком способе не обнаружится,
    поэтому шаг вершин маршрута существенен: маршрутизатор отдаёт их чаще,
    чем меняется геометрия зон.
    """
    catalogue = zones if zones is not None else list(
        RestrictionZone.objects.exclude(geom__isnull=True).order_by("level")
    )
    return [
        zone
        for zone in catalogue
        if any(zone.contains(point[0], point[1]) for point in points)
    ]


def evaluate(vehicle: Vehicle, lon: float, lat: float,
             moment: date | None = None,
             zones: list[RestrictionZone] | None = None) -> Verdict:
    """Определить условия въезда транспортного средства в заданную точку."""
    return _verdict(vehicle, zones_at(lon, lat, zones), moment)


def evaluate_route(vehicle: Vehicle, points: Sequence[Sequence[float]],
                   moment: date | None = None,
                   zones: list[RestrictionZone] | None = None) -> Verdict:
    """Определить условия проезда по маршруту.

    Требования определяет самая внутренняя из зон, которые маршрут задевает:
    пропуск нужен для въезда в неё, а действует он и во всех внешних.
    """
    return _verdict(vehicle, zones_along(points, zones), moment)


def _verdict(vehicle: Vehicle, reached: list[RestrictionZone],
             moment: date | None) -> Verdict:
    """Заключение по набору достигнутых зон."""
    verdict = Verdict(zones=reached)

    if not reached:
        verdict.notes.append(
            str(_("Точка расположена вне зон ограничения движения грузового транспорта"))
        )
        return verdict

    # Самая внутренняя из достигаемых зон определяет требования: её условия
    # строже, а выданный в неё пропуск действует и во внешних зонах.
    innermost = max(reached, key=lambda zone: zone.level)

    if vehicle.mass_tons > innermost.permit_required_from_tons:
        verdict.required_permit = innermost
    else:
        verdict.notes.append(
            str(_("Разрешённая максимальная масса не превышает %(limit)s т — "
                  "пропуск не требуется")) % {"limit": innermost.permit_required_from_tons}
        )

    _check_ecological_class(vehicle, innermost, verdict)
    _check_seasonal_limit(vehicle, innermost, moment, verdict)

    return verdict


def _check_ecological_class(vehicle: Vehicle, zone: RestrictionZone,
                            verdict: Verdict) -> None:
    """Проверить соответствие экологическому классу."""
    required = zone.min_ecological_class
    if required is None:
        return

    if vehicle.ecological_class is None:
        verdict.notes.append(
            str(_("Экологический класс не указан; для въезда требуется "
                  "не ниже «Евро-%(class)s»")) % {"class": required}
        )
        return

    if vehicle.ecological_class < required:
        verdict.prohibitions.append(
            str(_("Экологический класс «Евро-%(actual)s» ниже требуемого "
                  "«Евро-%(required)s»: въезд запрещён независимо от пропуска"))
            % {"actual": vehicle.ecological_class, "required": required}
        )


def _check_seasonal_limit(vehicle: Vehicle, zone: RestrictionZone,
                          moment: date | None, verdict: Verdict) -> None:
    """Проверить сезонное ограничение по массе."""
    limit = zone.seasonal_limit_tons
    if limit is None or vehicle.mass_tons <= limit:
        return

    if moment is None:
        verdict.notes.append(
            str(_("С 1 мая по 1 октября по пятницам и в выходные дни с 06:00 "
                  "до 24:00 въезд транспорта тяжелее %(limit)s т ограничен"))
            % {"limit": limit}
        )
        return

    if is_seasonal_period(moment):
        verdict.prohibitions.append(
            str(_("Сезонное ограничение: %(date)s с 06:00 до 24:00 въезд "
                  "транспорта тяжелее %(limit)s т запрещён"))
            % {"date": moment.strftime("%d.%m.%Y"), "limit": limit}
        )
