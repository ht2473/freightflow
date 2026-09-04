"""Сверка канонического описания схемы с моделями приложения.

Файл ``db/001_schema.sql`` объявлен каноническим: базу можно поднять и
миграциями Django, и «сырым» SQL, и результат должен получиться одинаковым.
Расхождение между этими двумя описаниями обнаруживается только тогда, когда
контур разворачивают вторым способом, — то есть в самый неподходящий момент.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

#: Таблицы, описанные в каноническом файле схемы.
SCHEMA_FILE = Path(settings.ROOT_DIR) / "db" / "001_schema.sql"

#: Объявление таблицы целиком: от списка колонок до закрывающей скобки.
_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL)


def parse_schema() -> dict[str, set[str]]:
    """Прочитать состав колонок каждой таблицы из файла схемы."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    tables: dict[str, set[str]] = {}
    for match in _TABLE_RE.finditer(sql):
        columns = set()
        for line in match.group(2).splitlines():
            line = line.strip().rstrip(",")
            # Ограничения и хвосты многострочных выражений колонками не являются.
            if not line or line in {")", "("} or line.startswith(("CONSTRAINT", "--")):
                continue
            columns.add(line.split()[0])
        tables[match.group(1)] = columns
    return tables


@pytest.fixture(scope="module")
def schema() -> dict[str, set[str]]:
    return parse_schema()


def domain_models():
    """Модели, отображённые на таблицы канонической схемы."""
    known = parse_schema()
    return [model for model in apps.get_models() if model._meta.db_table in known]


class TestSchemaFile:
    """Файл схемы читается и содержит доменные таблицы."""

    def test_file_exists(self):
        assert SCHEMA_FILE.exists()

    def test_tables_are_parsed(self, schema):
        assert len(schema) >= 13

    def test_service_tables_are_present(self, schema):
        assert "etl_log" in schema
        assert "etl_rejects" in schema


class TestColumnsMatchModels:
    """Состав колонок совпадает с полями моделей."""

    @pytest.mark.parametrize("model", domain_models(), ids=lambda m: m._meta.db_table)
    def test_model_columns_are_declared(self, model, schema):
        """Каждое поле модели объявлено в файле схемы."""
        columns = {field.column for field in model._meta.local_fields}
        missing = columns - schema[model._meta.db_table]
        assert not missing, (
            f"в db/001_schema.sql нет колонок таблицы {model._meta.db_table}: "
            f"{sorted(missing)}"
        )

    @pytest.mark.parametrize("model", domain_models(), ids=lambda m: m._meta.db_table)
    def test_declared_columns_have_fields(self, model, schema):
        """Каждая колонка файла схемы отвечает полю модели."""
        columns = {field.column for field in model._meta.local_fields}
        extra = schema[model._meta.db_table] - columns
        assert not extra, (
            f"в модели {model.__name__} нет полей для колонок: {sorted(extra)}"
        )


class TestConstraints:
    """Перечисления в ограничениях CHECK совпадают с перечислениями приложения."""

    @staticmethod
    def allowed(constraint: str) -> set[str]:
        sql = SCHEMA_FILE.read_text(encoding="utf-8")
        match = re.search(rf"CONSTRAINT {constraint} CHECK \(\s*\w+ IN \((.*?)\)",
                          sql, re.DOTALL)
        assert match, f"ограничение {constraint} не найдено"
        return {value.strip().strip("'") for value in match.group(1).split(",")}

    def test_source_types(self):
        from core.choices import SourceType

        assert self.allowed("data_sources_type_allowed") == set(SourceType.values)

    def test_flow_directions(self):
        from core.choices import FlowDirection

        assert self.allowed("flow_direction_allowed") == set(FlowDirection.values)

    def test_flow_scopes(self):
        from core.choices import FlowScope

        assert self.allowed("flow_scope_allowed") == set(FlowScope.values)

    def test_period_types(self):
        from core.choices import PeriodType

        assert self.allowed("flow_period_allowed") == set(PeriodType.values)

    def test_incident_types(self):
        from core.choices import IncidentType

        assert self.allowed("incident_type_allowed") == set(IncidentType.values)

    def test_etl_statuses(self):
        from core.choices import EtlStatus

        assert self.allowed("etl_status_allowed") == set(EtlStatus.values)

    def test_etl_triggers(self):
        from core.choices import EtlTrigger

        assert self.allowed("etl_trigger_allowed") == set(EtlTrigger.values)

    def test_road_classes(self):
        from core.choices import RoadClass

        assert self.allowed("road_class_allowed") == set(RoadClass.values)

    def test_route_types(self):
        from core.choices import RouteType

        assert self.allowed("route_type_allowed") == set(RouteType.values)
