"""Конфигурация прикладного модуля «Аналитика»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AnalyticsConfig(AppConfig):
    """Расчётные показатели: индексы, типология, прогнозирование."""

    name = "analytics"
    verbose_name = _("Аналитика")
    default_auto_field = "django.db.models.AutoField"
