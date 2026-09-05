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



#: Политика безопасности содержимого.
#:
#: Система обслуживается собственным доменом целиком: шрифты, библиотека
#: карты, тайлы и подложка приходят с неё же. Политика превращает это правило
#: из договорённости в ограничение, которое проверяет браузер: обращение
#: к чужому домену, попавшее в разметку по недосмотру, не выполнится.
#:
#: Разрешения, выходящие за пределы собственного домена, объявлены поимённо:
#:
#: * ``style-src`` допускает разметочные объявления: ширина полосы доли
#:   и предельная ширина столбца задаются атрибутом ``style`` у элемента;
#: * ``img-src`` допускает ``data:`` — значки и знаки карты приходят
#:   вложенными в таблицу стилей библиотеки;
#: * ``worker-src`` допускает ``blob:`` — разбор векторных тайлов библиотека
#:   карты ведёт в фоновом потоке, который создаёт из собственного кода.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "child-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))


class ContentSecurityPolicyMiddleware:
    """Объявить политику безопасности содержимого.

    Заголовок ставит приложение, а не обратный прокси: статику раздаёт сам
    контейнер, и политика должна действовать при любом способе развёртывания.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response
