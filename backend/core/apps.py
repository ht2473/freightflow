"""Конфигурация прикладного модуля «Ядро»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    """Доменные сущности, публичные страницы и карта."""

    name = "core"
    verbose_name = _("Логистическая инфраструктура")
    default_auto_field = "django.db.models.AutoField"
