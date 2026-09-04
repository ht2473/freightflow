"""Планировщик регламентных задач.

Регламентная загрузка данных не может выполняться в веб-процессе: выгрузка
Overpass идёт минутами, а запрос пользователя столько ждать не должен. Задачи
выносятся в отдельный процесс-исполнитель, а расписание ведёт планировщик.

Расписание собирается из реестра конвейеров, а не задаётся списком: иначе
объявленный набор данных мог бы остаться без регламента, а расписание —
ссылаться на удалённый набор.

Очередь необязательна. При пустой ``FF_CELERY_BROKER_URL`` приложение работает
без исполнителя: загрузка выполняется на месте, а панель администратора
сообщает об этом. Такой контур пригоден для разработки и для установки,
где регламент ведёт системный таймер.
"""

from __future__ import annotations

import logging
import os

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger("freightflow.etl")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("freightflow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_finalize.connect
def register_schedule(sender=None, **kwargs) -> None:
    """Наполнить расписание планировщика по реестру конвейеров."""
    from etl import schedule

    entries = {}
    for name, item in schedule.beat_schedule().items():
        entries[name] = {
            "task": item["task"],
            "schedule": crontab(**item["schedule"]),
            "kwargs": item["kwargs"],
        }
    sender.conf.beat_schedule = entries
    logger.info("Регламент загрузки данных: заданий %d", len(entries))
