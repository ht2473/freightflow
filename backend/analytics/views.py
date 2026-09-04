"""Представления аналитического модуля."""

from __future__ import annotations

import json

from core import selectors
from core.choices import PeriodType
from core.models import District
from core.views.base import int_param, page_context
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from . import services


def _float_param(request, name: str, default: float = 0.0) -> float:
    """Прочитать вещественный параметр запроса в допустимых пределах."""
    try:
        value = float(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default
    # Пределы отвечают тому, что можно себе представить как решение:
    # вывод более чем девяти десятых мощностей или троекратный их ввод.
    return max(-90.0, min(value, 200.0))


def index(request):
    """Композитный индекс логистической нагрузки округов."""
    rows = services.load_index()
    context = page_context(
        request,
        title=_("Индекс логистической нагрузки"),
        lead=_(
            "Композитная оценка нагрузки на логистическую инфраструктуру округа "
            "по четырём измеренным составляющим, приведённым к стобалльной шкале."
        ),
        active="index",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Индекс нагрузки"),)],
        rows=rows,
        summary=services.index_summary(),
        components=services.COMPONENTS,
    )
    return render(request, "pages/analytics_index.html", context)


def sensitivity(request):
    """Устойчивость ранжирования к выбору весов индекса."""
    result = services.sensitivity()
    context = page_context(
        request,
        title=_("Чувствительность индекса"),
        lead=_(
            "Проверка того, насколько расстановка округов зависит от весов, "
            "назначенных составляющим, а не от самих данных."
        ),
        active="sensitivity",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Чувствительность"),)],
        result=result,
        components=services.COMPONENTS,
    )
    return render(request, "pages/analytics_sensitivity.html", context)


def typology(request):
    """Типология округов по методу k-средних."""
    quality = services.cluster_quality()
    # Без явного указания берётся число групп, обоснованное силуэтом:
    # круглое значение по умолчанию задавало бы разбиение произволом.
    default = quality.get("recommended") or 4
    bounds = services.CLUSTER_RANGE
    k = min(max(int_param(request, "k", default) or default, bounds.start),
            bounds.stop - 1)
    result = services.typology(k)
    context = page_context(
        request,
        title=_("Типология округов"),
        lead=_(
            "Разбиение округов на однородные группы по стандартизованным "
            "показателям нагрузки методом k-средних."
        ),
        active="typology",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Типология"),)],
        result=result,
        quality=quality,
        components=services.INDEX_COMPONENTS,
        verdict=services.silhouette_verdict(result.get("silhouette") or 0.0),
        k=k,
        k_options=bounds,
    )
    return render(request, "pages/analytics_typology.html", context)


def forecast(request):
    """Прогноз объёма перевозок по территории."""
    territories = selectors.flow_territories()
    names = [item["name"] for item in territories]
    territory = request.GET.get("territory", "").strip()
    if territory not in names:
        # Без ведомственных рядов остаётся внутригородской: он не привязан
        # к территории, и указывать её нечем.
        territory = selectors.CITY_TERRITORY if names else ""

    horizon = min(max(int_param(request, "horizon", 5) or 5, 1), 12)
    result = services.forecast_flow(territory, horizon)
    annual = result.get("granularity") == PeriodType.YEAR
    # Шаг ряда определяет и набор горизонтов, и их подписи: предлагать
    # «12 месяцев» для годового ряда бессмысленно.
    horizons = (
        [(1, _("1 год")), (2, _("2 года")), (3, _("3 года")), (5, _("5 лет")),
         (7, _("7 лет"))]
        if annual
        else [(3, _("3 месяца")), (6, _("6 месяцев")), (9, _("9 месяцев")),
              (12, _("12 месяцев"))]
    )

    context = page_context(
        request,
        title=_("Прогноз перевозок"),
        lead=_(
            "Продолжение ряда перевозок по модели линейного тренда. Качество "
            "измеряется на отложенной выборке — на наблюдениях, которых "
            "модель при обучении не видела."
        ),
        active="forecast",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Прогноз"),)],
        result=result,
        annual=annual,
        forecast_chart=_forecast_chart(result, annual),
        territories=territories,
        territory=territory,
        horizons=horizons,
        filters={"territory": territory, "horizon": horizon},
    )
    return render(request, "pages/analytics_forecast.html", context)


def _forecast_chart(result: dict, annual: bool = False) -> str:
    """Подготовить описание графика «факт и прогноз».

    Ряды располагаются на общей шкале времени: фактические наблюдения
    занимают начало, прогнозные значения — продолжение. Пропуски в рядах
    (значение ``null``) обеспечивают разрыв линии на стыке, благодаря чему
    прогнозная часть визуально отделена от фактической.
    """
    if not result.get("available"):
        return json.dumps({"labels": [], "series": []}, cls=DjangoJSONEncoder)

    history = result["history"]
    forecast = result["forecast"]

    fmt = "%Y" if annual else "%m.%y"
    labels = [row["period"].strftime(fmt) for row in history]
    labels += [row["period"].strftime(fmt) for row in forecast]

    fact = [row["volume"] for row in history] + [None] * len(forecast)
    # Прогнозная линия начинается с последнего фактического значения —
    # иначе на графике возник бы визуальный разрыв.
    predicted = [None] * (len(history) - 1)
    predicted.append(history[-1]["volume"] if history else None)
    predicted += [row["value"] for row in forecast]

    return json.dumps(
        {
            "title": _("Факт и прогноз объёма перевозок"),
            "labels": labels,
            "series": [
                {"values": fact},
                {"values": predicted, "forecast": True},
            ],
        },
        ensure_ascii=False,
        cls=DjangoJSONEncoder,
    )


def compare(request):
    """Сопоставление профилей округов."""
    raw = request.GET.getlist("district")
    ids = [int(value) for value in raw if value.isdigit()]
    if not ids:
        # По умолчанию сравниваются три округа с наибольшей нагрузкой —
        # страница не должна открываться пустой.
        ids = [row["district"].id for row in services.load_index()[:3]]
    result = services.compare_districts(ids)
    context = page_context(
        request,
        title=_("Сравнение округов"),
        lead=_("Сопоставление округов по составляющим индекса логистической нагрузки."),
        active="compare",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Сравнение"),)],
        result=result,
        districts=District.objects.all(),
        selected=ids,
    )
    return render(request, "pages/analytics_compare.html", context)


def scenario(request):
    """Сценарный расчёт «что если»."""
    changes = {
        key: _float_param(request, key, 0.0) for key in services.SCENARIO_LEVERS
    }
    district_id = int_param(request, "district")
    result = services.scenario(district_id, **changes)
    context = page_context(
        request,
        title=_("Сценарный расчёт"),
        lead=_(
            "Пересчёт индекса нагрузки при изменении условий в выбранном "
            "округе: складских площадей, магистральной сети, числа работ."
        ),
        active="scenario",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Сценарный расчёт"),)],
        result=result,
        districts=District.objects.all(),
        selected=result["district"].id if result.get("available") else None,
        levers=[
            {"key": key, "title": title, "value": changes[key],
             "hint": services.SCENARIO_HINTS[key]}
            for key, title in services.SCENARIO_LEVERS.items()
        ],
        filters=changes,
    )
    return render(request, "pages/analytics_scenario.html", context)
