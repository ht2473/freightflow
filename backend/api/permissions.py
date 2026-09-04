"""Разрешения REST API.

Открытые сведения — справочники, реестры и аналитика — отдаются без
авторизации: они и на страницах системы доступны любому посетителю.
Разграничение начинается там, где действие имеет цену: формирование
отчётных документов нагружает сервер и попадает в журнал от чьего-то имени.
"""

from __future__ import annotations

from accounts.models import Role, profile_for
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions


class HasRole(permissions.BasePermission):
    """Пропустить пользователя с ролью не ниже требуемой.

    Роль берётся из профиля, а способ входа значения не имеет: одно и то же
    разрешение действует и для обращения по токену, и для запроса из
    браузера с открытой сессией.
    """

    #: Наименьшая допустимая роль. Переопределяется наследником.
    minimum: str = Role.ANALYST

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            self.message = _(
                "Требуется токен доступа: выпустите его в личном кабинете, "
                "раздел «Доступ к API»."
            )
            return False
        profile = getattr(request, "user_profile", None) or profile_for(request.user)
        if profile is None or not profile.has_role(self.minimum):
            self.message = _("Метод доступен с роли «%(role)s»") % {
                "role": Role(self.minimum).label
            }
            return False
        return True


class IsAnalyst(HasRole):
    """Право пользоваться расчётными и выгрузочными методами интерфейса."""

    minimum = Role.ANALYST
