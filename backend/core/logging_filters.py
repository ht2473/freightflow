"""Фильтры журналирования.

Идентификатор запроса генерируется middleware и хранится в контекстной
переменной; фильтр переносит его в запись журнала. Это позволяет собрать все
сообщения, относящиеся к одному HTTP-запросу, даже когда они порождены
разными модулями системы.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

# Контекстная переменная безопасна при работе нескольких потоков и корутин:
# каждый запрос получает собственное значение.
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Добавить в запись журнала поле ``request_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True
