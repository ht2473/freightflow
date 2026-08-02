"""Конфигурация прикладного модуля «Контент»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ContentConfig(AppConfig):
    """Аналитические материалы, справочные страницы и обратная связь."""

    name = "content"
    verbose_name = _("Материалы и обратная связь")
    default_auto_field = "django.db.models.AutoField"
