"""Загрузка ряда, присланного пользователем.

Ведомственная статистика приходит в систему готовыми публикациями, но не всё
существует в виде публикации: у перевозчика есть собственный учёт, у отраслевой
организации — обследование, у исследователя — ряд, собранный вручную. Такие
данные должны попадать в систему тем же путём, что и все прочие, — иначе
рядом с прослеживаемыми сведениями появятся сведения, о происхождении которых
ничего не известно.

Поэтому присланный файл проходит тот же конвейер: те же проверки, тот же
журнал, тот же карантин. Отличие ровно одно — получение: вместо обращения
к службе читается файл. Строки, не прошедшие проверку, откладываются
в карантин с номером строки, и приславший видит, что именно исправить.

Ожидаемый состав колонок описан в :data:`COLUMNS`. Подписи распознаются
с допусками: регистр, «ё» и лишние пробелы значения не имеют, а для каждой
колонки предусмотрено несколько написаний.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.choices import (
    DataOrigin,
    FlowDirection,
    FlowScope,
    PeriodType,
    SourceType,
    UpdateFrequency,
)
from core.models import DataSource, District, FreightFlowStat

from .pipeline import Candidate, Context, Extract, ModelPipeline, RunReport
from .quality import Check, not_negative, required
from .tabular import Table, TableError, TableRow, normalize_header, read_table

logger = logging.getLogger("freightflow.etl")

#: Код источника в справочнике системы.
SOURCE_CODE = "upload"


@dataclass(frozen=True)
class Column:
    """Колонка ожидаемой выгрузки."""

    field: str
    title: str
    aliases: tuple[str, ...]
    required: bool = False
    hint: str = ""

    def find(self, columns: list[str]) -> str | None:
        for alias in self.aliases:
            key = normalize_header(alias)
            if key in columns:
                return key
        return None


#: Состав присылаемой выгрузки.
COLUMNS: tuple[Column, ...] = (
    Column("period", "Период", ("период", "дата", "год", "месяц"), required=True,
           hint="год «2024», месяц «2024-07» либо дата «01.07.2024»"),
    Column("territory", "Территория", ("территория", "субъект", "регион"),
           hint="наименование территории; для округов Москвы — их аббревиатура"),
    Column("scope", "Круг перевозчиков", ("круг перевозчиков", "охват", "scope"),
           hint="«все» либо «коммерческие»; по умолчанию — все"),
    Column("direction", "Направление", ("направление", "direction"),
           hint="ввоз, вывоз, транзит либо всего; по умолчанию — всего"),
    Column("volume_tons", "Объём, т", ("объем, т", "объем", "перевезено, т", "тонн"),
           hint="объём перевезённого груза в тоннах"),
    Column("turnover_ton_km", "Грузооборот, т·км",
           ("грузооборот, т·км", "грузооборот", "тонно-километры"),
           hint="грузооборот в тонно-километрах"),
    Column("vehicle_count", "Число рейсов", ("число рейсов", "рейсов", "поездок"),
           hint="количество выполненных рейсов"),
)

#: Подписи направлений и охвата, встречающиеся в присылаемых выгрузках.
DIRECTIONS: dict[str, str] = {
    "ввоз": FlowDirection.IN, "in": FlowDirection.IN, "прибытие": FlowDirection.IN,
    "вывоз": FlowDirection.OUT, "out": FlowDirection.OUT, "отправление": FlowDirection.OUT,
    "транзит": FlowDirection.TRANSIT, "transit": FlowDirection.TRANSIT,
    "всего": FlowDirection.TOTAL, "итого": FlowDirection.TOTAL,
    "total": FlowDirection.TOTAL, "": FlowDirection.TOTAL,
}

SCOPES: dict[str, str] = {
    "все": FlowScope.ALL, "все перевозчики": FlowScope.ALL, "all": FlowScope.ALL,
    "": FlowScope.ALL,
    "коммерческие": FlowScope.COMMERCIAL, "commercial": FlowScope.COMMERCIAL,
    "на коммерческой основе": FlowScope.COMMERCIAL,
}

#: Разбор периода: год, год с месяцем, дата.
_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})[-./](\d{1,2})$")
_DATE_RE = re.compile(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{4})")
_ISO_RE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})")


class UploadError(TableError):
    """Присланная выгрузка непригодна для загрузки."""


def ensure_source() -> DataSource:
    """Справочная запись об источнике присланных выгрузок."""
    source, _ = DataSource.objects.update_or_create(
        code=SOURCE_CODE,
        defaults={
            "name": "Выгрузки, присланные пользователями",
            "source_type": SourceType.CSV,
            "url": "",
            "update_frequency": UpdateFrequency.MONTHLY,
            "is_active": True,
        },
    )
    return source


def parse_period(raw: str) -> tuple[date | None, str]:
    """Разобрать период наблюдения.

    Возвращает пару «начало периода, гранулярность». Год и месяц различаются
    по самой записи: пользователь не должен объявлять гранулярность отдельно,
    когда она видна из значения.
    """
    text = (raw or "").strip()
    if not text:
        return None, ""

    # Excel отдаёт дату строкой вида «2024-07-01 00:00:00».
    text = text.split(" ")[0]

    match = _YEAR_RE.match(text)
    if match:
        return date(int(match.group(1)), 1, 1), PeriodType.YEAR

    match = _MONTH_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1), PeriodType.MONTH
        return None, ""

    match = _ISO_RE.match(text)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)),
                          int(match.group(3))), PeriodType.DAY

    match = _DATE_RE.match(text)
    if match:
        return _safe_date(int(match.group(3)), int(match.group(2)),
                          int(match.group(1))), PeriodType.DAY

    return None, ""


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_number(raw: str) -> Decimal | None | str:
    """Разобрать число, допуская русскую запись дробной части.

    Возвращает число, ``None`` для пустого значения либо строку с описанием
    ошибки. Пробелы-разделители разрядов встречаются в любой выгрузке,
    сделанной человеком, и отбрасывать из-за них строку нельзя.
    """
    text = (raw or "").strip().replace(" ", "").replace(" ", "")
    if not text or text in {"-", "—", "…", "нд", "н/д"}:
        return None
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return f"значение «{raw}» не является числом"


# ---------------------------------------------------------------------------
#  Проверки
# ---------------------------------------------------------------------------


def _period_parsed(candidate: Candidate) -> str | None:
    if candidate.values.get("period_date") is None:
        return (
            f"период «{candidate.extra.get('period_raw', '')}» не распознан: "
            f"ожидается год, год с месяцем либо дата"
        )
    return None


def _numbers_parsed(candidate: Candidate) -> str | None:
    problems = candidate.extra.get("number_errors") or []
    return "; ".join(problems) or None


def _known_direction(candidate: Candidate) -> str | None:
    """Направление должно быть распознано.

    Проверка читает исходное значение, а не записываемое: в запись подставлен
    итог «всего», и по нему нераспознанное направление уже неотличимо
    от пропущенного.
    """
    if candidate.extra.get("direction_resolved") is None:
        raw = candidate.extra.get("direction_raw", "")
        allowed = ", ".join(sorted({key for key in DIRECTIONS if key}))
        return f"направление «{raw}» не распознано; допустимы: {allowed}"
    return None


def _known_scope(candidate: Candidate) -> str | None:
    """Круг перевозчиков должен быть распознан."""
    if candidate.extra.get("scope_resolved") is None:
        raw = candidate.extra.get("scope_raw", "")
        allowed = ", ".join(sorted({key for key in SCOPES if key}))
        return f"круг перевозчиков «{raw}» не распознан; допустимы: {allowed}"
    return None


def _has_measure(candidate: Candidate) -> str | None:
    measures = ("volume_tons", "turnover_ton_km", "vehicle_count")
    if all(candidate.values.get(field) is None for field in measures):
        return "в строке нет ни одного показателя"
    return None


def _unique_within_file(candidate: Candidate) -> str | None:
    first = candidate.extra.get("duplicate_of")
    if first:
        return f"строка повторяет ранее прочитанную (строка {first})"
    return None


class FlowUploadPipeline(ModelPipeline):
    """Ряд грузопотоков из выгрузки, присланной пользователем."""

    name = "upload.flows"
    title = "Выгрузка ряда грузопотоков"
    target_table = "freight_flow_stats"
    source_code = SOURCE_CODE
    description = (
        "Файл CSV или книга Excel с рядом перевозок. Строки проходят те же "
        "проверки, что и данные внешних служб; не прошедшие откладываются "
        "в карантин с номером строки."
    )
    model = FreightFlowStat
    expects_upload = True
    console_enabled = False
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        Check("upload.period", "Период распознан", _period_parsed),
        Check("upload.number", "Числовые значения разобраны", _numbers_parsed),
        Check("upload.direction", "Направление распознано", _known_direction),
        Check("upload.scope", "Круг перевозчиков распознан", _known_scope),
        Check("upload.measure", "Строка содержит показатель", _has_measure),
        Check("upload.duplicate", "Строка не повторяется в файле", _unique_within_file),
        required("period_date", "Период наблюдения"),
        not_negative("volume_tons", "Объём перевозок"),
        not_negative("turnover_ton_km", "Грузооборот"),
        not_negative("vehicle_count", "Число рейсов"),
    )

    def ensure_source(self) -> DataSource:
        return ensure_source()

    def lookup(self, candidate: Candidate) -> dict:
        return {"source": candidate.extra["source"], "external_key": candidate.key}

    def extract(self, context: Context) -> Extract:
        payload = context.options.get("content")
        path = context.options.get("file")
        title = context.options.get("name") or (Path(path).name if path else "выгрузка")

        if payload is None and not path:
            raise UploadError(
                "Не указан файл выгрузки. Конвейер работает с присланными "
                "данными: путь передаётся ключом --file либо формой панели."
            )
        table = read_table(payload if payload is not None else path, name=title)
        _require_expected_columns(table)
        return Extract(records=table, count=len(table), fetched_at=None)

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        table: Table = extract.records
        source = self.ensure_source()
        districts = {
            district.short_name.lower(): district for district in District.objects.all()
        }
        districts.update(
            {district.name.lower(): district for district in District.objects.all()}
        )
        mapping = {column.field: column.find(table.columns) for column in COLUMNS}
        seen: dict[str, int] = {}

        for row in table.rows:
            candidate = self._candidate(row, mapping, source, districts, seen)
            if candidate is not None:
                yield candidate

    def _candidate(self, row: TableRow, mapping: dict[str, str | None],
                   source: DataSource, districts: dict[str, District],
                   seen: dict[str, int]) -> Candidate | None:
        def cell(field: str) -> str:
            column = mapping.get(field)
            return row.get(column) if column else ""

        period_raw = cell("period")
        period_date, period_type = parse_period(period_raw)

        direction_raw = cell("direction")
        scope_raw = cell("scope")
        territory = cell("territory")

        numbers: dict[str, Decimal | None] = {}
        errors: list[str] = []
        for field, label in (
            ("volume_tons", "объём"),
            ("turnover_ton_km", "грузооборот"),
            ("vehicle_count", "число рейсов"),
        ):
            parsed = parse_number(cell(field))
            if isinstance(parsed, str):
                errors.append(f"{label}: {parsed}")
                numbers[field] = None
            else:
                numbers[field] = parsed

        direction = DIRECTIONS.get(direction_raw.strip().lower())
        scope = SCOPES.get(scope_raw.strip().lower())
        district = districts.get(territory.strip().lower())

        key = "|".join((
            "upload",
            territory.strip().lower(),
            scope or scope_raw,
            direction or direction_raw,
            period_raw.strip(),
        ))
        duplicate_of = seen.get(key)
        if duplicate_of is None:
            seen[key] = row.number

        vehicle_count = numbers["vehicle_count"]
        return Candidate(
            key=key[:120],
            position=f"строка {row.number}",
            values={
                "period_date": period_date,
                "period_type": period_type or PeriodType.YEAR,
                "territory": territory[:120],
                "district": district,
                "direction": direction or FlowDirection.TOTAL,
                "scope": scope or FlowScope.ALL,
                "volume_tons": numbers["volume_tons"],
                "turnover_ton_km": numbers["turnover_ton_km"],
                "vehicle_count": int(vehicle_count) if vehicle_count is not None else None,
                "origin": DataOrigin.MEASURED,
                "source": source,
            },
            extra={
                "source": source,
                "period_raw": period_raw,
                "direction_raw": direction_raw,
                "direction_resolved": direction,
                "scope_raw": scope_raw,
                "scope_resolved": scope,
                "number_errors": errors,
                "duplicate_of": duplicate_of,
            },
            payload=row.values,
        )

    def verify(self, report: RunReport, context: Context) -> None:
        if report.processed:
            report.detail(
                f"принято строк: {report.processed} "
                f"(создано {report.created}, обновлено {report.updated})"
            )


def _require_expected_columns(table: Table) -> None:
    """Убедиться, что в выгрузке есть обязательные колонки."""
    missing = [
        column.title
        for column in COLUMNS
        if column.required and column.find(table.columns) is None
    ]
    if missing:
        raise UploadError(
            f"В выгрузке {table.name} нет обязательных колонок: "
            f"{', '.join(missing)}. Ожидаемый состав: "
            f"{', '.join(item.title for item in COLUMNS)}"
        )


def template_csv() -> str:
    """Образец выгрузки: заголовок и одна строка-пример.

    Образец отдаётся из того же описания колонок, по которому идёт разбор:
    разойтись они не могут.
    """
    header = ";".join(column.title for column in COLUMNS)
    example = ";".join((
        "2024", "г. Москва", "все", "всего", "39500000", "10500000000", "",
    ))
    hints = ";".join(column.hint for column in COLUMNS)
    return f"{header}\n{example}\n# {hints}\n"


__all__ = [
    "COLUMNS",
    "FlowUploadPipeline",
    "UploadError",
    "ensure_source",
    "parse_number",
    "parse_period",
    "template_csv",
]
