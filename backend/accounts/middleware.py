"""Промежуточный обработчик журналирования действий пользователей."""

from __future__ import annotations

from django.urls import Resolver404, resolve
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
    "accounts:api_access": _("Управление токеном доступа"),
    "password_change": _("Смена пароля"),
    "content:feedback": _("Отправка обращения"),
    "console:user_action": _("Изменение учётной записи"),
    "console:content_action": _("Публикация материала"),
    "console:feedback_detail": _("Обработка обращения"),
    "console:cache_flush": _("Сброс кеша сводок"),
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
