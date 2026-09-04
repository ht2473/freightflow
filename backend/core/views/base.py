"""Общие механизмы страниц: навигационная цепочка, постраничный вывод, отбор.

Модуль устраняет дублирование между двумя десятками публичных страниц:
все реестры используют одинаковый постраничный вывод, одинаковую сортировку
по выбранной колонке и одинаковую схему «хлебных крошек».
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import QuerySet
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from ..tilelayers import CLIENT_MAX_ZOOM, MIN_ZOOM


@dataclass(frozen=True)
class Crumb:
    """Звено навигационной цепочки.

    Последнее звено выводится без ссылки — оно обозначает текущую страницу.
    """

    title: str
    route: str = ""
    args: tuple = ()

    @property
    def url(self) -> str:
        """Адрес звена; пустая строка означает «без ссылки»."""
        if not self.route:
            return ""
        try:
            return reverse(self.route, args=self.args)
        except NoReverseMatch:  # pragma: no cover — защита от опечатки в маршруте
            return ""


def minimap_settings(geometry, zoom: int = 14) -> dict | None:
    """Настройки карты на карточке записи.

    Карточка показывает положение одной записи на той же подложке, что и
    карта раздела, и теми же векторными тайлами. Точку карта центрирует,
    ломаную — вписывает целиком: магистраль тянется через полгорода, и её
    середина сама по себе ни о чём не говорит.

    Числа собираются здесь и передаются через ``json_script``: подстановка
    координат прямо в разметку проходит через локализацию и при русском
    языке даёт десятичную запятую.
    """
    if geometry is None:
        return None

    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    settings_ = {
        "tilejson": reverse("core:map_tilejson"),
        "attribution": settings.MAP_ATTRIBUTION,
        "geometry": geometry.geojson,
        "center": [geometry.lon, geometry.lat],
        "zoom": zoom,
        "minZoom": MIN_ZOOM,
        "maxZoom": CLIENT_MAX_ZOOM,
        "bounds": None,
    }
    if geometry.geom_type != "POINT":
        settings_["bounds"] = [[min_lon, min_lat], [max_lon, max_lat]]
    return settings_


def breadcrumbs(*items: Crumb | tuple) -> list[Crumb]:
    """Собрать цепочку, начиная с главной страницы.

    Принимает как объекты :class:`Crumb`, так и кортежи вида
    ``("Заголовок", "маршрут", (аргументы,))`` — это делает вызов из
    представлений компактным.
    """
    chain: list[Crumb] = [Crumb(_("Главная"), "core:home")]
    for item in items:
        if isinstance(item, Crumb):
            chain.append(item)
        elif isinstance(item, (tuple, list)):
            title = item[0]
            route = item[1] if len(item) > 1 else ""
            args = tuple(item[2]) if len(item) > 2 else ()
            chain.append(Crumb(title, route, args))
        else:
            chain.append(Crumb(str(item)))
    return chain


def paginate(request, queryset: QuerySet | Sequence, per_page: int | None = None):
    """Разбить выборку на страницы, сохранив устойчивость к неверным номерам.

    Некорректный номер страницы не приводит к ошибке 404: пользователь
    получает первую или последнюю доступную страницу — так ссылки из внешних
    источников не «ломаются» при изменении объёма данных.
    """
    paginator = Paginator(queryset, per_page or settings.PAGE_SIZE)
    number = request.GET.get("page", 1)
    try:
        page = paginator.page(number)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)
    return page


def querystring(request, **overrides) -> str:
    """Сформировать строку запроса, заменив указанные параметры.

    Используется в ссылках постраничной навигации и сортировки, чтобы не
    терять уже наложенные пользователем условия отбора.
    """
    params = request.GET.copy()
    for key, value in overrides.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("page", None) if "page" not in overrides else None
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


def apply_sort(queryset: QuerySet, request, allowed: dict[str, str], default: str) -> QuerySet:
    """Применить сортировку по разрешённому набору колонок.

    Аргументы:
        allowed: соответствие кода сортировки и выражения ORM;
        default: код сортировки по умолчанию.

    Ограничение набора колонок — мера безопасности: параметр из адресной
    строки не попадает в запрос напрямую.
    """
    code = request.GET.get("sort", default)
    if code not in allowed:
        code = default
    expression = allowed[code]
    return queryset.order_by(*expression.split(","))


def int_param(request, name: str, default: int | None = None) -> int | None:
    """Прочитать целочисленный параметр запроса, игнорируя мусорные значения."""
    raw = request.GET.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def working_area(request) -> tuple[int | None, bool]:
    """Округ отбора с учётом рабочей области пользователя.

    Рабочая область — округ, с которым человек работает изо дня в день.
    Она применяется, когда параметр отбора в запросе **отсутствует**, то есть
    при первом заходе в раздел. Стоит воспользоваться формой отбора — и
    параметр появляется, пусть и пустым, а область уступает выбору:
    настройка не должна незаметно урезать выборку, которую человек только
    что задал сам.

    Возвращает пару «идентификатор округа, округ рабочей области». Второе
    значение заполнено только тогда, когда область действительно применена:
    по нему страница и сообщает, что показывает не весь город.
    """
    if "district" in request.GET:
        return int_param(request, "district"), None

    profile = None
    if getattr(request, "user", None) is not None:
        from accounts.models import profile_for

        profile = profile_for(request.user)
    if profile is None or profile.default_district is None:
        return None, None
    return profile.default_district_id, profile.default_district


def choice_param(request, name: str, allowed: Iterable[str], default: str = "") -> str:
    """Прочитать параметр, значение которого ограничено списком допустимых."""
    value = (request.GET.get(name) or "").strip()
    return value if value in set(allowed) else default


def page_context(
    request,
    *,
    title: str,
    active: str,
    crumbs: Sequence,
    lead: str = "",
    **extra: Any,
) -> dict:
    """Собрать базовый контекст страницы.

    Аргументы ``title``, ``lead`` и ``crumbs`` используются общим шаблоном
    ``partials/_page_header.html``, поэтому оформление заголовков одинаково
    во всех разделах системы.
    """
    context = {
        "page_title": title,
        "page_lead": lead,
        "active": active,
        "crumbs": breadcrumbs(*crumbs),
        "query_base": querystring(request),
    }
    context.update(extra)
    return context
