"""Конфигурация прикладного модуля «Загрузка данных»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EtlConfig(AppConfig):
    """Импорт из внешних источников, валидация и журнал загрузок."""

    name = "etl"
    verbose_name = _("Загрузка данных")
    default_auto_field = "django.db.models.AutoField"
