"""Конфигурация прикладного модуля «Ядро»."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    """Доменные сущности, публичные страницы и карта."""

    name = "core"
    verbose_name = _("Логистическая инфраструктура")
    default_auto_field = "django.db.models.AutoField"

    def ready(self) -> None:
        """Подключить проверки готовности контура.

        Импорт выполняется здесь, а не на уровне модуля настроек: регистрация
        проверок обращается к настройкам, а те к моменту разбора settings.py
        ещё не готовы.
        """
        from config import checks  # noqa: F401 — регистрация через декораторы
