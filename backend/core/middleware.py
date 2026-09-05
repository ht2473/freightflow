"""Промежуточные обработчики запросов.

Модуль содержит инфраструктурные обработчики, не привязанные к предметной
области: сквозной идентификатор запроса и измерение времени ответа.
"""

from __future__ import annotations

import time
import uuid

from .logging_filters import current_request_id

# Заголовок, по которому идентификатор запроса можно передать снаружи —
# например, из обратного прокси или системы трассировки.
REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware:
    """Присвоить каждому запросу идентификатор и измерить время обработки.

    Идентификатор попадает в журнал, в заголовок ответа и в контекст шаблона,
    что упрощает разбор обращений пользователей: по номеру из подвала страницы
    администратор находит все связанные записи журнала.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex[:12]
        request.request_id = request_id
        token = current_request_id.set(request_id)

        started = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            current_request_id.reset(token)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response[REQUEST_ID_HEADER] = request_id
        # Заголовок используется в разделе «Состояние системы» и при
        # нагрузочном тестировании для оценки серверного времени ответа.
        response["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        request.elapsed_ms = elapsed_ms
        return response
