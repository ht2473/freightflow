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
    # Сценарные параметры ограничены разумным диапазоном: за его пределами
    # линейная модель отклика теряет содержательный смысл.
    return max(-90.0, min(value, 200.0))


def index(request):
    """Композитный индекс логистической нагрузки округов."""
    rows = services.load_index()
    context = page_context(
        request,
        title=_("Индекс логистической нагрузки"),
        lead=_(
            "Композитная оценка нагрузки на логистическую инфраструктуру округа "
            "по четырём взвешенным составляющим, приведённым к стобалльной шкале."
        ),
        active="index",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Индекс нагрузки"),)],
        rows=rows,
        summary=services.index_summary(),
        weights=services.INDEX_WEIGHTS,
        components=services.INDEX_COMPONENTS,
    )
    return render(request, "pages/analytics_index.html", context)


def typology(request):
    """Типология округов по методу k-средних."""
    k = min(max(int_param(request, "k", 4) or 4, 2), 6)
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
        k=k,
        k_options=range(2, 7),
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
    flow = _float_param(request, "flow", 0.0)
    capacity = _float_param(request, "capacity", 0.0)
    road = _float_param(request, "road", 0.0)
    result = services.scenario(flow, capacity, road)
    context = page_context(
        request,
        title=_("Сценарный расчёт"),
        lead=_(
            "Моделирование последствий изменения объёма перевозок, складских "
            "мощностей и пропускной способности дорожной сети."
        ),
        active="scenario",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Сценарный расчёт"),)],
        result=result,
        filters={"flow": flow, "capacity": capacity, "road": road},
    )
    return render(request, "pages/analytics_scenario.html", context)
