"""Проверки готовности контура к запуску.

Метод: модульное тестирование, позитивные и негативные сценарии.

Проверки из ``config.checks`` существуют, чтобы отказ развёртывания проявлялся
при старте, а не первым запросом пользователя. Соответственно, тесты
подтверждают две вещи: проверка срабатывает на неисправном контуре и молчит
на исправном. Второе не менее важно — проверка, которая ругается всегда,
перестаёт нести сведения и её начинают игнорировать.
"""

from __future__ import annotations

import json

import pytest
from config import checks


class TestSecretKey:
    """Ключ подписи (ff.E001)."""

    def test_default_key_in_production_is_error(self, settings):
        """Промышленный контур с ключом по умолчанию запускаться не должен."""
        settings.DEBUG = False
        settings.SECRET_KEY = checks.INSECURE_SECRET_KEY
        messages = checks.check_secret_key(None)
        assert [m.id for m in messages] == ["ff.E001"]

    def test_default_key_in_debug_is_allowed(self, settings):
        """В отладке ключ по умолчанию — штатное положение дел."""
        settings.DEBUG = True
        settings.SECRET_KEY = checks.INSECURE_SECRET_KEY
        assert checks.check_secret_key(None) == []

    def test_explicit_key_passes(self, settings):
        """Заданный явно ключ замечаний не вызывает."""
        settings.DEBUG = False
        settings.SECRET_KEY = "явно-заданный-ключ-достаточной-длины-для-подписи"
        assert checks.check_secret_key(None) == []


class TestStaticManifest:
    """Манифест статики (ff.E002)."""

    @staticmethod
    def _use_manifest_storage(settings, root):
        settings.STATIC_ROOT = str(root)
        settings.STORAGES = {
            **settings.STORAGES,
            "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
            },
        }

    def test_missing_manifest_is_error(self, settings, tmp_path):
        """Отсутствие манифеста останавливает запуск.

        Это исходный сценарий отказа: collectstatic выполнялся при включённой
        отладке, манифест не создавался, и каждая страница отвечала ошибкой.
        """
        self._use_manifest_storage(settings, tmp_path)
        messages = checks.check_static_manifest(None)
        assert [m.id for m in messages] == ["ff.E002"]
        assert "collectstatic" in messages[0].hint

    def test_empty_manifest_is_error(self, settings, tmp_path):
        """Пустой манифест равносилен отсутствующему."""
        self._use_manifest_storage(settings, tmp_path)
        (tmp_path / checks.STATIC_MANIFEST_NAME).write_text(
            json.dumps({"paths": {}}), encoding="utf-8"
        )
        assert [m.id for m in checks.check_static_manifest(None)] == ["ff.E002"]

    def test_corrupted_manifest_is_error(self, settings, tmp_path):
        """Повреждённый манифест сообщает о себе внятно, а не трассировкой."""
        self._use_manifest_storage(settings, tmp_path)
        (tmp_path / checks.STATIC_MANIFEST_NAME).write_text("{не json", encoding="utf-8")
        assert [m.id for m in checks.check_static_manifest(None)] == ["ff.E002"]

    def test_filled_manifest_passes(self, settings, tmp_path):
        """Собранный манифест замечаний не вызывает."""
        self._use_manifest_storage(settings, tmp_path)
        (tmp_path / checks.STATIC_MANIFEST_NAME).write_text(
            json.dumps({"paths": {"css/app.css": "css/app.abc123.css"}}),
            encoding="utf-8",
        )
        assert checks.check_static_manifest(None) == []

    def test_simple_storage_is_not_checked(self, settings, tmp_path):
        """Без хранилища с манифестом проверка не применяется."""
        settings.STATIC_ROOT = str(tmp_path)
        settings.STORAGES = {
            **settings.STORAGES,
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
        assert checks.check_static_manifest(None) == []


class TestAllowedHosts:
    """Домены обслуживания (ff.E003)."""

    def test_empty_hosts_in_production_is_error(self, settings):
        """Пустой список доменов отклонил бы все запросы."""
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = []
        assert [m.id for m in checks.check_allowed_hosts(None)] == ["ff.E003"]

    def test_empty_hosts_in_debug_is_allowed(self, settings):
        """В отладке Django подставляет локальные адреса сам."""
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = []
        assert checks.check_allowed_hosts(None) == []


class TestExportRoot:
    """Каталог выгрузок (ff.W001)."""

    def test_writable_directory_passes(self, settings, tmp_path):
        """Доступный для записи каталог замечаний не вызывает."""
        settings.EXPORT_ROOT = tmp_path / "exports"
        assert checks.check_export_root(None) == []
        assert (tmp_path / "exports").is_dir()

    def test_unwritable_path_is_warning(self, settings, tmp_path):
        """Недоступный каталог — предупреждение, а не отказ запуска.

        Система работоспособна и без выгрузок; останавливать из-за этого
        весь контур было бы несоразмерно.
        """
        blocker = tmp_path / "занято"
        blocker.write_text("", encoding="utf-8")
        settings.EXPORT_ROOT = blocker / "exports"
        messages = checks.check_export_root(None)
        assert [m.id for m in messages] == ["ff.W001"]


def test_checks_are_registered():
    """Проверки подключены к системе проверок Django.

    Без регистрации модуль остался бы мёртвым кодом: именно так и было —
    settings.py ссылался на config/checks.py, которого не существовало.
    """
    from django.core.checks import registry

    registered = {
        check.__name__
        for check in registry.registry.get_checks()
        if check.__module__ == "config.checks"
    }
    assert registered == {
        "check_secret_key",
        "check_static_manifest",
        "check_allowed_hosts",
        "check_export_root",
    }


@pytest.mark.django_db
def test_production_contour_passes_all_checks(settings, tmp_path):
    """Правильно собранный промышленный контур проходит проверки целиком."""
    settings.DEBUG = False
    settings.SECRET_KEY = "ключ-промышленного-контура-достаточной-длины"
    settings.ALLOWED_HOSTS = ["freightflow.example.ru"]
    settings.STATIC_ROOT = str(tmp_path / "static")
    settings.EXPORT_ROOT = tmp_path / "exports"
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / checks.STATIC_MANIFEST_NAME).write_text(
        json.dumps({"paths": {"css/app.css": "css/app.abc123.css"}}), encoding="utf-8"
    )
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }

    messages = (
        checks.check_secret_key(None)
        + checks.check_static_manifest(None)
        + checks.check_allowed_hosts(None)
        + checks.check_export_root(None)
    )
    assert messages == []
