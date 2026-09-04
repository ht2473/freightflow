"""Тесты порождения уведомлений событиями системы.

Метод: функциональное тестирование — проверяется, что уведомление появляется
как след произошедшего и что условия подписки действительно отбирают события,
а не только сохраняются в базе.
"""

from __future__ import annotations

import pytest
from accounts import notify
from accounts.models import IncidentSubscription, Notification, Role, UserProfile
from core.choices import DataOrigin, IncidentType
from core.models import TrafficIncident
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscriber(users):
    """Подписка наблюдателя на серьёзные события по всему городу."""
    return IncidentSubscription.objects.create(
        user=users[Role.VIEWER], district=None, min_severity=3, cargo_only=True
    )


def make_incident(districts, data_source, **kwargs):
    """Создать дорожное событие с заданными свойствами."""
    values = {
        "reported_at": timezone.now(),
        "incident_type": IncidentType.ROADWORKS,
        "severity": 4,
        "district": districts[0],
        "affects_cargo": True,
        "origin": DataOrigin.MEASURED,
        "source": data_source,
    }
    values.update(kwargs)
    return TrafficIncident.objects.create(**values)


class TestSubscriptionMatching:
    """Отбор событий условиями подписки."""

    def test_severity_below_threshold_ignored(self, subscriber, districts, data_source):
        """Событие ниже порога серьёзности подписку не затрагивает."""
        assert not subscriber.matches(make_incident(districts, data_source, severity=2))

    def test_cargo_condition_applied(self, subscriber, districts, data_source):
        """Подписка на грузовое движение пропускает прочие события."""
        assert not subscriber.matches(
            make_incident(districts, data_source, affects_cargo=False)
        )

    def test_district_condition_applied(self, users, districts, data_source):
        """Подписка на округ отбирает события только этого округа."""
        subscription = IncidentSubscription.objects.create(
            user=users[Role.VIEWER], district=districts[0], min_severity=1, cargo_only=False
        )
        assert subscription.matches(make_incident(districts, data_source))
        assert not subscription.matches(
            make_incident(districts, data_source, district=districts[1])
        )

    def test_district_taken_from_road_without_coordinate(
        self, users, districts, data_source, roads
    ):
        """Событие без координаты относится к округу через участок сети."""
        subscription = IncidentSubscription.objects.create(
            user=users[Role.VIEWER],
            district=roads[0].district,
            min_severity=1,
            cargo_only=False,
        )
        incident = make_incident(
            districts, data_source, district=None, road=roads[0], geom=None
        )
        assert subscription.matches(incident)

    def test_inactive_subscription_matches_nothing(self, subscriber, districts, data_source):
        """Отключённая подписка событий не отбирает."""
        subscriber.is_active = False
        assert not subscriber.matches(make_incident(districts, data_source))

    def test_url_repeats_conditions(self, users, districts):
        """Ссылка подписки открывает перечень с теми же условиями."""
        subscription = IncidentSubscription.objects.create(
            user=users[Role.VIEWER], district=districts[0], min_severity=3, cargo_only=True
        )
        assert f"district={districts[0].pk}" in subscription.url
        assert "severity=3" in subscription.url
        assert "cargo=1" in subscription.url


class TestDelivery:
    """Доставка уведомлений."""

    def test_matching_event_reaches_subscriber(self, subscriber, districts, data_source):
        """Подходящее событие порождает уведомление подписчику."""
        notify.incidents_loaded([make_incident(districts, data_source)])
        assert Notification.objects.filter(user=subscriber.user).count() == 1

    def test_unmatching_event_delivers_nothing(self, subscriber, districts, data_source):
        """Событие вне условий подписки уведомления не порождает."""
        notify.incidents_loaded([make_incident(districts, data_source, severity=1)])
        assert not Notification.objects.filter(user=subscriber.user).exists()

    def test_batch_collapses_into_one_message(self, subscriber, districts, data_source):
        """Загрузка со множеством событий даёт одно сводное уведомление."""
        events = [make_incident(districts, data_source) for _ in range(5)]
        notify.incidents_loaded(events)
        item = Notification.objects.get(user=subscriber.user)
        assert "5" in item.title
        assert item.url == subscriber.url

    def test_single_event_links_to_its_card(self, subscriber, districts, data_source):
        """Одиночное событие открывается собственной карточкой."""
        incident = make_incident(districts, data_source)
        notify.incidents_loaded([incident])
        item = Notification.objects.get(user=subscriber.user)
        assert item.url == incident.get_absolute_url()

    def test_level_follows_severity(self, subscriber, districts, data_source):
        """Уровень уведомления определяется самым серьёзным событием."""
        notify.incidents_loaded(
            [
                make_incident(districts, data_source, severity=3),
                make_incident(districts, data_source, severity=5),
            ]
        )
        item = Notification.objects.get(user=subscriber.user)
        assert item.level == Notification.Level.ALERT

    def test_quarantine_reaches_operators(self, users):
        """О карантине узнают те, кто с ним работает."""
        from etl.pipeline import RunReport

        report = RunReport(pipeline="p", title="Набор", target_table="t", rejected=3)
        report.by_check["required"] = 3
        notify.quarantined(report, source_title="Набор")

        recipients = set(Notification.objects.values_list("user__username", flat=True))
        assert recipients == {users[Role.OPERATOR].username, users[Role.ADMIN].username}

    def test_failed_load_is_announced(self, users):
        """Отказ загрузки доходит до ответственных с причиной."""
        from etl.pipeline import RunReport

        report = RunReport(pipeline="p", title="Набор", target_table="t")
        report.note("источник недоступен")
        notify.load_failed(report, source_title="Набор")

        item = Notification.objects.filter(user=users[Role.OPERATOR]).get()
        assert item.level == Notification.Level.ALERT
        assert "источник недоступен" in item.body

    def test_role_change_reaches_user(self, users):
        """Смена роли сообщается тому, чьи полномочия изменились."""
        notify.role_changed(users[Role.VIEWER], role=Role.ANALYST, actor=users[Role.ADMIN])
        assert Notification.objects.filter(user=users[Role.VIEWER]).exists()

    def test_empty_batch_delivers_nothing(self, subscriber):
        """Загрузка, не принёсшая новых событий, никого не оповещает."""
        assert notify.incidents_loaded([]) == 0
        assert not Notification.objects.exists()


class TestCabinet:
    """Работа с уведомлениями в кабинете."""

    def test_open_marks_read_and_redirects(self, client, subscriber, districts, data_source):
        """Переход по уведомлению отмечает его прочитанным."""
        incident = make_incident(districts, data_source)
        notify.incidents_loaded([incident])
        item = Notification.objects.get(user=subscriber.user)

        client.force_login(subscriber.user)
        response = client.get(f"/account/notifications/{item.pk}/open/")
        assert response.status_code == 302
        assert response.url == incident.get_absolute_url()
        item.refresh_from_db()
        assert item.is_read

    def test_foreign_notification_hidden(
        self, client, users, subscriber, districts, data_source
    ):
        """Чужое уведомление недоступно."""
        notify.incidents_loaded([make_incident(districts, data_source)])
        item = Notification.objects.get(user=subscriber.user)

        client.force_login(users[Role.ADMIN])
        assert client.get(f"/account/notifications/{item.pk}/open/").status_code == 404


class TestRoleChangeThroughConsole:
    """Смена роли в панели администратора."""

    def test_console_notifies_user(self, client, users):
        """Изменение роли в панели порождает уведомление её владельцу."""
        client.force_login(users[Role.ADMIN])
        client.post(
            f"/console/users/{users[Role.VIEWER].pk}/action/",
            {"action": "set_role", "role": Role.ANALYST},
        )
        assert UserProfile.objects.get(user=users[Role.VIEWER]).role == Role.ANALYST
        assert Notification.objects.filter(user=users[Role.VIEWER]).exists()


class TestConditionsAgreement:
    """Условия подписки одинаково читаются базой и памятью."""

    @pytest.mark.parametrize("district_bound", [False, True])
    @pytest.mark.parametrize("cargo_only", [False, True])
    @pytest.mark.parametrize("threshold", [1, 3, 5])
    def test_selection_matches(self, users, districts, data_source, roads,
                               district_bound, cargo_only, threshold):
        """Выборка из базы и перебор в памяти отбирают одни и те же события."""
        population = [
            make_incident(districts, data_source, severity=1, affects_cargo=False),
            make_incident(districts, data_source, severity=3),
            make_incident(districts, data_source, severity=5, district=districts[1]),
            make_incident(
                districts, data_source, severity=4, district=None, road=roads[0], geom=None
            ),
        ]
        subscription = IncidentSubscription.objects.create(
            user=users[Role.VIEWER],
            district=districts[0] if district_bound else None,
            min_severity=threshold,
            cargo_only=cargo_only,
        )

        from_database = set(subscription.matching_incidents().values_list("pk", flat=True))
        in_memory = {item.pk for item in population if subscription.matches(item)}
        assert from_database == in_memory


class TestSubscriptionsPage:
    """Раздел подписок кабинета."""

    def test_page_shows_current_count(self, client, subscriber, districts, data_source):
        """Рядом с подпиской показано число подпадающих под неё событий."""
        make_incident(districts, data_source)
        client.force_login(subscriber.user)
        response = client.get("/account/subscriptions/")
        assert response.status_code == 200
        assert response.context["items"][0]["open_count"] == 1
