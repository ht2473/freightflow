"""Конвейер загрузки данных: единый порядок работы с любым источником.

Источники у системы разные по природе: программный интерфейс OpenStreetMap,
статистические таблицы Росстата, перечни, ведущиеся вручную, файл, присланный
пользователем. Порядок работы с ними один и тот же, и он задан здесь:

1. **получение** — забрать выгрузку у источника (:meth:`Pipeline.extract`);
2. **приведение** — превратить её в записи доменной модели
   (:meth:`Pipeline.prepare`);
3. **проверка** — применить к каждой записи объявленные проверки качества;
   не прошедшие откладываются в карантин;
4. **запись** — обновить существующую запись либо создать новую
   (:meth:`Pipeline.write`);
5. **журнал** — записать итог со всеми счётчиками.

Разделение существенно не само по себе, а потому, что делает поведение
источников сопоставимым. Загрузка любого набора отвечает на одни и те же
вопросы: сколько записей пришло, сколько из них новых, сколько изменилось,
сколько отклонено и по какой причине. Без общего порядка эти величины у
каждого загрузчика считались бы по-своему и не складывались бы в оценку
состояния данных.

Повторный запуск не создаёт дубликатов: запись отыскивается по ключу
источника, а её содержимое сравнивается с поступившим. Совпадение означает,
что в источнике ничего не изменилось, и такая запись учитывается отдельно —
по доле неизменившихся видно, что инкрементальная загрузка работает.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from accounts import notify
from core import selectors
from core.choices import EtlStatus, EtlTrigger
from core.models import DataSource, EtlReject, EtlRun
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from geo.geometry import Geometry

from .quality import Check, Violation

logger = logging.getLogger("freightflow.etl")

#: Наибольшее число записей, откладываемых в карантин за один запуск.
#: Ограничение защищает от разрастания карантина при неисправном источнике:
#: тысяча однотипных отклонений говорит ровно то же, что и первая сотня,
#: а полный счётчик отклонений сохраняется в журнале в любом случае.
QUARANTINE_LIMIT = 200


class PipelineError(RuntimeError):
    """Загрузка прервана: источник недоступен или выгрузка непригодна."""


class Outcome(str, Enum):
    """Итог записи одного кандидата."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass
class Context:
    """Условия выполнения загрузки.

    Один и тот же конвейер запускается из командной строки, из панели
    администратора, по расписанию и при получении файла от пользователя.
    Различия между запусками собраны здесь, чтобы сам конвейер о способе
    запуска не знал.
    """

    refresh: bool = False
    offline: bool = False
    prune: bool = False
    dry_run: bool = False
    trigger: str = EtlTrigger.CLI
    actor: Any = None
    #: Параметры, относящиеся к одному конвейеру: путь к присланному файлу,
    #: отбор наборов и тому подобное.
    options: dict[str, Any] = field(default_factory=dict)

    _client: Any = None

    def client(self):
        """Клиент Overpass API, общий для всех наборов одного запуска."""
        if self._client is None:
            from .client import OverpassClient

            self._client = OverpassClient(offline=self.offline)
        return self._client

    def describe(self) -> str:
        """Параметры запуска строкой — для журнала."""
        parts = [name for name in ("refresh", "offline", "prune", "dry_run")
                 if getattr(self, name)]
        parts += [f"{key}={value}" for key, value in sorted(self.options.items())]
        return ", ".join(parts)[:200]


@dataclass(frozen=True)
class Candidate:
    """Запись источника, приведённая к доменной модели, но ещё не проверенная.

    Атрибуты:
        key: естественный ключ записи — по нему конвейер отыскивает её
            в базе и по нему же приводит реестр к составу источника;
        position: положение в источнике («way/12345», «строка 42») —
            без него отклонённую запись невозможно отыскать и исправить;
        values: значения полей доменной модели;
        extra: сведения для проверок, не попадающие в базу;
        payload: исходная запись — сохраняется в карантине при отклонении.
    """

    key: str
    position: str
    values: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)
    payload: Any = None


@dataclass
class Extract:
    """Выгрузка, полученная от источника."""

    records: Iterable[Any]
    count: int = 0
    fetched_at: datetime | None = None
    from_cache: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Reject:
    """Отклонённая запись — то, что откладывается в карантин."""

    position: str
    key: str
    code: str
    message: str
    payload: str = ""


@dataclass
class RunReport:
    """Итог прохождения конвейера."""

    pipeline: str
    title: str
    target_table: str
    fetched: int = 0
    filtered: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    removed: int = 0
    from_cache: bool = False
    fetched_at: datetime | None = None
    notes: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)
    #: Ключи записей, появившихся в этой загрузке. По ним конвейер, которому
    #: это нужно, находит новые записи и отличает их от подтверждённых
    #: источником повторно.
    created_keys: list[str] = field(default_factory=list)
    by_check: Counter = field(default_factory=Counter)
    by_rule: Counter = field(default_factory=Counter)
    #: Промежуточные сведения одного запуска: приведение накапливает здесь то,
    #: что понадобится проверке набора целиком. Хранить это в самом конвейере
    #: нельзя — конвейеры создаются один раз на весь срок работы приложения.
    state: dict[str, Any] = field(default_factory=dict)
    run_id: int | None = None
    status: str = EtlStatus.RUNNING

    @property
    def written(self) -> int:
        """Записей, попавших в базу: созданных и обновлённых."""
        return self.created + self.updated

    @property
    def processed(self) -> int:
        """Записей, прошедших конвейер до конца, включая неизменившиеся."""
        return self.written + self.unchanged

    def note(self, text: str) -> None:
        """Замечание к загрузке: итог получает пометку «с замечаниями»."""
        self.notes.append(text)

    def detail(self, text: str) -> None:
        """Сведение о ходе загрузки, не являющееся замечанием."""
        self.details.append(text)

    def skip(self, rule: str) -> None:
        """Учесть элемент выгрузки, не относящийся к предметной области.

        Отбор и проверка качества — разные вещи. Выгрузка намеренно шире
        реестра: в неё попадают объекты, которые системе не нужны, и их
        отсев дефектом источника не является. В карантин такие элементы
        не откладываются, но их доля видна в сводке — по ней оценивается
        точность запроса к источнику.
        """
        self.filtered += 1
        self.by_rule[rule] += 1

    def summary(self) -> str:
        return (
            f"{self.title}: получено {self.fetched}, создано {self.created}, "
            f"обновлено {self.updated}, без изменений {self.unchanged}, "
            f"отклонено {self.rejected}, отсеяно {self.filtered}, "
            f"удалено {self.removed}"
        )


# ---------------------------------------------------------------------------
#  Конвейер
# ---------------------------------------------------------------------------


class Pipeline:
    """Порядок загрузки одного набора данных.

    Наследник обязан объявить обозначение набора и описать получение
    и приведение; остальное — проверки, запись, журнал — выполняется одинаково
    для всех источников.
    """

    #: Обозначение конвейера в реестре: «osm.objects», «rosstat.freight».
    name: str = ""
    #: Наименование набора для пользователя.
    title: str = ""
    #: Таблица, которую наполняет конвейер.
    target_table: str = ""
    #: Код источника в справочнике системы.
    source_code: str = ""
    #: Пояснение: откуда берутся данные и что именно попадает в реестр.
    description: str = ""
    #: Проверки качества, применяемые к каждому кандидату.
    checks: tuple[Check, ...] = ()
    #: Регламентная периодичность обновления (``UpdateFrequency``).
    frequency: str = ""
    #: Поддерживает ли набор приведение реестра к составу источника.
    supports_prune: bool = False
    #: Допустим ли запуск из панели администратора. Наборы, требующие
    #: обращения к внешней службе на десятки минут, запускаются регламентом.
    console_enabled: bool = True
    #: Ожидает ли конвейер файл, присланный пользователем.
    expects_upload: bool = False

    # ------------------------------------------------------------- источник

    def ensure_source(self) -> DataSource:
        """Справочная запись об источнике.

        Источник объявляется в справочнике только вместе с работающим
        конвейером: запись означает, что данные оттуда действительно
        поступают, а не что такая возможность предполагается.
        """
        raise NotImplementedError

    # -------------------------------------------------------------- этапы

    def extract(self, context: Context) -> Extract:
        """Получить выгрузку от источника."""
        raise NotImplementedError

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        """Привести выгрузку к записям доменной модели.

        Здесь же выполняется отбор: элементы, не относящиеся к предметной
        области, не порождают кандидатов и учитываются :meth:`RunReport.skip`.
        """
        raise NotImplementedError

    def write(self, candidate: Candidate, context: Context) -> Outcome:
        """Записать кандидата, обновив существующую запись по ключу."""
        raise NotImplementedError

    def prune(self, seen: set[str], context: Context) -> int:
        """Удалить записи, отсутствующие в текущей выгрузке."""
        return 0

    def verify(self, report: RunReport, context: Context) -> None:
        """Проверки, возможные только после записи всего набора.

        Отдельная запись бывает безупречной, а набор целиком — нет: зоны
        обязаны быть вложены одна в другую, ряд статистики — не иметь
        пропусков в середине. Такие требования проверяются здесь.
        """

    def inspect(self, candidate: Candidate) -> Violation | None:
        """Применить проверки к кандидату; вернуть первое нарушение."""
        for check in self.checks:
            violation = check.inspect(candidate)
            if violation is not None:
                return violation
        return None


class ModelPipeline(Pipeline):
    """Конвейер, наполняющий одну таблицу доменной модели.

    Берёт на себя запись: отыскивает существующую запись по ключу, сравнивает
    её содержимое с поступившим и сохраняет только при расхождении. Поля,
    меняющиеся при каждом обращении к источнику (время выгрузки), в сравнении
    не участвуют — иначе каждая загрузка выглядела бы полным обновлением
    реестра, и признак «в источнике ничего не изменилось» пропал бы.
    """

    #: Модель, наполняемая конвейером.
    model: type[models.Model] | None = None
    #: Поля, не участвующие в сравнении: их значение меняется само по себе.
    volatile_fields: tuple[str, ...] = ("source_updated_at",)

    def lookup(self, candidate: Candidate) -> dict[str, Any]:
        """Поля, по которым запись отыскивается в базе."""
        raise NotImplementedError

    def write(self, candidate: Candidate, context: Context) -> Outcome:
        assert self.model is not None
        existing = self.model.objects.filter(**self.lookup(candidate)).first()

        if existing is None:
            if not context.dry_run:
                self.model.objects.create(**{**self.lookup(candidate), **candidate.values})
            return Outcome.CREATED

        changed = [
            name
            for name, value in candidate.values.items()
            if name not in self.volatile_fields
            and not same_value(getattr(existing, name, None), value)
        ]
        if not changed:
            # Запись не изменилась, но отметка о времени выгрузки обновляется:
            # по ней видно, что сведения подтверждены источником сегодня.
            volatile = [name for name in self.volatile_fields if name in candidate.values]
            if volatile and not context.dry_run:
                for name in volatile:
                    setattr(existing, name, candidate.values[name])
                existing.save(update_fields=volatile)
            return Outcome.UNCHANGED

        if not context.dry_run:
            for name, value in candidate.values.items():
                setattr(existing, name, value)
            existing.save(update_fields=list(candidate.values))
        return Outcome.UPDATED


def same_value(current: Any, incoming: Any) -> bool:
    """Совпадают ли хранимое и поступившее значения.

    Сравнение ведётся по смыслу, а не побайтно: геометрия сопоставляется
    в текстовом представлении с той же точностью, с какой она хранится,
    числа — как числа, ссылки на справочники — по идентификатору. Иначе
    неизменившаяся запись каждый раз выглядела бы обновлённой.
    """
    if current is None or incoming is None:
        return current is incoming

    if isinstance(current, Geometry) or isinstance(incoming, Geometry):
        if not (isinstance(current, Geometry) and isinstance(incoming, Geometry)):
            return False
        return current.wkt == incoming.wkt

    if isinstance(current, models.Model) or isinstance(incoming, models.Model):
        return _pk(current) == _pk(incoming)

    if isinstance(current, Decimal | float | int) and isinstance(incoming, Decimal | float | int):
        if isinstance(current, bool) or isinstance(incoming, bool):
            return bool(current) is bool(incoming)
        return Decimal(str(current)) == Decimal(str(incoming))

    return current == incoming


def _pk(value: Any) -> Any:
    return value.pk if isinstance(value, models.Model) else value


# ---------------------------------------------------------------------------
#  Выполнение
# ---------------------------------------------------------------------------


def run(pipeline: Pipeline, context: Context | None = None) -> RunReport:
    """Провести набор данных через конвейер и записать итог в журнал.

    Данные пишутся в одной транзакции: прерванная загрузка не оставляет
    наполовину обновлённый реестр. Журнал и карантин ведутся вне транзакции,
    иначе откат стёр бы и запись о самой неудаче.
    """
    context = context or Context()
    source = pipeline.ensure_source()
    entry = EtlRun.objects.create(
        source=source,
        pipeline=pipeline.name,
        target_table=pipeline.target_table,
        trigger=context.trigger,
        actor=context.actor if getattr(context.actor, "pk", None) else None,
        parameters=context.describe(),
        status=EtlStatus.RUNNING,
        started_at=timezone.now(),
    )
    report = RunReport(
        pipeline=pipeline.name,
        title=pipeline.title,
        target_table=pipeline.target_table,
        run_id=entry.pk,
    )

    try:
        _execute(pipeline, context, report)
    except Exception as exc:  # noqa: BLE001 — итог любой неудачи попадает в журнал
        report.status = EtlStatus.FAILED
        report.note(str(exc))
        _journal(entry, report, context)
        _announce(report, pipeline, context)
        logger.exception("Загрузка «%s» прервана", pipeline.title)
        raise PipelineError(str(exc)) from exc

    report.status = _status(report)
    _journal(entry, report, context)
    _announce(report, pipeline, context)
    if report.created or report.updated or report.removed:
        # Сводки и тайлы карты собраны по прежнему составу данных: загрузка,
        # что-либо изменившая, делает их недействительными.
        selectors.invalidate_caches()
    logger.info("%s", report.summary())
    return report


def _announce(report: RunReport, pipeline: Pipeline, context: Context) -> None:
    """Сообщить о неблагополучном итоге загрузки тем, кто с ним работает.

    Отказ и карантин — состояния, требующие вмешательства человека, и ждать,
    пока кто-нибудь откроет журнал загрузок, они не должны. Пробный проход
    ничего не меняет и потому никого не оповещает.
    """
    if context.dry_run:
        return
    if report.status == EtlStatus.FAILED:
        notify.load_failed(report, source_title=pipeline.title)
    elif report.rejected:
        notify.quarantined(report, source_title=pipeline.title)


def _execute(pipeline: Pipeline, context: Context, report: RunReport) -> None:
    """Получение, приведение, проверка и запись набора."""
    extract = pipeline.extract(context)
    report.fetched = extract.count
    report.from_cache = extract.from_cache
    report.fetched_at = extract.fetched_at
    report.notes.extend(extract.notes)

    seen: set[str] = set()
    with transaction.atomic():
        for candidate in pipeline.prepare(extract, context, report):
            violation = pipeline.inspect(candidate)
            if violation is not None:
                _reject(report, candidate, violation)
                continue

            outcome = pipeline.write(candidate, context)
            if outcome is Outcome.CREATED:
                report.created += 1
                report.created_keys.append(candidate.key)
            elif outcome is Outcome.UPDATED:
                report.updated += 1
            else:
                report.unchanged += 1
            seen.add(candidate.key)

        if context.prune and pipeline.supports_prune and not context.dry_run:
            report.removed = pipeline.prune(seen, context)

        pipeline.verify(report, context)


def _reject(report: RunReport, candidate: Candidate, violation: Violation) -> None:
    """Отложить запись в карантин."""
    report.rejected += 1
    report.by_check[violation.code] += 1
    if len(report.rejects) >= quarantine_limit():
        return
    report.rejects.append(
        Reject(
            position=candidate.position[:120],
            key=candidate.key[:200],
            code=violation.code[:64],
            message=violation.message[:500],
            payload=_payload(candidate),
        )
    )


def _payload(candidate: Candidate) -> str:
    """Исходная запись в виде, пригодном для разбора оператором."""
    data = candidate.payload if candidate.payload is not None else candidate.values
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:4000]
    except (TypeError, ValueError):
        return str(data)[:4000]


def _status(report: RunReport) -> str:
    """Итог загрузки по её счётчикам.

    Загрузка считается неудачной, если из непустой выгрузки не получилось
    ни одной записи. Отсутствие изменений неудачей не является: у исправного
    источника это обычное состояние повторного запуска.
    """
    if report.fetched and not report.processed:
        return EtlStatus.FAILED
    if report.rejected or report.notes:
        return EtlStatus.PARTIAL
    return EtlStatus.SUCCESS


def _journal(entry: EtlRun, report: RunReport, context: Context) -> None:
    """Записать итог в журнал и отложить отклонённые записи в карантин."""
    entry.status = report.status
    entry.finished_at = timezone.now()
    entry.records_created = report.created
    entry.records_updated = report.updated
    entry.records_unchanged = report.unchanged
    entry.records_removed = report.removed
    entry.records_loaded = report.written
    entry.records_errors = report.rejected
    entry.error_message = "; ".join(report.notes[:5])
    entry.save()

    if report.rejects and not context.dry_run:
        # Запись, уже стоящая в очереди по той же проверке, повторно
        # не откладывается: регламентная загрузка идёт по расписанию, и
        # неисправленный источник за месяц удлинил бы очередь тридцатью
        # копиями одного и того же. Полное число отклонений при этом
        # сохраняется в журнале каждого запуска.
        queued = set(
            EtlReject.objects.filter(
                run__pipeline=entry.pipeline, reviewed_at__isnull=True
            ).values_list("check_code", "record_key")
        )
        fresh = [
            EtlReject(
                run=entry,
                position=item.position,
                record_key=item.key,
                check_code=item.code,
                message=item.message,
                payload=item.payload,
            )
            for item in report.rejects
            if (item.code, item.key) not in queued
        ]
        EtlReject.objects.bulk_create(fresh)
        logger.info(
            "Карантин: отложено %d записей из %d отклонённых",
            len(fresh), report.rejected,
        )


def quarantine_limit() -> int:
    """Предел карантина, с возможностью изменить его настройкой."""
    return int(getattr(settings, "ETL_QUARANTINE_LIMIT", QUARANTINE_LIMIT))


__all__ = [
    "Candidate",
    "Context",
    "Extract",
    "ModelPipeline",
    "Outcome",
    "Pipeline",
    "PipelineError",
    "RunReport",
    "run",
    "same_value",
]
