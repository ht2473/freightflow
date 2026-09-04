"""Допуск грузового транспорта: зоны ограничения и условия въезда.

Раздел отвечает на вопрос, ради которого в системе есть и нормативная база,
и геометрия колец: что требуется транспортному средству с заданными
характеристиками, чтобы законно оказаться в нужной точке города.

Расчёт выполняет :mod:`core.permits` — здесь только разбор запроса и подача.
Разделение позволяет одному и тому же заключению приходить и на страницу,
и в ответ маршрутизатора: условия проезда по маршруту определяются тем же
правилом, что и условия въезда в точку.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .. import permits
from ..models import InfrastructureObject, RestrictionZone
from .base import minimap_settings, page_context

#: Экологические классы, встречающиеся у грузового транспорта в обращении.
ECOLOGICAL_CLASSES: tuple[tuple[str, str], ...] = (
    ("", _("не установлен")),
    ("0", _("Евро-0")),
    ("1", _("Евро-1")),
    ("2", _("Евро-2")),
    ("3", _("Евро-3")),
    ("4", _("Евро-4")),
    ("5", _("Евро-5")),
    ("6", _("Евро-6")),
)

#: Разрешённая максимальная масса по умолчанию: порог, с которого начинается
#: разрешительный режим по постановлению, — на нём заключение содержательно.
DEFAULT_MASS = Decimal("3.5")


def zone_list(request):
    """Реестр зон ограничения движения грузового транспорта."""
    zones = list(
        RestrictionZone.objects.select_related("boundary_road").order_by("level")
    )
    # Число объектов реестра внутри зоны показывает, какая часть городской
    # логистики работает в разрешительном режиме. Проверка ведётся по точкам
    # объектов в памяти: полигонов три, и запрос на каждый был бы дороже.
    points = list(
        InfrastructureObject.objects.located().values_list("pk", "geom")
    )
    inside = {
        zone.pk: sum(1 for _pk, point in points if zone.contains(point.lon, point.lat))
        for zone in zones
    }

    context = page_context(
        request,
        title=_("Зоны ограничения движения"),
        lead=_(
            "Три вложенные зоны, в которых движение грузового транспорта "
            "подчинено разрешительному режиму. Границы выведены из геометрии "
            "самих колец, условия — из постановления, установившего режим."
        ),
        active="zones",
        crumbs=[(_("Допуск"), "core:permit_check"), (_("Зоны ограничения"),)],
        zones=zones,
        inside=inside,
        objects_total=len(points),
    )
    return render(request, "pages/zone_list.html", context)


def zone_detail(request, pk: int):
    """Карточка зоны: условия въезда, граница и что внутри."""
    zone = get_object_or_404(
        RestrictionZone.objects.select_related("boundary_road"), pk=pk
    )
    outer = RestrictionZone.objects.filter(level__lt=zone.level).order_by("level")
    inner = RestrictionZone.objects.filter(level__gt=zone.level).order_by("level")

    inside = [
        obj
        for obj in InfrastructureObject.objects.located().with_refs()
        if zone.contains(obj.geom.lon, obj.geom.lat)
    ]

    context = page_context(
        request,
        minimap=minimap_settings(zone.geom, zoom=10),
        title=zone.name,
        lead=zone.description or str(_("Зона ограничения движения грузового транспорта")),
        active="zones",
        crumbs=[
            (_("Допуск"), "core:permit_check"),
            (_("Зоны ограничения"), "core:zone_list"),
            (zone.short_name,),
        ],
        zone=zone,
        outer=outer,
        inner=inner,
        inside_count=len(inside),
        inside=sorted(inside, key=lambda obj: obj.name)[:25],
    )
    return render(request, "pages/zone_detail.html", context)


def permit_check(request):
    """Условия допуска транспортного средства в заданную точку.

    Точка задаётся координатой либо выбором объекта реестра — второе избавляет
    от необходимости знать координаты склада, к которому едут. День расчёта
    существен: сезонное ограничение действует не круглый год.
    """
    zones = list(RestrictionZone.objects.exclude(geom__isnull=True).order_by("level"))
    form = _read_request(request)
    verdict = None
    target = None

    if form["object_id"]:
        target = (
            InfrastructureObject.objects.located()
            .with_refs()
            .filter(pk=form["object_id"])
            .first()
        )
        if target is not None:
            form["lon"], form["lat"] = target.geom.lon, target.geom.lat

    if form["lon"] is not None and form["lat"] is not None:
        verdict = permits.evaluate(
            permits.Vehicle(
                mass_tons=form["mass"], ecological_class=form["ecological_class"]
            ),
            form["lon"],
            form["lat"],
            moment=form["moment"],
            zones=zones,
        )

    context = page_context(
        request,
        title=_("Условия допуска"),
        lead=_(
            "Какой пропуск требуется транспортному средству с заданными "
            "характеристиками, какие ограничения действуют в точке назначения "
            "и какова ответственность за их нарушение."
        ),
        active="permits",
        crumbs=[(_("Допуск"), "core:permit_check"), (_("Условия допуска"),)],
        form=form,
        verdict=verdict,
        target=target,
        zones=zones,
        seasonal_today=permits.is_seasonal_period(form["moment"]),
        ecological_classes=ECOLOGICAL_CLASSES,
        candidates=InfrastructureObject.objects.located().with_refs().order_by("name"),
        minimap=minimap_settings(target.geom, zoom=13) if target else None,
    )
    return render(request, "pages/permit_check.html", context)


def _read_request(request) -> dict:
    """Разобрать условия расчёта из строки запроса.

    Неразобранное значение заменяется умолчанием молча: страница открывается
    и без параметров, и её первое состояние ничем не отличается от состояния
    после ввода заведомо негодного числа — в обоих случаях расчёт ещё не
    выполнялся.
    """
    return {
        "mass": _decimal(request.GET.get("mass"), DEFAULT_MASS),
        "ecological_class": _integer(request.GET.get("eco")),
        "lon": _float(request.GET.get("lon")),
        "lat": _float(request.GET.get("lat")),
        "object_id": _integer(request.GET.get("object")),
        "moment": _day(request.GET.get("date")),
    }


def _decimal(raw: str | None, default: Decimal) -> Decimal:
    """Прочитать массу, приняв и десятичную запятую."""
    if not raw:
        return default
    try:
        value = Decimal(raw.strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return default
    return value if value >= 0 else default


def _float(raw: str | None) -> float | None:
    """Прочитать координату."""
    try:
        return float((raw or "").strip().replace(",", "."))
    except ValueError:
        return None


def _integer(raw: str | None) -> int | None:
    """Прочитать целое значение."""
    try:
        return int((raw or "").strip())
    except ValueError:
        return None


def _day(raw: str | None) -> date:
    """Прочитать день расчёта; по умолчанию — сегодняшний."""
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        return timezone.localdate()
