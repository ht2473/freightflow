"""Конфигурация прикладного модуля «Панель администратора»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ConsoleConfig(AppConfig):
    """Собственная панель управления системой."""

    name = "console"
    verbose_name = _("Панель администратора")
    default_auto_field = "django.db.models.AutoField"
