"""Тесты осмотра записей реестра диспетчером.

Метод: функциональное тестирование — проверяется, что итог осмотра
сохраняется, доходит до карточки объекта и не подменяет собой сведения
источника; и что осмотр устаревает, когда источник обновляет запись.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from accounts.models import Role
from core.choices import VerificationState
from core.models import InfrastructureObject
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

QUEUE = "/console/verification/"


def action_url(obj) -> str:
    """Адрес записи итога осмотра."""
    return reverse("console:verification_action", args=[obj.pk])


class TestAccess:
    """Кому открыт раздел."""

    def test_viewer_denied(self, client, users, objects):
        """Наблюдателю раздел закрыт."""
        client.force_login(users[Role.VIEWER])
        assert client.get(QUEUE).status_code == 403

    def test_analyst_denied(self, client, users, objects):
        """Аналитику раздел закрыт: он работает с готовым реестром."""
        client.force_login(users[Role.ANALYST])
        assert client.get(QUEUE).status_code == 403

    def test_operator_allowed(self, client, users, objects):
        """Диспетчеру раздел открыт."""
        client.force_login(users[Role.OPERATOR])
        assert client.get(QUEUE).status_code == 200

    def test_action_requires_role(self, client, users, objects):
        """Записать итог осмотра без роли нельзя."""
        client.force_login(users[Role.ANALYST])
        response = client.post(action_url(objects[0]), {"decision": "confirmed"})
        assert response.status_code == 403


class TestDecision:
    """Итог осмотра."""

    @pytest.fixture
    def operator_client(self, client, users):
        client.force_login(users[Role.OPERATOR])
        return client

    def test_confirm_records_author_and_moment(self, operator_client, users, objects):
        """Подтверждение сохраняет, кто и когда осматривал запись."""
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        obj = InfrastructureObject.objects.get(pk=objects[0].pk)
        assert obj.verification == VerificationState.CONFIRMED
        assert obj.verified_by == users[Role.OPERATOR]
        assert obj.verified_at is not None

    def test_dispute_requires_note(self, operator_client, objects):
        """Отметка «требует уточнения» без пояснения не принимается."""
        operator_client.post(action_url(objects[0]), {"decision": "disputed", "note": " "})
        obj = InfrastructureObject.objects.get(pk=objects[0].pk)
        assert obj.verification == VerificationState.NONE

    def test_dispute_stores_note(self, operator_client, objects):
        """Замечание сохраняется вместе с отметкой."""
        operator_client.post(
            action_url(objects[0]),
            {"decision": "disputed", "note": "объект снесён, на месте пустырь"},
        )
        obj = InfrastructureObject.objects.get(pk=objects[0].pk)
        assert obj.verification == VerificationState.DISPUTED
        assert "пустырь" in obj.verification_note

    def test_reset_clears_marks(self, operator_client, objects):
        """Снятие отметки очищает и автора, и момент осмотра."""
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        operator_client.post(action_url(objects[0]), {"decision": "reset"})
        obj = InfrastructureObject.objects.get(pk=objects[0].pk)
        assert obj.verification == VerificationState.NONE
        assert obj.verified_at is None and obj.verified_by is None

    def test_source_values_untouched(self, operator_client, objects):
        """Осмотр не меняет сведений источника."""
        before = InfrastructureObject.objects.filter(pk=objects[0].pk).values(
            "name", "address", "capacity_tons", "area_sq_m"
        )[0]
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        after = InfrastructureObject.objects.filter(pk=objects[0].pk).values(
            "name", "address", "capacity_tons", "area_sq_m"
        )[0]
        assert before == after

    def test_decision_reaches_audit_log(self, operator_client, users, objects):
        """Итог осмотра попадает в журнал действий."""
        from accounts.models import AuditEvent

        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        assert AuditEvent.objects.filter(
            user=users[Role.OPERATOR], entity="infrastructure_object"
        ).exists()


class TestStaleness:
    """Устаревание осмотра."""

    def test_fresh_check_is_not_stale(self, objects):
        """Осмотр после последней выгрузки остаётся действительным."""
        obj = objects[0]
        obj.source_updated_at = timezone.now() - timedelta(days=1)
        obj.verified_at = timezone.now()
        assert not obj.verification_is_stale

    def test_later_load_makes_check_stale(self, objects):
        """Выгрузка, пришедшая после осмотра, делает его устаревшим."""
        obj = objects[0]
        obj.verified_at = timezone.now() - timedelta(days=1)
        obj.source_updated_at = timezone.now()
        assert obj.verification_is_stale

    def test_unchecked_record_is_not_stale(self, objects):
        """Неосмотренная запись устаревшей не считается."""
        assert not objects[0].verification_is_stale


class TestQueue:
    """Очередь осмотра."""

    @pytest.fixture
    def operator_client(self, client, users):
        client.force_login(users[Role.OPERATOR])
        return client

    def test_default_state_is_pending(self, operator_client, objects):
        """По умолчанию показаны записи, которые ещё не осматривали."""
        response = operator_client.get(QUEUE)
        assert response.context["filters"]["state"] == "pending"
        assert response.context["total_count"] == len(objects)

    def test_confirmed_leaves_queue(self, operator_client, objects):
        """Подтверждённая запись уходит из очереди."""
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        response = operator_client.get(QUEUE)
        assert response.context["total_count"] == len(objects) - 1

    def test_summary_counts_states(self, operator_client, objects):
        """Сводка считает состояния осмотра по реестру целиком."""
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        summary = operator_client.get(QUEUE).context["summary"]
        assert summary["confirmed"] == 1
        assert summary["pending"] == len(objects) - 1
        assert summary["total"] == len(objects)

    def test_stale_selection(self, operator_client, objects):
        """Отбор «осмотр устарел» находит записи, обновлённые после осмотра."""
        operator_client.post(action_url(objects[0]), {"decision": "confirmed"})
        InfrastructureObject.objects.filter(pk=objects[0].pk).update(
            source_updated_at=timezone.now() + timedelta(minutes=1)
        )
        response = operator_client.get(f"{QUEUE}?state=stale")
        assert response.context["total_count"] == 1

    def test_district_filter(self, operator_client, objects, districts):
        """Отбор по округу сужает очередь."""
        response = operator_client.get(f"{QUEUE}?district={districts[0].pk}")
        expected = sum(1 for item in objects if item.district_id == districts[0].pk)
        assert response.context["total_count"] == expected


class TestObjectCard:
    """Итог осмотра на карточке объекта."""

    def test_card_shows_confirmation(self, client, users, objects, full_dataset):
        """Карточка сообщает, что запись подтверждена."""
        client.force_login(users[Role.OPERATOR])
        client.post(action_url(objects[0]), {"decision": "confirmed"})

        page = client.get(objects[0].get_absolute_url()).content.decode()
        assert "подтверждена" in page

    def test_card_shows_dispute_note(self, client, users, objects, full_dataset):
        """Замечание осмотра виден всякому, кто открыл карточку."""
        client.force_login(users[Role.OPERATOR])
        client.post(
            action_url(objects[0]), {"decision": "disputed", "note": "склад не действует"}
        )
        client.logout()

        page = client.get(objects[0].get_absolute_url()).content.decode()
        assert "склад не действует" in page


class TestLoadKeepsCheck:
    """Загрузка данных и отметка осмотра."""

    def test_update_from_source_keeps_verification(self, users, objects, data_source,
                                                   infrastructure_types, districts):
        """Обновление записи источником не стирает итог осмотра.

        Осмотр и сведения источника живут в одной записи, но принадлежат
        разным сторонам: конвейер пишет только то, что получил из выгрузки.
        """
        from collections.abc import Iterator

        from etl.pipeline import Candidate, Context, Extract, ModelPipeline, RunReport, run

        target = objects[0]
        InfrastructureObject.objects.filter(pk=target.pk).update(
            osm_type="way", osm_id=101,
            verification=VerificationState.CONFIRMED,
            verified_at=timezone.now(), verified_by=users[Role.OPERATOR],
        )

        class ObjectsPipeline(ModelPipeline):
            """Набор из одного объекта, пришедшего из выгрузки повторно."""

            name = "test.objects"
            title = "Объекты инфраструктуры"
            target_table = "infrastructure_objects"
            source_code = "test"
            model = InfrastructureObject
            volatile_fields = ()
            checks = ()

            def ensure_source(self):
                return data_source

            def lookup(self, candidate: Candidate) -> dict:
                return {"osm_type": "way", "osm_id": int(candidate.key)}

            def extract(self, context: Context) -> Extract:
                return Extract(records=[101], count=1)

            def prepare(self, extract: Extract, context: Context,
                        report: RunReport) -> Iterator[Candidate]:
                for identifier in extract.records:
                    yield Candidate(
                        key=str(identifier),
                        position=f"way/{identifier}",
                        values={
                            "name": "Складской комплекс «Юг», корпус 2",
                            "type": infrastructure_types[0],
                            "district": districts[0],
                        },
                    )

        report = run(ObjectsPipeline())
        assert report.updated == 1

        obj = InfrastructureObject.objects.get(pk=target.pk)
        assert obj.name.endswith("корпус 2")
        assert obj.verification == VerificationState.CONFIRMED
        assert obj.verified_by == users[Role.OPERATOR]
