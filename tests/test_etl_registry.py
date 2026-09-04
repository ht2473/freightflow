"""Проверки реестра конвейеров, регламента и передачи загрузки на исполнение.

Реестр — единственное место, где объявлен состав источников: командная строка,
панель администратора и планировщик читают его и потому не могут разойтись.
Здесь проверяется, что реестр остаётся связным, регламент отсчитывается
от последней успешной загрузки, а способ выполнения загрузки не подменяется
молча.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.choices import EtlStatus, EtlTrigger, UpdateFrequency
from core.models import EtlRun
from django.utils import timezone
from etl import dispatch, registry, schedule


class TestRegistry:
    """Состав реестра."""

    def test_pipelines_are_declared(self):
        assert registry.names()

    def test_names_are_unique(self):
        names = registry.names()
        assert len(names) == len(set(names))

    def test_unknown_name_is_reported(self):
        with pytest.raises(KeyError, match="неизвестен"):
            registry.get("нет.такого")

    def test_every_pipeline_is_described(self):
        """Обозначение, наименование и таблица нужны панели и журналу."""
        for pipeline in registry.available():
            assert pipeline.name
            assert pipeline.title
            assert pipeline.target_table
            assert pipeline.source_code

    def test_target_tables_have_labels(self):
        """Пользователю показывается наименование раздела, а не имя таблицы."""
        for pipeline in registry.available():
            assert pipeline.target_table in EtlRun.TARGET_LABELS

    def test_districts_load_before_objects(self):
        """Объект относится к округу по координатам: порядок наборов значим."""
        names = registry.names()
        assert names.index("osm.districts") < names.index("osm.objects")

    def test_roads_load_before_frame(self):
        """Принадлежность каркасу отмечается в уже заполненном реестре."""
        names = registry.names()
        assert names.index("osm.roads") < names.index("reference.frame")

    def test_upload_pipeline_waits_for_a_file(self):
        assert registry.get("upload.flows").expects_upload


class TestSchedule:
    """Регламент отсчитывается от последней успешной загрузки."""

    @staticmethod
    def journal(pipeline, *, status=EtlStatus.SUCCESS, ago=timedelta(0)):
        return EtlRun.objects.create(
            pipeline=pipeline.name,
            target_table=pipeline.target_table,
            status=status,
            started_at=timezone.now() - ago,
        )

    def test_never_loaded_is_due(self, db):
        assert schedule.is_due(registry.get("osm.districts"))

    def test_recent_success_is_not_due(self, db):
        pipeline = registry.get("osm.districts")
        self.journal(pipeline, ago=timedelta(hours=2))
        assert not schedule.is_due(pipeline)

    def test_expired_success_is_due(self, db):
        pipeline = registry.get("osm.districts")
        self.journal(pipeline, ago=timedelta(days=8))
        assert schedule.is_due(pipeline)

    def test_failed_run_does_not_postpone(self, db):
        """Сломавшийся источник не должен считаться обновлённым."""
        pipeline = registry.get("osm.districts")
        self.journal(pipeline, status=EtlStatus.FAILED)
        assert schedule.is_due(pipeline)

    def test_pipeline_without_frequency_is_never_due(self, db):
        assert not schedule.is_due(registry.get("upload.flows"))

    def test_due_lists_only_scheduled(self, db):
        due = schedule.due()
        assert due
        assert all(item.frequency for item in due)

    def test_describe_reports_last_run(self, db):
        pipeline = registry.get("osm.districts")
        self.journal(pipeline)
        assert "последняя загрузка" in schedule.describe(pipeline)

    def test_describe_reports_absence(self, db):
        assert "не выполнялась" in schedule.describe(registry.get("osm.districts"))

    def test_every_frequency_has_an_interval(self):
        for value in UpdateFrequency.values:
            assert value in schedule.INTERVALS
            assert value in schedule.CRONTABS


class TestBeatSchedule:
    """Расписание планировщика собирается из реестра."""

    def test_scheduled_pipelines_are_present(self):
        entries = schedule.beat_schedule()
        assert entries
        assert all(item["task"] == "etl.run_pipeline" for item in entries.values())

    def test_entry_names_the_pipeline(self):
        entries = schedule.beat_schedule()
        assert entries["etl:osm.districts"]["kwargs"]["name"] == "osm.districts"

    def test_entries_are_valid_crontabs(self):
        from celery.schedules import crontab

        for item in schedule.beat_schedule().values():
            assert crontab(**item["schedule"])

    def test_unscheduled_pipeline_is_absent(self):
        assert "etl:upload.flows" not in schedule.beat_schedule()


class TestDispatch:
    """Способ выполнения загрузки виден вызывающему."""

    def test_runs_inline_without_a_queue(self, db, settings):
        settings.CELERY_BROKER_URL = ""
        submission = dispatch.submit("reference.frame", trigger=EtlTrigger.CONSOLE)

        assert submission.mode == "inline"
        assert submission.report is not None
        assert not submission.deferred

    def test_inline_run_is_journalled(self, db, settings):
        settings.CELERY_BROKER_URL = ""
        dispatch.submit("reference.frame", trigger=EtlTrigger.CONSOLE)

        entry = EtlRun.objects.get(pipeline="reference.frame")
        assert entry.trigger == EtlTrigger.CONSOLE

    def test_queue_takes_the_task(self, db, settings, monkeypatch):
        settings.CELERY_BROKER_URL = "redis://localhost:6379/2"

        sent = {}

        class Result:
            id = "0f7a"

        def delay(**kwargs):
            sent.update(kwargs)
            return Result()

        monkeypatch.setattr("etl.tasks.run_pipeline.delay", delay)
        submission = dispatch.submit("osm.districts", trigger=EtlTrigger.CONSOLE)

        assert submission.deferred
        assert submission.task_id == "0f7a"
        assert sent["name"] == "osm.districts"
        assert not EtlRun.objects.exists()

    def test_queue_state_is_readable(self, settings):
        settings.CELERY_BROKER_URL = ""
        assert not dispatch.queue_configured()
        settings.CELERY_BROKER_URL = "redis://localhost:6379/2"
        assert dispatch.queue_configured()
