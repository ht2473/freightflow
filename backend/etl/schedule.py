"""Регламент обновления данных.

Периодичность объявляет сам конвейер — она определяется тем, как часто
меняется источник. Разметка OpenStreetMap правится непрерывно, но реестру
достаточно недельного шага; зоны ограничения меняются с нормативным актом,
то есть раз в несколько лет, и месячная сверка их заведомо покрывает; ряды
Росстата выходят помесячно с задержкой в полтора месяца.

Здесь периодичность превращается в две вещи: расписание для планировщика
и ответ на вопрос «что пора обновить». Второе нужно и без планировщика —
регламент можно вести и обычным системным таймером, вызывающим
``manage.py etl --due``.

Срок отсчитывается от последней **успешной** загрузки набора. Неудачная
попытка срок не сдвигает: иначе сломавшийся источник считался бы обновлённым.
"""

from __future__ import annotations

from datetime import timedelta

from core.choices import EtlStatus, UpdateFrequency
from core.models import EtlRun
from django.utils import timezone

from . import registry
from .pipeline import Pipeline

#: Периодичность обновления → промежуток между запусками.
INTERVALS: dict[str, timedelta] = {
    UpdateFrequency.HOURLY: timedelta(hours=1),
    UpdateFrequency.DAILY: timedelta(days=1),
    UpdateFrequency.WEEKLY: timedelta(days=7),
    UpdateFrequency.MONTHLY: timedelta(days=30),
    UpdateFrequency.QUARTERLY: timedelta(days=91),
}

#: Расписание планировщика: периодичность → час и день запуска.
#: Загрузки разнесены по времени суток — ночью общедоступные службы отвечают
#: быстрее, а совпадение нескольких тяжёлых выгрузок в одну минуту создаёт
#: пик нагрузки на стороне источника.
CRONTABS: dict[str, dict[str, str]] = {
    UpdateFrequency.HOURLY: {"minute": "0"},
    UpdateFrequency.DAILY: {"minute": "20", "hour": "3"},
    UpdateFrequency.WEEKLY: {"minute": "40", "hour": "3", "day_of_week": "1"},
    UpdateFrequency.MONTHLY: {"minute": "0", "hour": "4", "day_of_month": "2"},
    UpdateFrequency.QUARTERLY: {"minute": "30", "hour": "4", "day_of_month": "3",
                                "month_of_year": "1,4,7,10"},
}

#: Человеческие подписи периодичности — для панели администратора.
LABELS: dict[str, str] = {
    UpdateFrequency.HOURLY: "ежечасно",
    UpdateFrequency.DAILY: "ежедневно",
    UpdateFrequency.WEEKLY: "еженедельно",
    UpdateFrequency.MONTHLY: "ежемесячно",
    UpdateFrequency.QUARTERLY: "ежеквартально",
}


def interval(pipeline: Pipeline) -> timedelta | None:
    """Промежуток между регламентными запусками конвейера."""
    return INTERVALS.get(pipeline.frequency)


def last_success(pipeline: Pipeline):
    """Время последней успешной загрузки набора."""
    return (
        EtlRun.objects.filter(
            pipeline=pipeline.name,
            status__in=(EtlStatus.SUCCESS, EtlStatus.PARTIAL),
        )
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first()
    )


def is_due(pipeline: Pipeline, now=None) -> bool:
    """Подошёл ли регламентный срок обновления набора.

    Набор без объявленной периодичности регламенту не подчиняется: его
    запускает человек. Набор, ни разу не загружавшийся, подлежит загрузке
    немедленно.
    """
    step = interval(pipeline)
    if step is None:
        return False
    previous = last_success(pipeline)
    if previous is None:
        return True
    return (now or timezone.now()) - previous >= step


def due(now=None) -> list[Pipeline]:
    """Наборы, у которых подошёл регламентный срок обновления."""
    return [item for item in registry.scheduled() if is_due(item, now)]


def describe(pipeline: Pipeline) -> str:
    """Регламент словами: периодичность и время последней загрузки."""
    label = LABELS.get(pipeline.frequency, pipeline.frequency or "по требованию")
    previous = last_success(pipeline)
    if previous is None:
        return f"{label}; загрузка ещё не выполнялась"
    return f"{label}; последняя загрузка {timezone.localtime(previous):%d.%m.%Y %H:%M}"


def beat_schedule() -> dict[str, dict]:
    """Расписание для планировщика Celery.

    Собирается из реестра конвейеров, а не задаётся отдельным списком:
    иначе объявленный конвейер мог бы остаться без регламента, а расписание —
    ссылаться на удалённый набор.
    """
    entries: dict[str, dict] = {}
    for pipeline in registry.scheduled():
        crontab = CRONTABS.get(pipeline.frequency)
        if not crontab:
            continue
        entries[f"etl:{pipeline.name}"] = {
            "task": "etl.run_pipeline",
            "schedule": crontab,
            "kwargs": {"name": pipeline.name, "trigger": "schedule"},
        }
    return entries


__all__ = ["CRONTABS", "INTERVALS", "beat_schedule", "describe", "due", "is_due"]
