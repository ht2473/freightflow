"""Промежуточный обработчик журналирования действий пользователей."""

from __future__ import annotations

from core.context_processors import flatten_nav
from django.db.models import F
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import AuditEvent

# Методы, изменяющие состояние системы. Обращения на чтение в журнал не
# попадают: иначе он был бы забит записями о просмотре страниц.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Адреса, не подлежащие аудиту: служебные проверки и статика.
IGNORED_PREFIXES = (
    "/healthz", "/static/", "/media/", "/favicon", "/metrics",
    # Переключение языка и оформления — настройка отображения, а не
    # действие над данными; в журнале такие записи были бы шумом.
    "/i18n/",
)



#: Понятные описания действий по имени маршрута. Перечень намеренно неполный:
#: для неучтённых маршрутов выводится обобщённое описание, а не технические
#: подробности обращения.
ACTION_LABELS: dict[str, str] = {
    "accounts:profile": _("Изменение профиля"),
    "accounts:favorite_toggle": _("Изменение избранного"),
    "accounts:saved_views": _("Сохранение вида"),
    "accounts:saved_view_action": _("Действие с сохранённым видом"),
    "accounts:subscriptions": _("Оформление подписки"),
    "accounts:subscription_delete": _("Отмена подписки"),
    "accounts:notifications_read": _("Уведомления отмечены прочитанными"),
    "accounts:history_action": _("Изменение истории запросов"),
    "accounts:api_access": _("Управление токеном доступа"),
    "password_change": _("Смена пароля"),
    "content:feedback": _("Отправка обращения"),
    "console:user_action": _("Изменение учётной записи"),
    "console:content_action": _("Публикация материала"),
    "console:feedback_detail": _("Обработка обращения"),
    "console:cache_flush": _("Сброс кеша сводок"),
    "console:verification_action": _("Осмотр записи реестра"),
    "console:quarantine_action": _("Разбор карантина"),
    "console:etl_start": _("Запуск загрузки данных"),
}


class AuditMiddleware:
    """Фиксировать в журнале изменяющие операции авторизованных пользователей.

    Обработчик даёт «сеть безопасности»: даже если конкретное представление
    забыли снабдить явной записью в журнал, сам факт изменения будет
    зафиксирован с указанием адреса, метода и результата.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.method not in MUTATING_METHODS:
            return response
        if any(request.path.startswith(prefix) for prefix in IGNORED_PREFIXES):
            return response
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return response
        # Явно записанное представлением событие имеет приоритет: повторная
        # запись общего вида не нужна.
        if getattr(request, "audit_written", False):
            return response

        AuditEvent.objects.create(
            user=request.user,
            action=self._action_for(request),
            summary=self._describe(request),
            path=request.path[:300],
            ip_address=self._client_ip(request),
            request_id=getattr(request, "request_id", ""),
        )
        return response

    @staticmethod
    def _describe(request) -> str:
        """Составить понятное описание действия.

        Журнал показывается пользователю в личном кабинете, поэтому запись
        вида ``POST /account/profile/ → 302`` не годится: она сообщает о
        механике обращения, а не о том, что человек сделал. Описание
        выводится по имени маршрута — оно устойчиво к изменению адресов и
        не зависит от способа отправки формы.
        """
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return str(_("Изменение данных"))

        label = ACTION_LABELS.get(match.view_name)
        return str(label) if label else str(_("Изменение данных"))

    @staticmethod
    def _action_for(request) -> str:
        """Сопоставить HTTP-метод с типом действия в журнале."""
        if request.method == "DELETE":
            return AuditEvent.Action.DELETE
        if request.path.startswith("/console/"):
            return AuditEvent.Action.ADMIN
        return AuditEvent.Action.UPDATE

    @staticmethod
    def _client_ip(request) -> str | None:
        """Определить адрес клиента с учётом обратного прокси."""
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


#: Разделы, обращения к которым попадают в историю запросов. Перечень —
#: разделы данных: справочные и информационные страницы условий отбора
#: не имеют, и запоминать в них нечего.
TRACKED_ROUTES: frozenset[str] = frozenset({
    "core:map",
    "core:object_list",
    "core:district_list",
    "core:road_list",
    "core:incident_list",
    "core:traffic",
    "core:route_list",
    "core:flow_overview",
    "core:cargo_list",
    "core:permit_check",
    "core:zone_list",
    "core:source_list",
    "core:etl_log",
    "analytics:index",
    "analytics:sensitivity",
    "analytics:typology",
    "analytics:spatial",
    "analytics:forecast",
    "analytics:compare",
    "analytics:scenario",
    "analytics:siting",
    "analytics:corridor",
})

#: Сколько обращений хранится по каждому пользователю. История — рабочий
#: инструмент «вернуться ко вчерашней выборке», а не журнал: за пределами
#: полусотни записей ею уже не пользуются.
HISTORY_DEPTH = 50


class HistoryMiddleware:
    """Запоминать обращения пользователя к разделам данных.

    Разделы системы адресуемы целиком, поэтому истории достаточно запомнить
    маршрут и условия отбора: открытая заново, выборка соберётся на текущих
    данных. Хранится именно запрос, а не его результат.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Подписи берутся из состава меню: раздел назван в истории так же,
        # как в навигации, и второго перечня названий не заводится.
        self._titles = {
            item.route: str(item.title) for item in flatten_nav() if item.route
        }

    def __call__(self, request):
        response = self.get_response(request)

        if request.method != "GET" or response.status_code != 200:
            return response
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return response

        try:
            match = resolve(request.path_info)
        except Resolver404:
            return response
        if match.view_name not in TRACKED_ROUTES:
            return response

        self._remember(request, match.view_name)
        return response

    def _remember(self, request, route: str) -> None:
        """Обновить запись обращения и подрезать историю до глубины хранения."""
        from .models import QueryHistory

        query = request.GET.urlencode()[:500]
        entry, created = QueryHistory.objects.get_or_create(
            user=request.user,
            route=route,
            query=query,
            defaults={"title": self._titles.get(route, route)},
        )
        if not created:
            QueryHistory.objects.filter(pk=entry.pk).update(
                opened_at=timezone.now(), open_count=F("open_count") + 1
            )
            return

        stale = QueryHistory.objects.filter(user=request.user).values_list(
            "pk", flat=True
        )[HISTORY_DEPTH:]
        if stale:
            QueryHistory.objects.filter(pk__in=list(stale)).delete()
