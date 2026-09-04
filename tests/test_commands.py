"""Команды подготовки данных.

Метод: интеграционное тестирование. Проверяется, что команды работают
идемпотентно и приводят базу к ожидаемому состоянию при повторном запуске.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


class TestDistrictCenters:
    """Заполнение координат центров округов."""

    def test_fills_known_districts(self, db):
        """Координаты проставляются округам из справочника."""
        from core.models import District

        District.objects.create(name="Центральный", short_name="ЦАО")
        call_command("district_centers", stdout=StringIO())
        assert District.objects.first().center is not None

    def test_idempotent(self, db, districts):
        """Повторный запуск не изменяет уже заполненные координаты."""
        before = districts[0].center.wkt
        call_command("district_centers", stdout=StringIO())
        districts[0].refresh_from_db()
        assert districts[0].center.wkt == before

    def test_force_overwrites(self, db, districts):
        """Ключ --force перезаписывает координаты эталонными значениями."""
        from geo import Geometry

        districts[0].center = Geometry.point(0.0, 0.0)
        districts[0].save()
        call_command("district_centers", "--force", stdout=StringIO())
        districts[0].refresh_from_db()
        assert districts[0].center.lon == pytest.approx(37.6208)


class TestSetupRoles:
    """Создание групп разрешений."""

    def test_groups_created(self, db):
        """Для каждой роли создаётся группа."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        assert Group.objects.filter(name__startswith="role:").count() == 4

    def test_permissions_are_nested(self, db):
        """Группа старшей роли включает разрешения младших."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        viewer = set(
            Group.objects.get(name="role:viewer").permissions.values_list("id", flat=True)
        )
        admin = set(
            Group.objects.get(name="role:admin").permissions.values_list("id", flat=True)
        )
        assert viewer.issubset(admin)

    def test_idempotent(self, db):
        """Повторный запуск не создаёт дублей групп."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        call_command("setup_roles", stdout=StringIO())
        assert Group.objects.filter(name__startswith="role:").count() == 4


class TestInitDemo:
    """Демонстрационное наполнение."""

    def test_creates_users_for_each_role(self, db):
        """Создаются учётные записи всех четырёх ролей."""
        from django.contrib.auth.models import User

        call_command("init_demo", stdout=StringIO())
        for username in ("viewer", "analyst", "operator", "admin"):
            assert User.objects.filter(username=username).exists()

    def test_creates_articles(self, db):
        """Публикуются аналитические материалы."""
        from content.models import Article

        call_command("init_demo", stdout=StringIO())
        assert Article.objects.count() >= 4

    def test_creates_feedback(self, db):
        """Создаются примеры обращений в разных состояниях."""
        from content.models import FeedbackMessage

        call_command("init_demo", stdout=StringIO())
        statuses = set(FeedbackMessage.objects.values_list("status", flat=True))
        assert len(statuses) >= 2

    def test_idempotent(self, db):
        """Повторный запуск не создаёт дублирующих записей."""
        from content.models import Article
        from django.contrib.auth.models import User

        call_command("init_demo", stdout=StringIO())
        articles, users = Article.objects.count(), User.objects.count()
        call_command("init_demo", stdout=StringIO())
        assert Article.objects.count() == articles
        assert User.objects.count() == users

    def test_skip_users_option(self, db):
        """Ключ --skip-users отключает создание учётных записей."""
        from django.contrib.auth.models import User

        call_command("init_demo", "--skip-users", stdout=StringIO())
        assert not User.objects.filter(username="viewer").exists()
