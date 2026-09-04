"""Задачи исполнителя: загрузка данных вне веб-процесса.

Задача — тонкая обёртка над конвейером: она не знает ни о выгрузках,
ни о проверках, а только передаёт обозначение набора и условия запуска.
Итог возвращается сводкой, а подробности остаются в журнале загрузок —
исполнитель не единственный, кто запускает конвейеры, и место хранения
итогов у всех должно быть одно.
"""

from __future__ import annotations

import logging

from celery import shared_task
from core.choices import EtlTrigger

logger = logging.getLogger("freightflow.etl")


@shared_task(name="etl.run_pipeline")
def run_pipeline(name: str, trigger: str = EtlTrigger.SCHEDULE,
                 actor_id: int | None = None, **options) -> dict:
    """Провести набор данных через конвейер.

    Аргументы соответствуют ключам командной строки: ``refresh``, ``prune``
    и прочие передаются как есть.
    """
    from django.contrib.auth.models import User

    from . import registry
    from .pipeline import Context, run

    pipeline = registry.get(name)
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    context = Context(
        refresh=bool(options.pop("refresh", False)),
        offline=bool(options.pop("offline", False)),
        prune=bool(options.pop("prune", False)),
        trigger=trigger,
        actor=actor,
        options=options,
    )
    report = run(pipeline, context)
    return {
        "pipeline": report.pipeline,
        "run_id": report.run_id,
        "status": report.status,
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "rejected": report.rejected,
        "removed": report.removed,
    }


@shared_task(name="etl.run_due")
def run_due() -> list[dict]:
    """Провести все наборы, у которых подошёл регламентный срок.

    Задача нужна установке без планировщика: системный таймер вызывает её
    раз в час, а решение о том, что именно пора обновлять, принимается
    по журналу последних успешных загрузок.
    """
    from . import schedule

    return [run_pipeline(item.name) for item in schedule.due()]
