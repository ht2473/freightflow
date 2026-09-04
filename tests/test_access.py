"""Тесты разграничения доступа.

Метод: тестирование безопасности — проверка того, что ограничения
реализованы на уровне представлений, а не только скрытием элементов
интерфейса.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

# Разделы личного кабинета, требующие авторизации.
CABINET_ROUTES = [
    "accounts:overview", "accounts:profile", "accounts:favorites",
    "accounts:saved_views", "accounts:comparisons", "accounts:exports",
    "accounts:subscriptions", "accounts:notifications", "accounts:activity",
    "accounts:api_access",
]

# Разделы панели администратора.
CONSOLE_ROUTES = [
    "console:dashboard", "console:users", "console:references",
    "console:feedback", "console:content", "console:quality",
    "console:etl", "console:etl_upload", "console:quarantine",
    "console:audit", "console:system",
]


class TestAnonymousAccess:
    """Доступ неавторизованного посетителя."""

    @pytest.mark.parametrize("route", CABINET_ROUTES)
    def test_cabinet_requires_login(self, client, db, route):
        """Разделы кабинета перенаправляют на страницу входа."""
        response = client.get(reverse(route))
        assert response.status_code == 302
        assert "/login/" in response.url

    @pytest.mark.parametrize("route", CONSOLE_ROUTES)
    def test_console_requires_login(self, client, db, route):
        """Панель администратора недоступна без авторизации."""
        assert client.get(reverse(route)).status_code in (302, 403)

    def test_public_pages_open(self, client, full_dataset):
        """Публичные разделы доступны без авторизации."""
        assert client.get(reverse("core:object_list")).status_code == 200

    def test_export_requires_login(self, client, db):
        """Выгрузка отчётов требует авторизации."""
        response = client.post(reverse("exports:create"), {"dataset": "objects"})
        assert response.status_code in (302, 403)


class TestRolePermissions:
    """Полномочия ролей."""

    def test_viewer_reaches_cabinet(self, client, users):
        """Наблюдатель имеет доступ к личному кабинету."""
        client.force_login(users["viewer"])
        assert client.get(reverse("accounts:overview")).status_code == 200

    def test_viewer_denied_console(self, client, users):
        """Наблюдателю панель администратора закрыта."""
        client.force_login(users["viewer"])
        assert client.get(reverse("console:dashboard")).status_code == 403

    def test_analyst_denied_console(self, client, users):
        """Аналитику панель администратора также закрыта."""
        client.force_login(users["analyst"])
        assert client.get(reverse("console:dashboard")).status_code == 403

    def test_operator_denied_console(self, client, users):
        """Диспетчеру панель администратора закрыта."""
        client.force_login(users["operator"])
        assert client.get(reverse("console:dashboard")).status_code == 403

    @pytest.mark.parametrize("route", CONSOLE_ROUTES)
    def test_admin_reaches_console(self, client, users, full_dataset, route):
        """Администратор имеет доступ ко всем разделам панели."""
        client.force_login(users["admin"])
        assert client.get(reverse(route)).status_code == 200

    def test_viewer_cannot_export(self, client, users, full_dataset):
        """Наблюдателю выгрузка отчётов недоступна."""
        client.force_login(users["viewer"])
        response = client.post(
            reverse("exports:create"), {"dataset": "objects", "format": "csv"}
        )
        assert response.status_code == 403

    def test_analyst_can_export(self, client, users, full_dataset):
        """Аналитик может сформировать отчёт."""
        from accounts.models import ExportJob

        client.force_login(users["analyst"])
        client.post(reverse("exports:create"), {"dataset": "objects", "format": "csv"})
        assert ExportJob.objects.filter(status="done").exists()


class TestRoleModel:
    """Модель вложенных полномочий."""

    def test_role_hierarchy(self, users):
        """Каждая следующая роль включает права предыдущей."""
        from accounts.models import Role, profile_for

        assert profile_for(users["admin"]).has_role(Role.VIEWER)
        assert profile_for(users["admin"]).has_role(Role.OPERATOR)
        assert not profile_for(users["viewer"]).has_role(Role.ANALYST)

    def test_export_permission_by_role(self, users):
        """Право выгрузки появляется с роли «Аналитик»."""
        from accounts.models import profile_for

        assert profile_for(users["viewer"]).can_export is False
        assert profile_for(users["analyst"]).can_export is True

    def test_operate_permission_by_role(self, users):
        """Право изменения данных появляется с роли «Диспетчер»."""
        from accounts.models import profile_for

        assert profile_for(users["analyst"]).can_operate is False
        assert profile_for(users["operator"]).can_operate is True

    def test_profile_created_on_registration(self, db):
        """Профиль создаётся автоматически вместе с учётной записью."""
        from accounts.models import UserProfile
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="newbie", password="Password2026!")
        assert UserProfile.objects.filter(user=user).exists()

    def test_superuser_gets_admin_role(self, db):
        """Суперпользователь получает роль администратора."""
        from accounts.models import Role, profile_for
        from django.contrib.auth.models import User

        user = User.objects.create_superuser(
            username="root", password="Password2026!", email="root@example.test"
        )
        assert profile_for(user).role == Role.ADMIN


class TestApiToken:
    """Персональный токен доступа к программному интерфейсу."""

    def test_issue_token(self, users):
        """Выпуск токена сохраняет его в профиле."""
        from accounts.models import profile_for

        profile = profile_for(users["analyst"])
        token = profile.issue_api_token()
        assert token and profile.api_token == token

    def test_reissue_replaces_token(self, users):
        """Повторный выпуск отзывает предыдущий токен."""
        from accounts.models import profile_for

        profile = profile_for(users["analyst"])
        first = profile.issue_api_token()
        second = profile.issue_api_token()
        assert first != second

    def test_revoke_clears_token(self, users):
        """Отзыв очищает токен и отметку выпуска."""
        from accounts.models import profile_for

        profile = profile_for(users["analyst"])
        profile.issue_api_token()
        profile.revoke_api_token()
        assert profile.api_token == "" and profile.api_token_created is None


class TestAuditTrail:
    """Журнал действий пользователей."""

    def test_login_recorded(self, client, users):
        """Вход в систему фиксируется в журнале."""
        from accounts.models import AuditEvent

        client.login(username="test_analyst", password="TestPassword2026")
        assert AuditEvent.objects.filter(action="login").exists()

    def test_export_recorded(self, client, users, full_dataset):
        """Выгрузка отчёта фиксируется в журнале."""
        from accounts.models import AuditEvent

        client.force_login(users["analyst"])
        client.post(reverse("exports:create"), {"dataset": "objects", "format": "csv"})
        assert AuditEvent.objects.filter(action="export").exists()

    def test_role_change_recorded(self, client, users):
        """Изменение роли пользователя фиксируется администратором."""
        from accounts.models import AuditEvent

        client.force_login(users["admin"])
        client.post(
            reverse("console:user_action", args=[users["viewer"].pk]),
            {"action": "set_role", "role": "analyst"},
        )
        assert AuditEvent.objects.filter(action="admin", entity="user").exists()

    def test_admin_cannot_block_self(self, client, users):
        """Администратор не может заблокировать собственную запись."""
        client.force_login(users["admin"])
        client.post(
            reverse("console:user_action", args=[users["admin"].pk]), {"action": "block"}
        )
        users["admin"].refresh_from_db()
        assert users["admin"].is_active is True


class TestSavedViewSharing:
    """Публикация сохранённых видов по ссылке."""

    def test_publish_generates_token(self, users, db):
        """Публикация выпускает токен доступа."""
        from accounts.models import SavedView

        view = SavedView.objects.create(
            user=users["analyst"], title="Тест", page="core:object_list", query="district=1"
        )
        token = view.publish()
        assert token and view.is_public is True

    def test_shared_link_redirects(self, client, users, full_dataset):
        """Публичная ссылка ведёт на восстановленную страницу."""
        from accounts.models import SavedView

        view = SavedView.objects.create(
            user=users["analyst"], title="Тест", page="core:object_list",
            query="district=1&sort=capacity",
        )
        view.publish()
        response = client.get(reverse("shared_view", args=[view.share_token]))
        assert response.status_code == 302
        # Условия отбора восстанавливаются в адресе целевой страницы.
        assert response.url.startswith("/objects/?")
        assert "sort=capacity" in response.url

    def test_unpublished_view_not_shared(self, client, users, db):
        """Неопубликованный вид по ссылке недоступен."""
        from accounts.models import SavedView

        view = SavedView.objects.create(
            user=users["analyst"], title="Тест", page="core:object_list",
            share_token="secret-token-value",
        )
        assert client.get(reverse("shared_view", args=[view.share_token])).status_code == 404
