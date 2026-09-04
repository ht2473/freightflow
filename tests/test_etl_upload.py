"""Проверки загрузки выгрузок, присланных пользователями.

Присланный файл проходит тот же конвейер, что и данные внешних служб, поэтому
проверяется и разбор самого файла, и поведение конвейера на нём: номера строк
в карантине, распознавание периода и чисел, повторная загрузка.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from core.choices import FlowDirection, FlowScope, PeriodType
from core.models import EtlReject, FreightFlowStat
from etl.pipeline import Context, PipelineError, run
from etl.tabular import TableError, normalize_header, read_table
from etl.upload import (
    FlowUploadPipeline,
    parse_number,
    parse_period,
    template_csv,
)

HEADER = "Период;Территория;Круг перевозчиков;Направление;Объём, т;Грузооборот, т·км;Число рейсов"


def upload(text: str, name: str = "ряд.csv") -> Context:
    """Условия запуска с присланным содержимым."""
    return Context(options={"content": text.encode("utf-8"), "name": name})


# ---------------------------------------------------------------------------
#  Чтение файла
# ---------------------------------------------------------------------------


class TestTabular:
    """Разбор табличных выгрузок."""

    def test_semicolon_is_recognised(self):
        table = read_table(b"a;b\n1;2\n", "t.csv")
        assert table.columns == ["a", "b"]
        assert table.rows[0].values == {"a": "1", "b": "2"}

    def test_comma_is_recognised(self):
        table = read_table(b"a,b\n1,2\n", "t.csv")
        assert table.columns == ["a", "b"]

    def test_windows_encoding_is_read(self):
        """Выгрузка из Excel в русской локали приходит в кодировке Windows."""
        table = read_table("Период;Объём\n2024;10\n".encode("cp1251"), "t.csv")
        assert table.columns[0] == "период"

    def test_header_is_normalized(self):
        assert normalize_header("  Объём, Т ") == "объем, т"

    def test_row_numbers_match_the_file(self):
        table = read_table(b"a\n1\n2\n", "t.csv")
        assert [row.number for row in table.rows] == [2, 3]

    def test_blank_rows_are_dropped(self):
        table = read_table(b"a;b\n1;2\n;\n3;4\n", "t.csv")
        assert len(table.rows) == 2

    def test_empty_file_is_reported(self):
        with pytest.raises(TableError, match="пуст"):
            read_table(b"", "t.csv")

    def test_workbook_is_read(self, tmp_path):
        import openpyxl

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["Период", "Объём, т"])
        sheet.append([2024, 39500000])
        path = tmp_path / "ряд.xlsx"
        book.save(path)

        table = read_table(str(path))
        assert table.columns == ["период", "объем, т"]
        assert table.rows[0].get("объем, т") == "39500000"


class TestPeriod:
    """Гранулярность видна из самой записи периода."""

    @pytest.mark.parametrize(
        ("raw", "year", "month", "kind"),
        [
            ("2024", 2024, 1, PeriodType.YEAR),
            ("2024-07", 2024, 7, PeriodType.MONTH),
            ("2024-07-15", 2024, 7, PeriodType.DAY),
            ("15.07.2024", 2024, 7, PeriodType.DAY),
        ],
    )
    def test_forms(self, raw, year, month, kind):
        period, granularity = parse_period(raw)
        assert (period.year, period.month) == (year, month)
        assert granularity == kind

    def test_excel_datetime(self):
        period, kind = parse_period("2024-07-01 00:00:00")
        assert period.month == 7
        assert kind == PeriodType.DAY

    def test_unparsable(self):
        assert parse_period("июль прошлого года") == (None, "")

    def test_impossible_date(self):
        assert parse_period("2024-13") == (None, "")


class TestNumbers:
    """Разбор чисел с допусками к человеческой записи."""

    def test_comma_decimal(self):
        assert parse_number("39,5") == Decimal("39.5")

    def test_thousands_separator(self):
        assert parse_number("39 500 000") == Decimal("39500000")

    def test_dash_means_absent(self):
        assert parse_number("—") is None

    def test_text_is_reported(self):
        assert "не является числом" in parse_number("много")


# ---------------------------------------------------------------------------
#  Конвейер
# ---------------------------------------------------------------------------


class TestLoad:
    """Наполнение ряда из присланного файла."""

    def test_row_becomes_record(self, db):
        report = run(
            FlowUploadPipeline(),
            upload(f"{HEADER}\n2024;г. Москва;все;всего;39500000;10500000000;\n"),
        )
        assert report.created == 1

        record = FreightFlowStat.objects.get()
        assert record.territory == "г. Москва"
        assert record.volume_tons == Decimal("39500000.00")
        assert record.direction == FlowDirection.TOTAL
        assert record.scope == FlowScope.ALL

    def test_defaults_are_applied(self, db):
        run(FlowUploadPipeline(), upload("Период;Объём, т\n2024;100\n"))
        record = FreightFlowStat.objects.get()
        assert record.direction == FlowDirection.TOTAL
        assert record.scope == FlowScope.ALL

    def test_district_is_matched(self, db, districts):
        run(FlowUploadPipeline(), upload("Период;Территория;Объём, т\n2024;ЦАО;100\n"))
        assert FreightFlowStat.objects.get().district.short_name == "ЦАО"

    def test_scope_is_recognised(self, db):
        run(
            FlowUploadPipeline(),
            upload("Период;Круг перевозчиков;Объём, т\n2024;коммерческие;100\n"),
        )
        assert FreightFlowStat.objects.get().scope == FlowScope.COMMERCIAL

    def test_source_is_registered(self, db):
        run(FlowUploadPipeline(), upload("Период;Объём, т\n2024;100\n"))
        assert FreightFlowStat.objects.get().source.code == "upload"

    def test_missing_required_column(self, db):
        with pytest.raises(PipelineError, match="Период"):
            run(FlowUploadPipeline(), upload("Территория;Объём, т\nМосква;100\n"))

    def test_file_is_required(self, db):
        with pytest.raises(PipelineError, match="файл"):
            run(FlowUploadPipeline())


class TestQuarantine:
    """Строка, не прошедшая проверку, отыскивается в присланном файле."""

    def test_bad_period_is_rejected(self, db):
        report = run(
            FlowUploadPipeline(),
            upload("Период;Объём, т\n2024;100\nпозавчера;200\n"),
        )
        assert report.created == 1
        assert report.rejected == 1

        reject = EtlReject.objects.get()
        assert reject.position == "строка 3"
        assert reject.check_code == "upload.period"

    def test_text_instead_of_number(self, db):
        run(FlowUploadPipeline(), upload("Период;Объём, т\n2024;много\n"))
        assert EtlReject.objects.get().check_code == "upload.number"

    def test_row_without_measures(self, db):
        run(FlowUploadPipeline(), upload("Период;Территория;Объём, т\n2024;Москва;\n"))
        assert EtlReject.objects.get().check_code == "upload.measure"

    def test_unknown_direction(self, db):
        run(
            FlowUploadPipeline(),
            upload("Период;Направление;Объём, т\n2024;наискосок;100\n"),
        )
        assert EtlReject.objects.get().check_code == "upload.direction"

    def test_duplicate_row_is_rejected(self, db):
        report = run(
            FlowUploadPipeline(),
            upload("Период;Территория;Объём, т\n2024;Москва;100\n2024;Москва;200\n"),
        )
        assert report.created == 1
        assert EtlReject.objects.get().check_code == "upload.duplicate"

    def test_negative_value_is_rejected(self, db):
        run(FlowUploadPipeline(), upload("Период;Объём, т\n2024;-5\n"))
        assert EtlReject.objects.get().check_code == "positive.volume_tons"


class TestIncremental:
    """Повторная присылка того же ряда не удваивает записи."""

    def test_repeat_upload_changes_nothing(self, db):
        content = f"{HEADER}\n2024;г. Москва;все;всего;39500000;10500000000;\n"
        run(FlowUploadPipeline(), upload(content))
        report = run(FlowUploadPipeline(), upload(content))

        assert report.unchanged == 1
        assert FreightFlowStat.objects.count() == 1

    def test_corrected_value_updates_record(self, db):
        run(FlowUploadPipeline(), upload("Период;Территория;Объём, т\n2024;Москва;100\n"))
        run(FlowUploadPipeline(), upload("Период;Территория;Объём, т\n2024;Москва;200\n"))

        assert FreightFlowStat.objects.count() == 1
        assert FreightFlowStat.objects.get().volume_tons == Decimal("200.00")


class TestTemplate:
    """Образец выгрузки строится из того же описания колонок."""

    def test_template_is_readable(self):
        table = read_table(template_csv().encode("utf-8"), "образец.csv")
        assert "период" in table.columns

    def test_template_loads(self, db):
        report = run(FlowUploadPipeline(), upload(template_csv(), "образец.csv"))
        assert report.created == 1
