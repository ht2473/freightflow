"""Загрузка наборов данных из сценариев SQL в текущую базу.

Поставляемые наборы (``db/002_seed_data_scale*.sql``) написаны на диалекте
PostgreSQL с вызовами функций PostGIS. Прямое выполнение такого сценария на
SQLite невозможно, поэтому команда разбирает инструкции ``INSERT`` и записывает
данные средствами ORM: конструкторы геометрии приводятся к формату WKT, а
поле :class:`geo.fields.GeometryField` само подставляет нужное выражение для
активной СУБД.

Такой подход даёт три преимущества перед выполнением сценария через ``psql``:

* один и тот же набор данных загружается в оба поддерживаемых контура;
* соблюдаются ограничения и умолчания, объявленные в моделях;
* каждая загрузка регистрируется в журнале ``etl_log``, как и штатные
  процедуры обновления данных.

Примеры использования::

    python manage.py load_seed db/002_seed_data_scale1.sql
    python manage.py load_seed db/002_seed_data_scale400.sql --batch 2000
    python manage.py load_seed db/002_seed_data_scale1.sql --truncate
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import selectors
from core.models import (
    CargoCategory,
    CargoRoute,
    DataSource,
    District,
    EtlRun,
    FreightFlowStat,
    InfrastructureObject,
    InfrastructureType,
    RoadSegment,
    TrafficCondition,
    TrafficIncident,
)

# Соответствие имён таблиц и моделей. Порядок важен: справочники загружаются
# первыми, иначе ссылки на них окажутся недействительными.
TABLE_MODELS = {
    "districts": District,
    "infrastructure_types": InfrastructureType,
    "cargo_categories": CargoCategory,
    "data_sources": DataSource,
    "infrastructure_objects": InfrastructureObject,
    "road_segments": RoadSegment,
    "cargo_routes": CargoRoute,
    "freight_flow_stats": FreightFlowStat,
    "traffic_conditions": TrafficCondition,
    "traffic_incidents": TrafficIncident,
    "etl_log": EtlRun,
}

# Порядок очистки — обратный порядку загрузки, чтобы не нарушать ссылки.
TRUNCATE_ORDER = list(reversed(TABLE_MODELS.values()))

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?P<table>\w+)\s*\((?P<columns>[^)]*)\)\s*VALUES\s*\((?P<values>.*)\)\s*;\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Конструкторы геометрии PostGIS, встречающиеся в поставляемых наборах.
_MAKEPOINT_RE = re.compile(
    r"ST_SetSRID\s*\(\s*ST_MakePoint\s*\(\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)\s*\)\s*,\s*\d+\s*\)",
    re.IGNORECASE,
)
_GEOMFROMTEXT_RE = re.compile(
    r"ST_SetSRID\s*\(\s*ST_GeomFromText\s*\(\s*'([^']+)'\s*\)\s*,\s*\d+\s*\)", re.IGNORECASE
)
_GEOMFROMTEXT_SRID_RE = re.compile(
    r"ST_GeomFromText\s*\(\s*'([^']+)'\s*(?:,\s*\d+\s*)?\)", re.IGNORECASE
)


class Command(BaseCommand):
    """Загрузить набор данных из сценария SQL."""

    help = "Загрузка наборов данных из сценариев db/*.sql в текущую базу"

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", type=str, help="Путь к файлу сценария SQL")
        parser.add_argument(
            "--truncate",
            action="store_true",
            help="Очистить доменные таблицы перед загрузкой",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=1000,
            help="Размер пакета для групповой вставки (по умолчанию 1000)",
        )
        parser.add_argument(
            "--quiet-progress",
            action="store_true",
            help="Не выводить промежуточный ход загрузки",
        )

    def handle(self, *args, **options) -> None:
        path = Path(options["path"])
        if not path.exists():
            # Путь может быть указан относительно корня репозитория.
            alternative = Path(__file__).resolve().parents[4] / options["path"]
            if alternative.exists():
                path = alternative
            else:
                raise CommandError(f"Файл не найден: {options['path']}")

        started = time.perf_counter()
        self.stdout.write(self.style.MIGRATE_HEADING(f"Загрузка набора данных: {path.name}"))

        if options["truncate"]:
            self._truncate()

        counters, errors = self._load(path, options["batch"], not options["quiet_progress"])

        # Сброс кеша обязателен: иначе страницы продолжат показывать сводки,
        # рассчитанные до загрузки.
        selectors.invalidate_caches()
        try:
            from analytics import services as analytics_services

            analytics_services.invalidate()
        except ImportError:  # pragma: no cover — модуль аналитики отключён
            pass

        elapsed = time.perf_counter() - started
        total = sum(counters.values())

        self.stdout.write("")
        for table, count in counters.items():
            self.stdout.write(f"  {table:<26} {count:>8} записей")
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Загружено {total} записей за {elapsed:.1f} с"
                + (f", отклонено строк: {errors}" if errors else "")
            )
        )

    # ------------------------------------------------------------------ этапы

    def _truncate(self) -> None:
        """Очистить доменные таблицы и сбросить счётчики первичных ключей.

        Сброс счётчиков обязателен: в поставляемых наборах внешние ключи
        записаны явными числовыми значениями (``district_id = 2``), которые
        предполагают нумерацию с единицы. Без сброса повторная загрузка
        привела бы к нарушению ссылочной целостности.
        """
        from django.db import connection

        self.stdout.write("  Очистка доменных таблиц…")
        tables = [model._meta.db_table for model in TRUNCATE_ORDER]

        if connection.vendor == "postgresql" and not connection.in_atomic_block:
            # Одна инструкция очищает все таблицы и сбрасывает
            # последовательности, не нарушая порядок ссылок.
            #
            # Внутри открытой транзакции TRUNCATE неприменим: при наличии
            # отложенных проверок внешних ключей СУБД отклоняет инструкцию.
            # Такая ситуация возникает при вызове команды из автотеста, где
            # каждая проверка выполняется в собственной транзакции, — тогда
            # используется общий путь с удалением строк.
            with connection.cursor() as cursor:
                cursor.execute(
                    f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"
                )
            self.stdout.write(f"    очищено таблиц: {len(tables)}")
            return

        for model in TRUNCATE_ORDER:
            deleted, _ = model.objects.all().delete()
            if deleted:
                self.stdout.write(f"    {model._meta.db_table}: удалено {deleted}")

        # Сброс счётчиков после удаления строк. Способ различается для СУБД,
        # но необходим в обоих случаях: в поставляемых наборах внешние ключи
        # записаны явными числовыми значениями, предполагающими нумерацию с
        # единицы.
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                # Django объявляет первичные ключи как AUTOINCREMENT, поэтому
                # счётчики хранятся в служебной таблице sqlite_sequence и
                # переживают удаление строк.
                placeholders = ", ".join(["%s"] * len(tables))
                cursor.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", tables
                )
            elif connection.vendor == "postgresql":
                for table in tables:
                    cursor.execute(
                        "SELECT pg_get_serial_sequence(%s, 'id')", [table]
                    )
                    sequence = cursor.fetchone()[0]
                    if sequence:
                        cursor.execute(f"ALTER SEQUENCE {sequence} RESTART WITH 1")

    def _load(self, path: Path, batch_size: int, progress: bool) -> tuple[dict[str, int], int]:
        """Разобрать сценарий и записать данные по одной таблице за проход.

        Файл читается столько раз, сколько в системе доменных таблиц, — по
        одному проходу на таблицу в порядке их объявления. Такой порядок
        необходим для соблюдения ссылочной целостности: справочники должны
        быть записаны до записей, которые на них ссылаются.

        Альтернатива с накоплением всех строк в памяти и последующей записью
        отвергнута: расширенный набор содержит около 78 000 записей, и их
        одновременное присутствие в памяти в виде объектов модели потребовало
        бы сотен мегабайт. При построчном проходе объём памяти ограничен
        размером пакета независимо от объёма файла.

        Затраты на повторное чтение невелики: файл читается последовательно,
        а разбор строки выполняется только для нужной таблицы.
        """
        counters: dict[str, int] = {}
        errors = 0
        run_started = timezone.now()

        for table, model in TABLE_MODELS.items():
            buffer: list = []

            with path.open(encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line.upper().startswith(f"INSERT INTO {table.upper()} "):
                        continue

                    match = _INSERT_RE.match(line)
                    if not match or match.group("table").lower() != table:
                        continue

                    try:
                        buffer.append(self._build_instance(model, match))
                    except (ValueError, KeyError) as exc:
                        errors += 1
                        if errors <= 5:
                            self.stderr.write(f"    строка {line_number}: {exc}")
                        continue

                    if len(buffer) >= batch_size:
                        self._flush(model, buffer, counters, table, progress)

            if buffer:
                self._flush(model, buffer, counters, table, progress)

        self._register_run(path, counters, errors, run_started)
        return counters, errors

    def _flush(self, model, buffer: list, counters: dict, table: str, progress: bool) -> None:
        """Записать накопленный пакет объектов в базу."""
        with transaction.atomic():
            model.objects.bulk_create(buffer, batch_size=500)
        counters[table] = counters.get(table, 0) + len(buffer)
        if progress:
            self.stdout.write(f"    {table}: {counters[table]}", ending="\r")
            self.stdout.flush()
        buffer.clear()

    def _register_run(self, path: Path, counters: dict, errors: int, started) -> None:
        """Зафиксировать факт загрузки в журнале ETL.

        Записывается одна итоговая строка на весь сценарий: она отражает
        загрузку как единую операцию и не искажает статистику регламентных
        процедур обновления данных.
        """
        source = DataSource.objects.filter(code="manual").first() or DataSource.objects.first()
        EtlRun.objects.create(
            started_at=started,
            finished_at=timezone.now(),
            source=source,
            target_table=f"seed:{path.name}",
            records_loaded=sum(counters.values()),
            records_errors=errors,
            status="success" if not errors else "partial",
            error_message=f"Отклонено строк: {errors}" if errors else "",
        )

    # ----------------------------------------------------------- разбор строки

    def _build_instance(self, model, match: re.Match):
        """Собрать объект модели из инструкции INSERT."""
        columns = [name.strip() for name in match.group("columns").split(",")]
        values = _split_values(match.group("values"))
        if len(columns) != len(values):
            raise ValueError(
                f"несовпадение числа колонок ({len(columns)}) и значений ({len(values)})"
            )

        # Соответствие имён колонок базы и атрибутов модели.
        field_by_column = {
            field.column: field.attname
            for field in model._meta.fields
            if getattr(field, "column", None)
        }

        kwargs = {}
        for column, raw in zip(columns, values, strict=False):
            attname = field_by_column.get(column)
            if attname is None:
                # Колонка отсутствует в модели — пропускается сознательно:
                # набор данных может содержать поля, не используемые системой.
                continue
            value = _parse_value(raw)
            if value is None:
                # Поле, не допускающее NULL, получает объявленное умолчание:
                # отсутствие необязательного атрибута не должно приводить к
                # отклонению всей записи.
                field = model._meta.get_field(attname.removesuffix("_id"))
                if not field.null:
                    value = field.get_default()
            kwargs[attname] = value
        return model(**kwargs)


def _split_values(text: str) -> list[str]:
    """Разбить список значений по запятым верхнего уровня.

    Учитываются строковые литералы в одинарных кавычках (включая удвоенные
    кавычки внутри строки) и вложенные скобки вызовов функций.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    index = 0

    while index < len(text):
        char = text[index]

        if in_string:
            current.append(char)
            if char == "'":
                # Удвоенная кавычка внутри строки экранирует саму себя.
                if index + 1 < len(text) and text[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if char == "'":
            in_string = True
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1

    if current:
        parts.append("".join(current).strip())
    return parts


def _parse_value(raw: str):
    """Преобразовать литерал SQL в значение Python."""
    text = raw.strip()
    upper = text.upper()

    if upper == "NULL":
        return None
    if upper == "TRUE":
        return True
    if upper == "FALSE":
        return False

    # Конструкторы геометрии PostGIS → представление WKT.
    if upper.startswith("ST_"):
        point = _MAKEPOINT_RE.match(text)
        if point:
            return f"POINT({point.group(1)} {point.group(2)})"
        line = _GEOMFROMTEXT_RE.match(text) or _GEOMFROMTEXT_SRID_RE.match(text)
        if line:
            return line.group(1)
        raise ValueError(f"неизвестный конструктор геометрии: {text[:48]}")

    # Строковый литерал. Отметки времени в наборах записаны без указания
    # смещения и трактуются как местное время (Europe/Moscow) — именно так
    # их формируют ведомственные источники.
    if text.startswith("'") and text.endswith("'"):
        literal = text[1:-1].replace("''", "'")
        moment = _parse_datetime(literal)
        return moment if moment is not None else literal

    # Числовой литерал.
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass

    return text


def _parse_datetime(literal: str):
    """Разобрать отметку времени и привязать её к часовому поясу проекта.

    Возвращает ``None``, если строка не является отметкой времени, — тогда
    вызывающий код трактует её как обычный текст. Даты без времени
    (``YYYY-MM-DD``) не преобразуются: они относятся к полям типа ``DateField``,
    для которых часовой пояс неприменим.
    """
    if len(literal) < 16 or literal[4] != "-" or literal[7] != "-":
        return None
    try:
        moment = datetime.fromisoformat(literal)
    except ValueError:
        return None
    if timezone.is_naive(moment):
        return timezone.make_aware(moment, timezone.get_default_timezone())
    return moment
