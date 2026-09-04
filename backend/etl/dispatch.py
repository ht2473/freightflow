"""Передача загрузки на исполнение.

Загрузку начинают из трёх мест: командной строки, панели администратора
и регламента. Из командной строки она выполняется на месте — там ждать
уместно. Из панели ждать нельзя: выгрузка внешней службы идёт минутами,
и запрос пользователя столько висеть не должен.

Поэтому панель обращается сюда. Если очередь настроена, задача уходит
исполнителю и страница отвечает сразу. Если очереди нет — а такой контур
допустим и для разработки, и для установки, где регламент ведёт системный
таймер, — загрузка выполняется на месте, и пользователю об этом сообщается.

Что именно произошло, видно из :class:`Submission`: подменять один способ
другим молча нельзя, иначе администратор не поймёт, отчего страница то
отвечает мгновенно, то думает полторы минуты.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.choices import EtlTrigger
from django.conf import settings

from . import registry
from .pipeline import Context, Pipeline, RunReport, run

logger = logging.getLogger("freightflow.etl")


@dataclass(frozen=True)
class Submission:
    """Итог передачи загрузки на исполнение."""

    pipeline: Pipeline
    #: ``worker`` — задача передана исполнителю, ``inline`` — выполнена сразу.
    mode: str
    task_id: str | None = None
    report: RunReport | None = None

    @property
    def deferred(self) -> bool:
        return self.mode == "worker"


def queue_configured() -> bool:
    """Настроена ли очередь задач."""
    return bool(getattr(settings, "CELERY_BROKER_URL", ""))


def submit(name: str, *, trigger: str = EtlTrigger.CONSOLE, actor: Any = None,
           inline: bool = False, **options) -> Submission:
    """Начать загрузку набора данных.

    Возвращает описание того, как загрузка была начата. Исключения конвейера
    при выполнении на месте не подавляются: администратору важно увидеть
    причину отказа сразу, а не разыскивать её в журнале.

    Ключ ``inline`` требует выполнения на месте независимо от очереди. Он нужен
    загрузке присланного файла: содержимое передаётся вместе с запросом,
    через очередь не проходит, а разбор таблицы занимает доли секунды —
    ждать его уместно.
    """
    pipeline = registry.get(name)

    if queue_configured() and not inline:
        from .tasks import run_pipeline

        result = run_pipeline.delay(
            name=name,
            trigger=trigger,
            actor_id=getattr(actor, "pk", None),
            **options,
        )
        logger.info("Загрузка «%s» передана исполнителю (%s)", pipeline.title, result.id)
        return Submission(pipeline=pipeline, mode="worker", task_id=result.id)

    context = Context(
        refresh=bool(options.pop("refresh", False)),
        offline=bool(options.pop("offline", False)),
        prune=bool(options.pop("prune", False)),
        trigger=trigger,
        actor=actor,
        options=options,
    )
    report = run(pipeline, context)
    return Submission(pipeline=pipeline, mode="inline", report=report)


__all__ = ["Submission", "queue_configured", "submit"]
