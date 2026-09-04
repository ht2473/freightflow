"""Информационные страницы портала.

Раздел объединяет страницы, не привязанные к отдельному реестру: оперативную
сводку на главной, методику расчёта показателей, описание программного
интерфейса, справку по работе с системой и карту сайта.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from .. import selectors
from ..choices import PeriodType
from ..context_processors import MAIN_NAV, flatten_nav
from ..models import CargoRoute, District, InfrastructureObject, RoadSegment
from .base import page_context


def home(request):
    """Главная страница — оперативная сводка по городу."""
    summary = selectors.dashboard_summary()
    profiles = selectors.district_profiles()

    # На главную выносятся четыре наиболее нагруженных округа и восемь
    # наиболее проблемных участков сети: этого достаточно для быстрой оценки
    # обстановки без перехода в детальные разделы.
    top_districts = profiles[:4]
    # Округа выстраиваются по грузопотоку, когда он измерен; иначе —
    # по складским площадям, и заголовок рейтинга говорит именно об этом.
    ranked_by_flow = any(profile["volume_tons"] for profile in profiles)
    congested = selectors.top_congested_roads(limit=6)
    incidents = selectors.recent_incidents(limit=5)
    timeseries = selectors.city_flow_series()
    # Структура ряда показывается тем разрезом, который в нём действительно
    # есть: ведомственная статистика делит перевозки по кругу перевозчиков,
    # внутригородская — по направлениям.
    by_scope = selectors.flow_by_scope()
    by_direction = [] if len(by_scope) > 1 else selectors.flow_by_direction()
    by_type = selectors.objects_by_type()

    try:
        from content.models import Article

        articles = list(Article.objects.published().select_related("category")[:3])
    except Exception:  # pragma: no cover — модуль материалов может быть отключён
        articles = []

    breakdown = by_scope if len(by_scope) > 1 else by_direction
    total_flow_volume = max((row["volume"] for row in breakdown), default=0) or 1

    context = page_context(
        request,
        title=_("Логистическая инфраструктура Москвы"),
        lead=_(
            "Единая информационная среда мониторинга складских мощностей, "
            "грузовых маршрутов и дорожной обстановки Московского "
            "транспортного узла."
        ),
        active="home",
        crumbs=[],
        summary=summary,
        top_districts=top_districts,
        ranked_by_flow=ranked_by_flow,
        congested=congested,
        incidents=incidents,
        timeseries=timeseries,
        breakdown=breakdown,
        breakdown_title=(
            _("Круг перевозчиков") if len(by_scope) > 1 else _("Направления перевозок")
        ),
        by_type=by_type[:6],
        max_type_count=max((row["count"] for row in by_type), default=0) or 1,
        articles=articles,
        profiles=profiles,
        total_flow_volume=total_flow_volume,
        # Лента состояния сети выводит участки в порядке возрастания
        # загруженности: слева спокойные, справа проблемные направления.
        ribbon_items=sorted(
            selectors.latest_conditions(), key=lambda row: row.congestion_level
        ),
        annual=bool(timeseries) and timeseries[-1]["period_type"] == PeriodType.YEAR,
        flow_chart=line_chart(
            [
                row["period"].strftime(
                    "%Y" if row["period_type"] == PeriodType.YEAR else "%m.%y"
                )
                for row in timeseries
            ],
            [row["volume"] for row in timeseries],
            title=_("Объём перевозок по городу"),
        ),
    )
    return render(request, "pages/home.html", context)


def line_chart(labels: list[str], values: list[float], title: str = "", **extra) -> str:
    """Подготовить описание линейного графика для клиентской отрисовки.

    Данные передаются в шаблон готовой строкой JSON: сценарий на странице
    только читает атрибут и строит изображение средствами SVG. Расчёты при
    этом остаются на сервере, а объём передаваемых данных минимален.
    """
    return json.dumps(
        {
            "title": title,
            "labels": labels,
            "series": [{"values": values}],
            **extra,
        },
        ensure_ascii=False,
        cls=DjangoJSONEncoder,
    )


def bar_chart(labels: list[str], values: list[float], tones: list[str] | None = None,
              title: str = "") -> str:
    """Подготовить описание столбчатой диаграммы."""
    payload = {"title": title, "labels": labels, "series": [{"values": values}]}
    if tones:
        payload["tones"] = tones
    return json.dumps(payload, ensure_ascii=False, cls=DjangoJSONEncoder)


def methodology(request):
    """Методика формирования и расчёта показателей системы."""
    # Расчётное ядро ввозится внутри представления: обратная зависимость
    # между разделами на уровне модуля замкнула бы их друг на друга.
    from analytics import metrics as analytics_metrics
    from analytics import services as analytics_services

    context = page_context(
        request,
        title=_("Методология"),
        lead=_(
            "Состав исходных данных, правила их приведения к сопоставимому "
            "виду и формулы расчёта аналитических показателей."
        ),
        active="methodology",
        crumbs=[(_("Данные"), "core:source_list"), (_("Методология"),)],
        coverage=selectors.data_coverage(),
        index_formula=analytics_services.index_formula(),
        sections=analytics_metrics.by_section(),
        metric_count=len(analytics_metrics.REGISTRY),
    )
    return render(request, "pages/methodology.html", context)


def api_docs(request):
    """Описание программного интерфейса с примерами обращения."""
    context = page_context(
        request,
        title=_("Программный интерфейс (REST API)"),
        lead=_(
            "Машиночитаемый доступ к справочникам, реестрам и аналитике "
            "системы. Спецификация публикуется в формате OpenAPI 3."
        ),
        active="api",
        crumbs=[(_("Данные"), "core:source_list"), ("REST API",)],
        endpoints=API_ENDPOINTS,
        base_url=request.build_absolute_uri("/api/v1/"),
    )
    return render(request, "pages/api_docs.html", context)


#: Перечень основных конечных точек API для справочной страницы. Полная
#: спецификация формируется автоматически и доступна по адресу /api/v1/schema/.
API_ENDPOINTS: tuple[dict, ...] = (
    {
        "path": "districts/",
        "title": _("Административные округа"),
        "description": _("Справочник округов с площадью, населением и координатами центра."),
        "params": "—",
    },
    {
        "path": "objects/",
        "title": _("Объекты инфраструктуры"),
        "description": _("Реестр складов, терминалов и распределительных центров."),
        "params": "district, type, q, page, page_size",
    },
    {
        "path": "objects/nearby/",
        "title": _("Объекты рядом с точкой"),
        "description": _("Поиск ближайших объектов по координатам и радиусу."),
        "params": "lon, lat, radius, limit",
    },
    {
        "path": "roads/",
        "title": _("Участки дорожной сети"),
        "description": _("Магистрали под мониторингом с текущей загруженностью."),
        "params": "district, class",
    },
    {
        "path": "traffic/current/",
        "title": _("Текущая обстановка"),
        "description": _("Последний замер загруженности по каждому участку сети."),
        "params": "district",
    },
    {
        "path": "incidents/",
        "title": _("Дорожные инциденты"),
        "description": _("Журнал происшествий, работ и ограничений движения."),
        "params": "type, state, severity, cargo",
    },
    {
        "path": "routes/",
        "title": _("Грузовые маршруты"),
        "description": _("Транспортные коридоры ввоза, вывоза и транзита."),
        "params": "type",
    },
    {
        "path": "flows/",
        "title": _("Статистика грузопотоков"),
        "description": _("Помесячные объёмы перевозок по округам и категориям грузов."),
        "params": "district, category, direction, period_from, period_to",
    },
    {
        "path": "analytics/load-index/",
        "title": _("Индекс логистической нагрузки"),
        "description": _("Композитная оценка нагрузки на инфраструктуру округов."),
        "params": "—",
    },
    {
        "path": "analytics/forecast/",
        "title": _("Прогноз грузопотока"),
        "description": _("Оценка объёма перевозок на ближайшие месяцы."),
        "params": "district, horizon",
    },
)


def help_page(request):
    """Справка по работе с системой."""
    context = page_context(
        request,
        title=_("Справка по системе"),
        lead=_(
            "Назначение разделов, порядок работы с реестрами и картой, "
            "возможности личного кабинета и выгрузки отчётов."
        ),
        active="help",
        crumbs=[(_("Справка"),)],
        sections=HELP_SECTIONS,
        nav=MAIN_NAV,
    )
    return render(request, "pages/help.html", context)


#: Разделы справки. Вынесены в структуру данных, чтобы шаблон оставался
#: разметкой, а содержание правилось в одном месте.
HELP_SECTIONS: tuple[dict, ...] = (
    {
        "key": "start",
        "title": _("С чего начать"),
        "items": (
            (
                _("Главная страница"),
                _("Оперативная сводка: число объектов, суммарная мощность хранения, "
                "средняя загруженность сети и открытые инциденты. Каждый показатель "
                "ведёт в соответствующий раздел."),
            ),
            (
                "Карта",
                _("Пространственный обзор. Слои включаются независимо: объекты, "
                "дорожная сеть с раскраской по загруженности, маршруты, инциденты. "
                "Инструмент «что рядом» показывает ближайшие объекты к указанной точке."),
            ),
            (
                _("Реестры"),
                _("Табличное представление с отбором и сортировкой. Любое состояние "
                "реестра адресуемо: ссылку можно сохранить или передать коллеге."),
            ),
        ),
    },
    {
        "key": "filters",
        "title": _("Отбор и сортировка"),
        "items": (
            (
                _("Наложение условий"),
                _("Условия отбора комбинируются: округ, тип объекта, источник данных, "
                "поисковый запрос. Сброс выполняется кнопкой «Очистить»."),
            ),
            (
                _("Сортировка"),
                _("Щелчок по заголовку колонки меняет порядок сортировки. Текущий "
                "порядок сохраняется при переходе между страницами списка."),
            ),
            (
                _("Сохранение вида"),
                _("Авторизованный пользователь может сохранить настроенный отбор в "
                "личном кабинете и открыть его позднее одним щелчком."),
            ),
        ),
    },
    {
        "key": "analytics",
        "title": _("Аналитические разделы"),
        "items": (
            (
                _("Индекс логистической нагрузки"),
                _("Композитная оценка округов по четырём составляющим: обеспеченность "
                "мощностями, интенсивность грузопотока, загруженность сети и "
                "аварийность. Методика приведена в разделе «Методология»."),
            ),
            (
                _("Типология округов"),
                _("Разбиение округов на однородные группы методом k-средних по "
                "нормированным показателям."),
            ),
            (
                _("Прогноз грузопотока"),
                _("Оценка объёма перевозок на ближайшие месяцы по модели тренда с "
                "сезонной составляющей."),
            ),
            (
                _("Сценарный расчёт"),
                _("Моделирование «что если»: изменение объёма перевозок или "
                "складских мощностей и оценка последствий для нагрузки на сеть."),
            ),
        ),
    },
    {
        "key": "account",
        "title": _("Личный кабинет"),
        "items": (
            (
                _("Избранное и сохранённые виды"),
                _("Закладки на объекты, округа и участки; сохранённые условия отбора "
                "с возможностью публикации по ссылке."),
            ),
            (
                _("Центр выгрузок"),
                _("Формирование отчётов в форматах XLSX, DOCX, CSV, PDF и GeoJSON. "
                "Доступно с роли «Аналитик»."),
            ),
            (
                _("Подписки и уведомления"),
                _("Оповещение о дорожных событиях выбранного округа с заданным "
                "порогом серьёзности."),
            ),
            (
                _("Токен REST API"),
                _("Выпуск и отзыв персонального токена для программного доступа."),
            ),
        ),
    },
    {
        "key": "roles",
        "title": _("Роли и права доступа"),
        "items": (
            (_("Наблюдатель"), _("Просмотр всех публичных разделов, избранное, сохранённые виды.")),
            (_("Аналитик"), _("Дополнительно — выгрузка отчётов и доступ к REST API по токену.")),
            (_("Диспетчер"), _("Дополнительно — регистрация инцидентов и правка карточек объектов.")),
            (_("Администратор"), _("Полный доступ, включая панель управления и журнал аудита.")),
        ),
    },
)


def about(request):
    """Сведения о системе, её назначении и составе данных."""
    context = page_context(
        request,
        title=_("О системе"),
        lead=_(
            "Назначение, архитектура и состав информационной системы по "
            "логистической инфраструктуре города Москвы."
        ),
        active="about",
        crumbs=[(_("О системе"),)],
        summary=selectors.dashboard_summary(),
        coverage=selectors.data_coverage(),
        db_vendor=connection.vendor,
    )
    return render(request, "pages/about.html", context)


def sitemap_page(request):
    """Карта сайта — полный перечень разделов системы."""
    context = page_context(
        request,
        title=_("Карта сайта"),
        lead=_("Полный перечень разделов и страниц информационной системы."),
        active="sitemap",
        crumbs=[(_("Карта сайта"),)],
        groups=MAIN_NAV,
        flat=flatten_nav(),
    )
    return render(request, "pages/sitemap.html", context)


@require_GET
def health(request) -> JsonResponse:
    """Проверка работоспособности для системы мониторинга.

    Возвращает состояние соединения с базой данных и объёмы ключевых таблиц.
    Используется обратным прокси и регламентными проверками доступности.
    """
    status = {"status": "ok", "version": settings.PROJECT_VERSION, "database": connection.vendor}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        status["counts"] = {
            "districts": District.objects.count(),
            "objects": InfrastructureObject.objects.count(),
            "roads": RoadSegment.objects.count(),
            "routes": CargoRoute.objects.count(),
        }
    except Exception as exc:  # pragma: no cover — аварийная ветка
        status["status"] = "error"
        status["detail"] = str(exc)
        return JsonResponse(status, status=503)
    return JsonResponse(status)
