"""Представления аналитического модуля."""

from __future__ import annotations

import json

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
    """Прогноз объёма грузопотока."""
    district_id = int_param(request, "district")
    horizon = min(max(int_param(request, "horizon", 6) or 6, 3), 12)
    result = services.forecast_flow(district_id, horizon)
    context = page_context(
        request,
        title=_("Прогноз грузопотока"),
        lead=_(
            "Оценка помесячного объёма перевозок на ближайший период по модели "
            "линейного тренда с аддитивной сезонной составляющей."
        ),
        active="forecast",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Прогноз"),)],
        result=result,
        forecast_chart=_forecast_chart(result),
        districts=District.objects.all(),
        filters={"district": district_id, "horizon": horizon},
    )
    return render(request, "pages/analytics_forecast.html", context)


def _forecast_chart(result: dict) -> str:
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

    labels = [row["month"].strftime("%m.%y") for row in history]
    labels += [row["month"].strftime("%m.%y") for row in forecast]

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
