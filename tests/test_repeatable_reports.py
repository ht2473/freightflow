"""Тесты повторяемых отчётов.

Метод: функциональное тестирование — проверяется, что условия отбора хранятся
вместе с заданием и что повтор собирает новый файл, а не подменяет прежний.
"""

from __future__ import annotations

import pytest
from accounts.models import ExportJob, Role
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def analyst(client, users):
    """Авторизованный аналитик."""
    client.force_login(users[Role.ANALYST])
    return users[Role.ANALYST]


class TestRepeatableReports:
    """Повтор сформированного отчёта."""

    def test_report_is_repeated_on_current_data(self, client, analyst, full_dataset,
                                                districts):
        """Повтор собирает новый файл по сохранённым условиям отбора."""
        client.post(
            reverse("exports:create"),
            {
                "dataset": "objects",
                "format": "csv",
                "filters": f"district={districts[0].pk}",
                "next": reverse("accounts:exports"),
            },
        )
        first = ExportJob.objects.get(user=analyst)

        client.post(
            reverse("exports:create"),
            {
                "dataset": first.dataset,
                "format": first.fmt,
                "filters": first.query,
                "next": reverse("accounts:exports"),
            },
        )
        jobs = ExportJob.objects.filter(user=analyst).order_by("created_at")
        assert jobs.count() == 2
        assert jobs[0].query == jobs[1].query
        assert jobs[0].file_name != jobs[1].file_name

    def test_conditions_are_shown_in_cabinet(self, client, analyst, full_dataset,
                                             districts):
        """Условия отбора отчёта видны в центре выгрузок."""
        client.post(
            reverse("exports:create"),
            {
                "dataset": "objects",
                "format": "csv",
                "filters": f"district={districts[0].pk}",
                "next": reverse("accounts:exports"),
            },
        )
        page = client.get(reverse("accounts:exports")).content.decode()
        assert f"district={districts[0].pk}" in page
