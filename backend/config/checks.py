"""Проверки готовности контура к запуску.

Модуль подключается к системе проверок Django (``manage.py check``) и
исполняется перед стартом сервера. Задача — превратить отказы, которые иначе
проявятся первым запросом пользователя, в понятное сообщение при запуске.

Каждая проверка родилась из настоящего отказа, а не из умозрительного риска:

* ``ff.E001`` — запуск промышленного контура с ключом по умолчанию;
* ``ff.E002`` — манифест статики отсутствует, потому что ``collectstatic``
  выполнялся при включённой отладке. Отказ проявлялся не при сборке,
  а на каждой странице сразу после переключения ``FF_DEBUG`` в ``False``;
* ``ff.E003`` — пустой ``ALLOWED_HOSTS`` при выключенной отладке;
* ``ff.W001`` — каталог выгрузок недоступен для записи.

Проверки, обозначенные как ошибки, останавливают запуск: контур, который
не может обслужить ни одного запроса, должен отказываться стартовать,
а не отвечать пятисотыми.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register
from django.core.checks import Warning as CheckWarning

#: Значение ключа, пригодное только для разработки. Совпадение с ним
#: в промышленном контуре означает, что переменная окружения не задана.
INSECURE_SECRET_KEY = "dev-insecure-key-заменить-в-продуктиве"

#: Имя файла манифеста, который создаёт ManifestStaticFilesStorage.
STATIC_MANIFEST_NAME = "staticfiles.json"


def _uses_manifest_storage() -> bool:
    """Использует ли контур хранилище статики с манифестом имён."""
    backend = settings.STORAGES.get("staticfiles", {}).get("BACKEND", "")
    return "Manifest" in backend


@register("ff")
def check_secret_key(app_configs, **kwargs) -> list:
    """Ключ подписи в промышленном контуре должен быть задан явно."""
    if settings.DEBUG or settings.SECRET_KEY != INSECURE_SECRET_KEY:
        return []
    return [
        Error(
            "В промышленном контуре используется ключ подписи по умолчанию.",
            hint=(
                "Задайте переменную FF_SECRET_KEY. Сгенерировать значение: "
                'python -c "import secrets; print(secrets.token_urlsafe(50))"'
            ),
            id="ff.E001",
        )
    ]


@register("ff")
def check_static_manifest(app_configs, **kwargs) -> list:
    """Манифест статики должен существовать и содержать записи.

    Хранилище с манифестом отказывает на любом обращении к файлу, которого
    в манифесте нет, — то есть на каждой странице, если манифест не собран.
    Отказ отложенный: сборка проходит, миграции проходят, сервер стартует,
    и только первый запрос отвечает ошибкой. Проверка переносит его на старт.
    """
    if not _uses_manifest_storage():
        return []

    manifest = Path(settings.STATIC_ROOT) / STATIC_MANIFEST_NAME
    hint = (
        "Выполните сборку статики тем же контуром, в котором запускается "
        "приложение: FF_DEBUG=False python backend/manage.py collectstatic "
        "--noinput. Сборка при включённой отладке манифест не создаёт, "
        "потому что в этом режиме подключается простое хранилище."
    )

    if not manifest.exists():
        return [
            Error(
                f"Не найден манифест статики {manifest}.",
                hint=hint,
                id="ff.E002",
            )
        ]

    try:
        paths = json.loads(manifest.read_text(encoding="utf-8")).get("paths", {})
    except (ValueError, OSError) as exc:
        return [Error(f"Манифест статики повреждён: {exc}", hint=hint, id="ff.E002")]

    if not paths:
        return [Error("Манифест статики пуст.", hint=hint, id="ff.E002")]

    return []


@register("ff")
def check_allowed_hosts(app_configs, **kwargs) -> list:
    """При выключенной отладке домены обслуживания должны быть перечислены."""
    if settings.DEBUG or settings.ALLOWED_HOSTS:
        return []
    return [
        Error(
            "ALLOWED_HOSTS пуст при выключенной отладке — Django отклонит "
            "все запросы.",
            hint="Задайте FF_ALLOWED_HOSTS списком доменов через запятую.",
            id="ff.E003",
        )
    ]


@register("ff")
def check_export_root(app_configs, **kwargs) -> list:
    """Каталог выгрузок должен быть доступен для записи.

    Это предупреждение, а не ошибка: система работоспособна и без выгрузок,
    но пользователь узнал бы о проблеме только в момент формирования отчёта.
    """
    root = Path(settings.EXPORT_ROOT)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [
            CheckWarning(
                f"Каталог выгрузок {root} недоступен для записи: {exc}",
                hint="Проверьте права на каталог или задайте FF_EXPORT_ROOT.",
                id="ff.W001",
            )
        ]
    return []
