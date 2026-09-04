"""Тесты истории обращений к разделам данных.

Метод: функциональное тестирование — проверяется, что обращение к разделу
запоминается вместе с условиями отбора, что повторное обращение не плодит
записей и что сохранённая ссылка возвращает ту же выборку.
"""

from __future__ import annotations

import pytest
from accounts.middleware import HISTORY_DEPTH
from accounts.models import QueryHistory, Role, SavedView
from django.urls import reverse

pytestmark = pytest.mark.django_db

HISTORY = "/account/history/"


@pytest.fixture
def analyst(client, users):
    """Авторизованный аналитик."""
    client.force_login(users[Role.ANALYST])
    return users[Role.ANALYST]


class TestRecording:
    """Запись обращений."""

    def test_visit_is_remembered(self, client, analyst, full_dataset):
        """Обращение к реестру попадает в историю."""
        client.get(reverse("core:object_list"))
        entry = QueryHistory.objects.get(user=analyst)
        assert entry.route == "core:object_list"
        assert entry.title

    def test_conditions_are_kept(self, client, analyst, full_dataset, districts):
        """Условия отбора сохраняются вместе с разделом."""
        client.get(f"{reverse('core:object_list')}?district={districts[0].pk}&q=склад")
        entry = QueryHistory.objects.get(user=analyst)
        assert f"district={districts[0].pk}" in entry.query
        assert dict(entry.conditions)["округ"] == str(districts[0].pk)

    def test_repeat_updates_counter(self, client, analyst, full_dataset):
        """Повторное обращение обновляет счётчик, а не плодит записи."""
        client.get(reverse("core:object_list"))
        client.get(reverse("core:object_list"))
        entry = QueryHistory.objects.get(user=analyst)
        assert entry.open_count == 2

    def test_different_conditions_are_separate(self, client, analyst, full_dataset,
                                               districts):
        """Разные условия отбора — разные записи истории."""
        client.get(f"{reverse('core:object_list')}?district={districts[0].pk}")
        client.get(f"{reverse('core:object_list')}?district={districts[1].pk}")
        assert QueryHistory.objects.filter(user=analyst).count() == 2

    def test_untracked_section_is_ignored(self, client, analyst, db):
        """Справочные страницы условий отбора не имеют и в историю не идут."""
        client.get(reverse("core:help"))
        assert not QueryHistory.objects.exists()

    def test_anonymous_leaves_no_history(self, client, full_dataset):
        """История ведётся по учётной записи, а не по посещению."""
        client.get(reverse("core:object_list"))
        assert not QueryHistory.objects.exists()

    def test_depth_is_bounded(self, client, analyst, full_dataset):
        """История подрезается до глубины хранения."""
        for number in range(HISTORY_DEPTH + 5):
            client.get(f"{reverse('core:object_list')}?page=1&q=запрос{number}")
        assert QueryHistory.objects.filter(user=analyst).count() <= HISTORY_DEPTH

    def test_url_restores_selection(self, client, analyst, full_dataset, districts):
        """Ссылка истории возвращает ту же выборку."""
        client.get(f"{reverse('core:object_list')}?district={districts[0].pk}")
        entry = QueryHistory.objects.get(user=analyst)

        rows = client.get(entry.url).context["page_obj"].object_list
        assert all(item.district_id == districts[0].pk for item in rows)


class TestCabinet:
    """Раздел истории в кабинете."""

    def test_page_lists_entries(self, client, analyst, full_dataset):
        """Раздел показывает записанные обращения."""
        client.get(reverse("core:road_list"))
        response = client.get(HISTORY)
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count >= 1

    def test_entry_can_be_forgotten(self, client, analyst, full_dataset):
        """Отдельное обращение удаляется из истории."""
        client.get(reverse("core:object_list"))
        entry = QueryHistory.objects.get(user=analyst)
        client.post(HISTORY + "action/", {"action": "forget", "entry": entry.pk})
        assert not QueryHistory.objects.filter(pk=entry.pk).exists()

    def test_history_can_be_cleared(self, client, analyst, full_dataset):
        """История очищается целиком."""
        client.get(reverse("core:object_list"))
        client.get(reverse("core:road_list"))
        client.post(HISTORY + "action/", {"action": "clear"})
        assert not QueryHistory.objects.filter(user=analyst).exists()

    def test_entry_becomes_saved_view(self, client, analyst, full_dataset, districts):
        """Обращение сохраняется как вид со своими условиями отбора."""
        client.get(f"{reverse('core:object_list')}?district={districts[0].pk}")
        entry = QueryHistory.objects.get(user=analyst)
        client.post(HISTORY + "action/", {"action": "save", "entry": entry.pk})

        view = SavedView.objects.get(user=analyst)
        assert view.page == "core:object_list"
        assert view.query == entry.query

    def test_foreign_entry_is_not_saved(self, client, users, analyst, full_dataset):
        """Чужое обращение сохранить нельзя."""
        foreign = QueryHistory.objects.create(
            user=users[Role.VIEWER], route="core:object_list", title="Реестр"
        )
        response = client.post(HISTORY + "action/", {"action": "save", "entry": foreign.pk})
        assert response.status_code == 404
        assert not SavedView.objects.filter(user=analyst).exists()

    def test_section_requires_login(self, client, db):
        """Раздел истории доступен только своему владельцу."""
        assert client.get(HISTORY).status_code == 302
