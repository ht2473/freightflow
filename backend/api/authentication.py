"""Аутентификация обращений к REST API персональным токеном.

Токен выпускается в личном кабинете и хранится в профиле пользователя
отпечатком. Отдельного хранилища ключей у системы нет: пользователь, его
роль и его токен описываются одной записью, поэтому отзыв токена, смена
роли и блокировка учётной записи действуют на программный доступ сразу.

Схема передачи повторяет общепринятую: ``Authorization: Token <значение>``.
"""

from __future__ import annotations

from accounts.models import profile_by_api_token
from django.utils.translation import gettext_lazy as _
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions

#: Слово-опознаватель схемы в заголовке.
KEYWORD = "Token"


class ProfileTokenAuthentication(authentication.BaseAuthentication):
    """Опознать пользователя по токену из его профиля."""

    def authenticate(self, request):
        """Разобрать заголовок и вернуть пару «пользователь, профиль».

        Отсутствие заголовка — не ошибка: справочники и реестры открыты,
        и запрос без токена обслуживается как анонимный. Ошибкой считается
        предъявленный, но негодный токен — умолчать об этом значило бы
        отдать клиенту урезанный ответ, оставив его в уверенности, что он
        обратился от своего имени.
        """
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if parts[0] != KEYWORD:
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed(
                _("Заголовок Authorization должен содержать схему Token и значение токена")
            )

        profile = profile_by_api_token(parts[1])
        if profile is None:
            raise exceptions.AuthenticationFailed(_("Токен недействителен или отозван"))
        if not profile.user.is_active:
            raise exceptions.AuthenticationFailed(_("Учётная запись отключена"))

        profile.note_api_use()
        # Профиль кладётся в запрос: право на операцию определяется ролью,
        # и проверяющему её разрешению не придётся идти в базу повторно.
        request.user_profile = profile
        return profile.user, profile

    def authenticate_header(self, request) -> str:
        """Схема, которую клиенту предлагается использовать при отказе."""
        return KEYWORD


class ProfileTokenScheme(OpenApiAuthenticationExtension):
    """Описание схемы доступа для спецификации OpenAPI.

    Без этого описания спецификация умалчивала бы о единственном способе
    авторизации: генератор знает о классе аутентификации, но не о том, как
    он читает заголовок, а клиентские библиотеки собираются по спецификации.
    """

    target_class = ProfileTokenAuthentication
    name = "ProfileToken"

    def get_security_definition(self, auto_schema) -> dict:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Персональный токен: «Token <значение>». Выпускается в личном "
                "кабинете пользователями с ролью «Аналитик» и выше."
            ),
        }
