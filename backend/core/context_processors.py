"""Данные, доступные во всех шаблонах системы.

Структура навигации описана здесь единожды и переиспользуется шапкой сайта,
подвалом, картой сайта, «хлебными крошками» и глобальным поиском. Такой подход
исключает рассинхронизацию: добавление раздела в одном месте автоматически
отражается во всех перечисленных механизмах.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    """Пункт навигационного меню.

    Атрибуты:
        key: внутренний код раздела, совпадающий с переменной ``active``
            в контексте страницы;
        title: подпись пункта;
        route: имя маршрута Django;
        description: пояснение, выводимое в выпадающем меню и в карте сайта;
        children: вложенные пункты (для группирующих разделов).
    """

    key: str
    title: str
    route: str = ""
    description: str = ""
    children: tuple[NavItem, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        """Разрешить адрес маршрута; при отсутствии вернуть заглушку."""
        if not self.route:
            return "#"
        try:
            return reverse(self.route)
        except NoReverseMatch:  # pragma: no cover — раздел ещё не подключён
            return "#"

    @property
    def is_group(self) -> bool:
        """Пункт содержит вложенные разделы."""
        return bool(self.children)

    @property
    def keys(self) -> set[str]:
        """Собственный код и коды всех вложенных пунктов."""
        result = {self.key}
        for child in self.children:
            result |= child.keys
        return result


# ---------------------------------------------------------------------------
#  Структура главного меню
#
#  Верхний уровень содержит десять пунктов, что удовлетворяет требованию к
#  объёму навигации и одновременно остаётся обозримым: тематически близкие
#  разделы собраны в группы.
# ---------------------------------------------------------------------------

MAIN_NAV: tuple[NavItem, ...] = (
    NavItem("home", _("Главная"), "core:home", _("Оперативная сводка по городу")),
    NavItem("map", _("Карта"), "core:map", _("Интерактивная карта логистической инфраструктуры")),
    NavItem(
        "objects",
        _("Инфраструктура"),
        "core:object_list",
        _("Реестр складов, терминалов и грузовых дворов"),
        (
            NavItem(
                "objects",
                _("Реестр объектов"),
                "core:object_list",
                _("Склады, терминалы, распределительные центры"),
            ),
            NavItem(
                "districts",
                _("Округа"),
                "core:district_list",
                _("Профили административных округов Москвы"),
            ),
            NavItem(
                "types",
                _("Типы объектов"),
                "core:type_list",
                _("Классификатор объектов инфраструктуры"),
            ),
        ),
    ),
    NavItem(
        "network",
        _("Дорожная сеть"),
        "core:road_list",
        _("Магистрали, обстановка и происшествия"),
        (
            NavItem("roads", _("Участки сети"), "core:road_list", _("Магистрали под мониторингом")),
            NavItem("traffic", _("Дорожная обстановка"), "core:traffic", _("Загруженность в динамике")),
            NavItem("incidents", _("Инциденты"), "core:incident_list", _("Журнал дорожных событий")),
        ),
    ),
    NavItem(
        "freight",
        _("Грузопотоки"),
        "core:flow_overview",
        _("Объёмы перевозок и маршрутная сеть"),
        (
            NavItem("flows", _("Статистика грузопотоков"), "core:flow_overview", _("Объёмы по периодам")),
            NavItem("routes", _("Грузовые маршруты"), "core:route_list", _("Коридоры ввоза и вывоза")),
            NavItem("cargo", _("Категории грузов"), "core:cargo_list", _("Классификатор с классами ADR")),
        ),
    ),
    NavItem(
        "analytics",
        _("Аналитика"),
        "analytics:index",
        _("Расчётные показатели и прогнозы"),
        (
            NavItem(
                "index",
                _("Индекс логистической нагрузки"),
                "analytics:index",
                _("Композитная оценка округов"),
            ),
            NavItem(
                "sensitivity",
                _("Чувствительность индекса"),
                "analytics:sensitivity",
                _("Зависимость выводов от весов"),
            ),
            NavItem("typology", _("Типология округов"), "analytics:typology", _("Кластерный анализ")),
            NavItem("forecast", _("Прогноз грузопотока"), "analytics:forecast", _("Оценка на 6 месяцев")),
            NavItem("compare", _("Сравнение округов"), "analytics:compare", _("Сопоставление профилей")),
            NavItem("scenario", _("Сценарный расчёт"), "analytics:scenario", _("Моделирование «что если»")),
        ),
    ),
    NavItem(
        "data",
        _("Данные"),
        "core:source_list",
        _("Источники, качество и программный доступ"),
        (
            NavItem("sources", _("Источники данных"), "core:source_list", _("Реестр интеграций")),
            NavItem("etl", _("Журнал загрузок"), "core:etl_log", _("История обновления данных")),
            NavItem("api", "REST API", "core:api_docs", _("Программный интерфейс и примеры")),
            NavItem("methodology", _("Методология"), "core:methodology", _("Расчёт показателей")),
        ),
    ),
    NavItem("articles", _("Материалы"), "content:article_list", _("Аналитические обзоры по логистике")),
    NavItem("help", _("Справка"), "core:help", _("Руководство пользователя системы")),
    NavItem("feedback", _("Обратная связь"), "content:feedback", _("Вопросы и замечания")),
)


def project_meta(request) -> dict:
    """Реквизиты проекта, доступные любому шаблону."""
    return {
        "PROJECT_NAME": settings.PROJECT_NAME,
        "PROJECT_NAME_LATIN": settings.PROJECT_NAME_LATIN,
        "PROJECT_VERSION": settings.PROJECT_VERSION,
        "PROJECT_AUTHOR": settings.PROJECT_AUTHOR,
        "PROJECT_AUTHOR_ID": settings.PROJECT_AUTHOR_ID,
        "MAP_TILE_URL": settings.MAP_TILE_URL,
        "MAP_TILE_URL_DARK": settings.MAP_TILE_URL_DARK,
        "MAP_ATTRIBUTION": settings.MAP_ATTRIBUTION,
        "MAP_DEFAULT_CENTER": settings.MAP_DEFAULT_CENTER,
        "MAP_DEFAULT_ZOOM": settings.MAP_DEFAULT_ZOOM,
        "request_id": getattr(request, "request_id", ""),
    }


def navigation(request) -> dict:
    """Структура меню и признак активного раздела."""
    return {"MAIN_NAV": MAIN_NAV}


def flatten_nav() -> list[NavItem]:
    """Развернуть меню в плоский список — для карты сайта и поиска."""
    items: list[NavItem] = []
    for item in MAIN_NAV:
        if item.is_group:
            items.extend(item.children)
        else:
            items.append(item)
    return items
