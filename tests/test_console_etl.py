"""Проверки разделов панели администратора, управляющих загрузкой данных.

Проверяется, что панель действительно управляет конвейером, а не показывает
его описание: запуск оставляет запись в журнале с указанием начавшего,
карантин разбирается, присланный файл попадает в реестр.
"""

from __future__ import annotations

import pytest
from core.choices import EtlTrigger
from core.models import EtlReject, EtlRun, FreightFlowStat
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, users):
    client.force_login(users["admin"])
    return client


@pytest.fixture
def rejected(db):
    """Одна отклонённая запись в карантине."""
    run = EtlRun.objects.create(
        pipeline="reference.frame", target_table="road_segments", status="partial",
    )
    return EtlReject.objects.create(
        run=run, position="перечень, «Улица»", record_key="Улица",
        check_code="reference.road", message="магистраль отсутствует в реестре",
        payload='{"name": "Улица"}',
    )


class TestEtlPage:
    """Раздел загрузки данных."""

    def test_page_opens(self, admin_client):
        response = admin_client.get(reverse("console:etl"))
        assert response.status_code == 200

    def test_registry_is_shown(self, admin_client):
        response = admin_client.get(reverse("console:etl"))
        assert b"osm.districts" in response.content

    def test_journal_can_be_filtered_by_pipeline(self, admin_client):
        EtlRun.objects.create(pipeline="osm.roads", target_table="road_segments")
        EtlRun.objects.create(pipeline="osm.zones", target_table="restriction_zones")

        response = admin_client.get(reverse("console:etl"), {"pipeline": "osm.roads"})
        assert response.status_code == 200
        assert [run.pipeline for run in response.context["page_obj"]] == ["osm.roads"]


class TestStart:
    """Запуск загрузки из панели."""

    def test_run_is_journalled(self, admin_client, users, settings):
        settings.CELERY_BROKER_URL = ""
        response = admin_client.post(
            reverse("console:etl_start"), {"pipeline": "reference.frame"}
        )
        assert response.status_code == 302

        entry = EtlRun.objects.get(pipeline="reference.frame")
        assert entry.trigger == EtlTrigger.CONSOLE
        assert entry.actor == users["admin"]

    def test_unknown_pipeline_is_refused(self, admin_client):
        admin_client.post(reverse("console:etl_start"), {"pipeline": "нет.такого"})
        assert not EtlRun.objects.exists()

    def test_prune_is_refused_where_unsupported(self, admin_client, settings):
        """Закрытый справочник приведению к составу источника не подлежит."""
        settings.CELERY_BROKER_URL = ""
        admin_client.post(
            reverse("console:etl_start"),
            {"pipeline": "osm.districts", "prune": "on"},
        )
        assert not EtlRun.objects.exists()

    def test_viewer_cannot_start(self, client, users):
        client.force_login(users["viewer"])
        response = client.post(
            reverse("console:etl_start"), {"pipeline": "reference.frame"}
        )
        assert response.status_code == 403
        assert not EtlRun.objects.exists()


class TestRunCard:
    """Карточка одной загрузки."""

    def test_card_opens(self, admin_client, rejected):
        response = admin_client.get(
            reverse("console:etl_run", args=[rejected.run_id])
        )
        assert response.status_code == 200

    def test_reject_is_listed(self, admin_client, rejected):
        response = admin_client.get(
            reverse("console:etl_run", args=[rejected.run_id])
        )
        assert b"reference.road" in response.content

    def test_missing_run_is_404(self, admin_client):
        assert admin_client.get(reverse("console:etl_run", args=[9999])).status_code == 404


class TestQuarantine:
    """Разбор карантина."""

    def test_page_opens(self, admin_client, rejected):
        response = admin_client.get(reverse("console:quarantine"))
        assert response.status_code == 200
        assert response.context["open_total"] == 1

    def test_filter_by_check(self, admin_client, rejected):
        response = admin_client.get(reverse("console:quarantine"), {"check": "нет"})
        assert list(response.context["page_obj"]) == []

    def test_record_is_marked_reviewed(self, admin_client, users, rejected):
        admin_client.post(
            reverse("console:quarantine_action"),
            {"action": "review", "reject": [rejected.pk]},
        )
        rejected.refresh_from_db()
        assert rejected.is_reviewed
        assert rejected.reviewed_by == users["admin"]

    def test_reviewed_record_leaves_the_queue(self, admin_client, rejected):
        admin_client.post(
            reverse("console:quarantine_action"),
            {"action": "review", "reject": [rejected.pk]},
        )
        response = admin_client.get(reverse("console:quarantine"))
        assert response.context["open_total"] == 0

    def test_record_can_be_returned(self, admin_client, rejected):
        admin_client.post(
            reverse("console:quarantine_action"),
            {"action": "review", "reject": [rejected.pk]},
        )
        admin_client.post(
            reverse("console:quarantine_action"),
            {"action": "reopen", "reject": [rejected.pk]},
        )
        rejected.refresh_from_db()
        assert not rejected.is_reviewed

    def test_whole_check_can_be_reviewed(self, admin_client, rejected):
        admin_client.post(
            reverse("console:quarantine_action"),
            {"action": "review_check", "check": "reference.road"},
        )
        rejected.refresh_from_db()
        assert rejected.is_reviewed


class TestUpload:
    """Присылка ряда файлом."""

    @staticmethod
    def file(content: str, name: str = "ряд.csv"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")

    def test_form_opens(self, admin_client):
        response = admin_client.get(reverse("console:etl_upload"))
        assert response.status_code == 200

    def test_template_is_downloadable(self, admin_client):
        response = admin_client.get(reverse("console:etl_template"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")

    def test_rows_are_loaded(self, admin_client, users):
        response = admin_client.post(
            reverse("console:etl_upload"),
            {"file": self.file("Период;Территория;Объём, т\n2024;г. Москва;39500000\n")},
        )
        assert response.status_code == 302
        assert FreightFlowStat.objects.count() == 1

        entry = EtlRun.objects.get(pipeline="upload.flows")
        assert entry.trigger == EtlTrigger.UPLOAD
        assert entry.actor == users["admin"]

    def test_bad_rows_reach_quarantine(self, admin_client):
        admin_client.post(
            reverse("console:etl_upload"),
            {"file": self.file("Период;Объём, т\n2024;100\nпозавчера;200\n")},
        )
        assert EtlReject.objects.get().position == "строка 3"

    def test_file_of_unknown_kind_is_refused(self, admin_client):
        response = admin_client.post(
            reverse("console:etl_upload"),
            {"file": self.file("что-то", name="ряд.pdf")},
        )
        assert response.status_code == 200
        assert not FreightFlowStat.objects.exists()

    def test_file_without_required_column_is_refused(self, admin_client):
        response = admin_client.post(
            reverse("console:etl_upload"),
            {"file": self.file("Территория;Объём, т\nМосква;100\n")},
        )
        assert response.status_code == 200
        assert not FreightFlowStat.objects.exists()
