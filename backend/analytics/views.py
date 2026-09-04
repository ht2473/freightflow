"""Представления аналитического модуля."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from core import selectors
from core.choices import PeriodType
from core.models import CargoRoute, District, InfrastructureType
from core.views.base import int_param, page_context
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from . import corridors, metrics, services, siting, spatial


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
        score_note=metrics.describe("score"),
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
        notes=[metrics.describe(key) for key in ("spearman", "pearson", "entropy_weight")],
    )
    return render(request, "pages/analytics_sensitivity.html", context)


def _radius_param(request) -> float:
    """Радиус обслуживания из запроса, ограниченный набором допустимых."""
    try:
        value = float(request.GET.get("radius", spatial.DEFAULT_RADIUS_KM))
    except (TypeError, ValueError):
        return spatial.DEFAULT_RADIUS_KM
    return value if value in spatial.RADIUS_OPTIONS else spatial.DEFAULT_RADIUS_KM


def spatial_analysis(request):
    """Пространственный разрез: картограмма и обеспеченность территории."""
    metric = request.GET.get("metric", "score")
    if metric not in spatial.CHOROPLETH_METRICS:
        metric = "score"
    radius = _radius_param(request)

    values, unit = spatial.metric_values(metric)
    context = page_context(
        request,
        title=_("Пространственный анализ"),
        lead=_(
            "Распределение логистической инфраструктуры по территории: "
            "картограмма показателей округов и обеспеченность города "
            "объектами в пределах заданного радиуса."
        ),
        active="spatial",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Пространственный анализ"),)],
        chart=spatial.choropleth(values),
        metric=metric,
        metric_title=spatial.CHOROPLETH_METRICS[metric],
        metric_unit=unit,
        metrics=spatial.CHOROPLETH_METRICS,
        coverage=spatial.coverage(radius),
        notes=[metrics.describe(key) for key in ("coverage_share", "mean_distance")],
        radius=radius,
        radius_options=spatial.RADIUS_OPTIONS,
    )
    return render(request, "pages/analytics_spatial.html", context)


@require_GET
def layer_accessibility(request) -> JsonResponse:
    """Слой доступности для карты: сетка с расстоянием до ближайшего объекта."""
    return JsonResponse(spatial.accessibility_layer(_radius_param(request)))


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
        notes=[metrics.describe(key) for key in ("silhouette", "inertia")],
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
    # Модель по умолчанию выбирается сопоставлением, но её можно назвать
    # явно: сравнить прогнозы двух моделей на одном ряде — законный запрос.
    chosen = request.GET.get("model", "").strip()
    result = services.forecast_flow(territory, horizon, model_code=chosen or None)
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
            "Продолжение ряда перевозок. Модели сопоставляются на отложенной "
            "выборке — на наблюдениях, которых они при обучении не видели, — "
            "и ряд продолжает та, что ошиблась меньше прочих."
        ),
        active="forecast",
        crumbs=[(_("Аналитика"), "analytics:index"), (_("Прогноз"),)],
        result=result,
        annual=annual,
        forecast_chart=_forecast_chart(result, annual),
        territories=territories,
        territory=territory,
        horizons=horizons,
        notes=[metrics.describe(key) for key in ("mae", "rmse", "mape", "gain")],
        filters={"territory": territory, "horizon": horizon,
                 "model": result.get("model").code if result.get("available") else ""},
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


def site_selection(request):
    """Подбор площадки под требования перевозчика.

    Требования читаются из строки запроса, поэтому подобранный набор
    адресуем: ссылку можно сохранить в кабинете или передать коллеге,
    и он получит тот же расчёт на текущих данных.
    """
    requirements = siting.Requirements(
        mass_tons=_decimal_param(request, "mass", Decimal("3.5")),
        min_area_sq_m=_positive_param(request, "area"),
        district_id=int_param(request, "district"),
        type_id=int_param(request, "type"),
        max_frame_km=_positive_param(request, "frame"),
        weights=_weights(request),
    )
    result = siting.select(requirements)

    context = page_context(
        request,
        title=_("Подбор площадки"),
        lead=_(
            "Отбор площадок реестра под требования перевозчика и их "
            "сопоставление по измеренным величинам: площади, удалению "
            "от грузового каркаса и федеральных коридоров, разрешительной "
            "нагрузке и нагрузке округа."
        ),
        active="siting",
        crumbs=[(_("Инфраструктура"), "core:object_list"), (_("Подбор площадки"),)],
        result=result,
        requirements=requirements,
        criteria=siting.CRITERIA,
        districts=District.objects.all(),
        types=InfrastructureType.objects.order_by("name"),
        filters={
            "mass": requirements.mass_tons,
            "area": requirements.min_area_sq_m or "",
            "district": requirements.district_id,
            "type": requirements.type_id,
            "frame": requirements.max_frame_km or "",
        },
    )
    return render(request, "pages/siting.html", context)


def _positive_param(request, name: str) -> float | None:
    """Прочитать положительное требование; пустое значение снимает его."""
    raw = (request.GET.get(name) or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _decimal_param(request, name: str, default: Decimal) -> Decimal:
    """Прочитать десятичный параметр, приняв и запятую в дробной части."""
    raw = (request.GET.get(name) or "").strip().replace(",", ".")
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return default
    return value if value >= 0 else default


def _weights(request) -> dict[str, float]:
    """Прочитать веса составляющих сопоставления.

    Вес задаётся целым числом от нуля до трёх: ноль исключает составляющую
    из оценки, и это единственный способ сказать, что она не важна вовсе.
    Дробные веса не принимаются — обосновать их точность нечем.
    """
    weights = dict(siting.DEFAULT_WEIGHTS)
    for criterion in siting.CRITERIA:
        raw = request.GET.get(f"w_{criterion.code}")
        if raw is None:
            continue
        try:
            weights[criterion.code] = float(max(0, min(int(raw), 3)))
        except ValueError:
            continue
    return weights


def corridor_analysis(request):
    """Разбор грузового коридора: что лежит вдоль трассы."""
    routes = CargoRoute.objects.exclude(geom__isnull=True).order_by("name")
    route_id = int_param(request, "route")
    route = routes.filter(pk=route_id).first() if route_id else routes.first()
    band_km = corridors.band_option(request.GET.get("band"))
    result = corridors.analyze(route, band_km) if route else {"available": False}

    context = page_context(
        request,
        title=_("Разбор коридора"),
        lead=_(
            "Что расположено вдоль федеральной трассы в границах города: "
            "инфраструктура в полосе, округа прохождения, зоны ограничения "
            "и открытые дорожные события."
        ),
        active="corridor",
        crumbs=[(_("Грузопотоки"), "core:flow_overview"), (_("Разбор коридора"),)],
        result=result,
        route=route,
        routes=routes,
        band_km=band_km,
        band_options=corridors.BAND_OPTIONS,
    )
    return render(request, "pages/corridor.html", context)
