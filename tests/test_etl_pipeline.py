"""Проверки ядра подсистемы загрузки.

Конвейер отвечает за то, что одинаково для всех источников: порядок этапов,
проверки качества, карантин, различение созданных и неизменившихся записей,
журнал. Здесь он проверяется на простом наборе, чтобы поведение самого
конвейера не смешивалось с особенностями конкретного источника.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from core.choices import EtlStatus, EtlTrigger, SourceType
from core.models import CargoCategory, DataSource, EtlReject, EtlRun
from django.utils import timezone
from etl.pipeline import (
    Candidate,
    Context,
    Extract,
    ModelPipeline,
    Outcome,
    PipelineError,
    RunReport,
    run,
    same_value,
)
from etl.quality import condition, fits, not_negative, one_of, required, within


class CategoriesPipeline(ModelPipeline):
    """Учебный набор: справочник категорий грузов из списка словарей."""

    name = "test.categories"
    title = "Категории грузов"
    target_table = "cargo_categories"
    source_code = "test"
    model = CargoCategory
    supports_prune = True
    volatile_fields = ()
    checks = (
        required("name", "Наименование"),
        within("hazard_class", 0, 9, "Класс опасности"),
    )

    def __init__(self, records=None, fail=False):
        self.records = records or []
        self.fail = fail

    def ensure_source(self) -> DataSource:
        source, _ = DataSource.objects.update_or_create(
            code="test",
            defaults={"name": "Учебный источник", "source_type": SourceType.MANUAL},
        )
        return source

    def extract(self, context: Context) -> Extract:
        if self.fail:
            raise RuntimeError("источник недоступен")
        return Extract(records=self.records, count=len(self.records))

    def lookup(self, candidate: Candidate) -> dict:
        return {"code": candidate.key}

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        for index, item in enumerate(extract.records, start=1):
            if item.get("skip"):
                report.skip("вне предметной области")
                continue
            yield Candidate(
                key=item["code"],
                position=f"строка {index}",
                values={
                    "name": item.get("name", ""),
                    "hazard_class": item.get("hazard_class", 0),
                },
                payload=item,
            )

    def prune(self, seen: set[str], context: Context) -> int:
        doomed = CargoCategory.objects.exclude(code__in=seen)
        removed = doomed.count()
        doomed.delete()
        return removed


@pytest.fixture
def records():
    return [
        {"code": "food", "name": "Продовольствие", "hazard_class": 0},
        {"code": "fuel", "name": "ГСМ и топливо", "hazard_class": 3},
    ]


class TestOutcomes:
    """Различение созданных, обновлённых и неизменившихся записей."""

    def test_first_run_creates(self, db, records):
        report = run(CategoriesPipeline(records))
        assert (report.created, report.updated, report.unchanged) == (2, 0, 0)
        assert CargoCategory.objects.count() == 2

    def test_repeat_run_changes_nothing(self, db, records):
        run(CategoriesPipeline(records))
        report = run(CategoriesPipeline(records))
        assert (report.created, report.updated, report.unchanged) == (0, 0, 2)
        assert report.written == 0

    def test_changed_record_is_updated(self, db, records):
        run(CategoriesPipeline(records))
        records[1]["hazard_class"] = 4
        report = run(CategoriesPipeline(records))
        assert (report.created, report.updated, report.unchanged) == (0, 1, 1)
        assert CargoCategory.objects.get(code="fuel").hazard_class == 4

    def test_repeat_run_does_not_duplicate(self, db, records):
        for _ in range(3):
            run(CategoriesPipeline(records))
        assert CargoCategory.objects.count() == 2


class TestQuarantine:
    """Записи, не прошедшие проверку, откладываются с причиной."""

    def test_record_without_name_is_rejected(self, db, records):
        records.append({"code": "empty", "name": ""})
        report = run(CategoriesPipeline(records))

        assert report.rejected == 1
        assert report.created == 2
        assert not CargoCategory.objects.filter(code="empty").exists()

    def test_reject_is_stored_with_reason(self, db, records):
        records.append({"code": "empty", "name": ""})
        report = run(CategoriesPipeline(records))

        reject = EtlReject.objects.get()
        assert reject.check_code == "required.name"
        assert "Наименование" in reject.message
        assert reject.record_key == "empty"
        assert reject.position == "строка 3"
        assert reject.run_id == report.run_id

    def test_payload_keeps_source_record(self, db, records):
        records.append({"code": "bad", "name": "Груз", "hazard_class": 42})
        run(CategoriesPipeline(records))

        reject = EtlReject.objects.get()
        assert reject.check_code == "range.hazard_class"
        assert json.loads(reject.payload)["hazard_class"] == 42

    def test_repeat_rejection_does_not_lengthen_the_queue(self, db, records):
        """Неисправленный источник не должен множить одну и ту же запись."""
        records.append({"code": "empty", "name": ""})
        run(CategoriesPipeline(records))
        run(CategoriesPipeline(records))

        assert EtlReject.objects.count() == 1
        assert EtlRun.objects.order_by("-id").first().records_errors == 1

    def test_reviewed_problem_returns_to_the_queue(self, db, records):
        """Отметка «разобрано» не скрывает сохраняющийся дефект источника."""
        records.append({"code": "empty", "name": ""})
        run(CategoriesPipeline(records))
        EtlReject.objects.update(reviewed_at=timezone.now())
        run(CategoriesPipeline(records))

        assert EtlReject.objects.filter(reviewed_at__isnull=True).count() == 1

    def test_quarantine_is_bounded(self, db, settings):
        """Неисправный источник не должен раздувать карантин без предела."""
        settings.ETL_QUARANTINE_LIMIT = 5
        broken = [{"code": f"c{i}", "name": ""} for i in range(20)]
        report = run(CategoriesPipeline(broken))

        assert report.rejected == 20
        assert EtlReject.objects.count() == 5


class TestFiltering:
    """Отбор и проверка качества считаются раздельно."""

    def test_filtered_records_are_not_rejects(self, db, records):
        records.append({"code": "x", "skip": True})
        report = run(CategoriesPipeline(records))

        assert report.filtered == 1
        assert report.rejected == 0
        assert EtlReject.objects.count() == 0
        assert report.by_rule["вне предметной области"] == 1

    def test_filtered_records_are_not_errors_in_journal(self, db, records):
        records.append({"code": "x", "skip": True})
        run(CategoriesPipeline(records))
        assert EtlRun.objects.get().records_errors == 0


class TestJournal:
    """Журнал наполняется настоящими величинами."""

    def test_counters_are_written(self, db, records):
        run(CategoriesPipeline(records))
        entry = EtlRun.objects.get()

        assert entry.records_created == 2
        assert entry.records_loaded == 2
        assert entry.records_unchanged == 0
        assert entry.status == EtlStatus.SUCCESS
        assert entry.finished_at is not None

    def test_unchanged_run_is_successful(self, db, records):
        run(CategoriesPipeline(records))
        run(CategoriesPipeline(records))
        latest = EtlRun.objects.order_by("-id").first()

        assert latest.records_unchanged == 2
        assert latest.records_loaded == 0
        assert latest.status == EtlStatus.SUCCESS

    def test_rejects_mark_run_as_partial(self, db, records):
        records.append({"code": "empty", "name": ""})
        run(CategoriesPipeline(records))
        assert EtlRun.objects.get().status == EtlStatus.PARTIAL

    def test_trigger_and_actor_are_recorded(self, db, records, django_user_model):
        user = django_user_model.objects.create_user("operator", password="x")
        run(CategoriesPipeline(records),
            Context(trigger=EtlTrigger.CONSOLE, actor=user))
        entry = EtlRun.objects.get()

        assert entry.trigger == EtlTrigger.CONSOLE
        assert entry.actor == user

    def test_failure_is_journalled(self, db):
        with pytest.raises(PipelineError):
            run(CategoriesPipeline(fail=True))

        entry = EtlRun.objects.get()
        assert entry.status == EtlStatus.FAILED
        assert "источник недоступен" in entry.error_message

    def test_source_is_linked(self, db, records):
        run(CategoriesPipeline(records))
        assert EtlRun.objects.get().source.code == "test"


class TestDryRun:
    """Проверочный проход показывает итог, ничего не записывая."""

    def test_nothing_is_written(self, db, records):
        report = run(CategoriesPipeline(records), Context(dry_run=True))

        assert report.created == 2
        assert CargoCategory.objects.count() == 0

    def test_quarantine_stays_empty(self, db, records):
        records.append({"code": "empty", "name": ""})
        run(CategoriesPipeline(records), Context(dry_run=True))
        assert EtlReject.objects.count() == 0


class TestPrune:
    """Приведение реестра к составу источника."""

    def test_absent_records_are_removed(self, db, records):
        run(CategoriesPipeline(records))
        report = run(CategoriesPipeline(records[:1]), Context(prune=True))

        assert report.removed == 1
        assert list(CargoCategory.objects.values_list("code", flat=True)) == ["food"]

    def test_prune_is_off_by_default(self, db, records):
        run(CategoriesPipeline(records))
        run(CategoriesPipeline(records[:1]))
        assert CargoCategory.objects.count() == 2


class TestChecks:
    """Готовые проверки качества."""

    @staticmethod
    def candidate(**values):
        return Candidate(key="k", position="p", values=values)

    def test_required_rejects_blank(self):
        assert required("name", "Наименование").inspect(self.candidate(name="  "))

    def test_required_accepts_value(self):
        assert required("name", "Наименование").inspect(self.candidate(name="Склад")) is None

    def test_within_reports_bounds(self):
        violation = within("lanes", 1, 16, "Число полос").inspect(self.candidate(lanes=40))
        assert violation is not None
        assert "40" in violation.message

    def test_within_skips_unknown_value(self):
        """Отсутствующее значение — это не выход за пределы."""
        assert within("lanes", 1, 16, "Число полос").inspect(self.candidate(lanes=None)) is None

    def test_not_negative(self):
        assert not_negative("area", "Площадь").inspect(self.candidate(area=-1))
        assert not_negative("area", "Площадь").inspect(self.candidate(area=0)) is None

    def test_not_negative_reports_non_numeric(self):
        violation = not_negative("area", "Площадь").inspect(self.candidate(area="около ста"))
        assert violation is not None
        assert "не является числом" in violation.message

    def test_one_of(self):
        check = one_of("kind", {"ring", "radial"}, "Роль")
        assert check.inspect(self.candidate(kind="chord"))
        assert check.inspect(self.candidate(kind="ring")) is None

    def test_fits_length(self):
        assert fits("name", 5, "Наименование").inspect(self.candidate(name="123456"))

    def test_condition_reads_extra(self):
        check = condition("x", "Условие", lambda item: item.extra.get("problem"))
        candidate = Candidate(key="k", position="p", values={}, extra={"problem": "беда"})
        assert check.inspect(candidate).message == "беда"

    def test_first_violation_wins(self, db):
        """Записи довольно одной причины отклонения: чинить всё равно её."""
        pipeline = CategoriesPipeline()
        candidate = Candidate(key="k", position="p", values={"name": "", "hazard_class": 42})
        assert pipeline.inspect(candidate).code == "required.name"


class TestValueComparison:
    """Сравнение хранимого и поступившего значений."""

    def test_numbers_of_different_types_match(self):
        from decimal import Decimal

        assert same_value(Decimal("108.40"), 108.4)

    def test_geometry_matches_by_representation(self):
        from geo import Geometry

        assert same_value(Geometry.point(37.6, 55.75), Geometry.point(37.60, 55.7500))

    def test_geometry_difference_is_seen(self):
        from geo import Geometry

        assert not same_value(Geometry.point(37.6, 55.75), Geometry.point(37.7, 55.75))

    def test_none_differs_from_empty_string(self):
        assert not same_value(None, "")

    def test_model_reference_matches_by_key(self, db, data_source):
        assert same_value(data_source, data_source)

    def test_outcome_names_are_stable(self):
        """Итог записи попадает в счётчики журнала и не должен меняться молча."""
        assert {item.value for item in Outcome} == {"created", "updated", "unchanged"}
