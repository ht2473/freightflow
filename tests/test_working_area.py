"""Тесты рабочей области пользователя.

Метод: функциональное тестирование — проверяется, что настройка кабинета
действительно ограничивает разделы, что заданный в разделе отбор её
замещает и что урезанная выборка объявлена, а не молчалива.
"""

from __future__ import annotations

import pytest
from accounts.models import Role, profile_for
from django.urls import reverse

pytestmark = pytest.mark.django_db

SECTIONS = ["core:object_list", "core:road_list", "core:incident_list"]


@pytest.fixture
def worker(client, users, districts):
    """Пользователь, у которого рабочая область — первый округ."""
    user = users[Role.ANALYST]
    profile = profile_for(user)
    profile.default_district = districts[0]
    profile.save(update_fields=["default_district"])
    client.force_login(user)
    return user


class TestApplication:
    """Область применяется при первом открытии раздела."""

    def test_registry_limited_to_area(self, client, worker, full_dataset, districts):
        """Реестр объектов открывается округом рабочей области."""
        response = client.get(reverse("core:object_list"))
        rows = response.context["page_obj"].object_list
        assert rows
        assert all(item.district_id == districts[0].pk for item in rows)

    @pytest.mark.parametrize("route", SECTIONS)
    def test_area_reported_on_page(self, client, worker, full_dataset, route):
        """Урезанная выборка объявлена: страница называет рабочую область."""
        response = client.get(reverse(route))
        assert response.context["area_district"] is not None
        assert "рабочая область" in response.content.decode()

    def test_explicit_filter_replaces_area(self, client, worker, full_dataset, districts):
        """Отбор, заданный в разделе, рабочую область замещает."""
        response = client.get(f"{reverse('core:object_list')}?district={districts[1].pk}")
        rows = response.context["page_obj"].object_list
        assert response.context["area_district"] is None
        assert all(item.district_id == districts[1].pk for item in rows)

    def test_empty_filter_shows_whole_city(self, client, worker, full_dataset, objects):
        """Пустое значение отбора возвращает город целиком."""
        response = client.get(f"{reverse('core:object_list')}?district=")
        assert response.context["area_district"] is None
        assert response.context["total_count"] == len(objects)


class TestAbsence:
    """Область не задана или пользователь не авторизован."""

    def test_anonymous_sees_whole_city(self, client, full_dataset, objects):
        """Посетителю без учётной записи показан весь город."""
        response = client.get(reverse("core:object_list"))
        assert response.context["area_district"] is None
        assert response.context["total_count"] == len(objects)

    def test_user_without_area_sees_whole_city(self, client, users, full_dataset, objects):
        """Пользователь, не выбравший область, видит весь город."""
        client.force_login(users[Role.VIEWER])
        response = client.get(reverse("core:object_list"))
        assert response.context["area_district"] is None
        assert response.context["total_count"] == len(objects)


class TestProfile:
    """Настройка в кабинете."""

    def test_area_is_saved(self, client, users, districts):
        """Выбранная область сохраняется в профиле."""
        client.force_login(users[Role.VIEWER])
        response = client.post(
            reverse("accounts:profile"),
            {
                "first_name": "Тест",
                "last_name": "Наблюдатель",
                "email": "viewer@example.test",
                "organization": "",
                "position": "",
                "phone": "",
                "theme": "auto",
                "language": "ru",
                "default_district": districts[1].pk,
                "notify_incidents": "on",
            },
        )
        assert response.status_code in (200, 302)
        assert profile_for(users[Role.VIEWER]).default_district_id == districts[1].pk
