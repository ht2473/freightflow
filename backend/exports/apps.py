"""Конфигурация модуля выгрузок."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExportsConfig(AppConfig):
    """Формирование отчётов в форматах XLSX, DOCX, PDF, CSV и GeoJSON."""

    name = "exports"
    verbose_name = _("Выгрузка отчётов")
    default_auto_field = "django.db.models.AutoField"
