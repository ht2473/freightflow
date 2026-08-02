"""Конфигурация прикладного модуля «Пользователи»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    """Учётные записи, роли, личный кабинет и журнал действий."""

    name = "accounts"
    verbose_name = _("Пользователи и доступ")
    default_auto_field = "django.db.models.AutoField"

    def ready(self) -> None:
        # Импорт обработчиков сигналов: создание профиля при регистрации.
        from . import signals  # noqa: F401
