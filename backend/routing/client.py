"""Обращение к службе маршрутизации.

Расчёт маршрута и зон доступности ведёт Valhalla — маршрутизатор, работающий
на графе дорог OpenStreetMap и умеющий учитывать габариты и массу
транспортного средства. Служба разворачивается рядом с приложением
(`docker-compose.yml`, служба ``router``), поэтому обращения к ней не выходят
за пределы контура.

Адрес службы задаётся настройкой ``FF_VALHALLA_URL``. Пока она не задана,
система об этом прямо сообщает и расчётов не показывает: изображать маршрут
по прямой линии, выдавая его за проложенный по дорогам, недопустимо.

Ответы кешируются. Граф дорог обновляется вместе с выгрузкой OpenStreetMap,
то есть не чаще раза в неделю, поэтому повторный запрос той же изохроны
пересчитывать нечего.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("freightflow.routing")

#: Заголовок, по которому служба различает обращения приложения.
USER_AGENT = "FreightFlow/1.0 (+routing)"


class RoutingError(RuntimeError):
    """Расчёт выполнить не удалось."""


class RouterNotConfiguredError(RoutingError):
    """Адрес службы маршрутизации не задан."""


class RouterUnavailableError(RoutingError):
    """Служба маршрутизации не отвечает либо отказала в расчёте."""


def base_url() -> str:
    """Адрес службы маршрутизации без завершающей косой черты."""
    return (settings.VALHALLA_URL or "").rstrip("/")


def is_configured() -> bool:
    """Задан ли адрес службы маршрутизации."""
    return bool(base_url())


class RoutingClient:
    """Клиент службы маршрутизации.

    Запросы выполняются методом POST с телом JSON. Ответ кешируется по
    отпечатку запроса: одинаковые обращения — а на карте они одинаковы,
    пока пользователь не сменил точку или профиль, — обслуживаются из кеша.
    """

    def __init__(self, url: str | None = None, timeout: int | None = None,
                 cache_ttl: int | None = None) -> None:
        self.url = (url or base_url()).rstrip("/")
        self.timeout = timeout or settings.VALHALLA_TIMEOUT
        self.cache_ttl = settings.VALHALLA_CACHE_TTL if cache_ttl is None else cache_ttl

    # ------------------------------------------------------------------ вызовы

    def isochrone(self, payload: dict) -> dict:
        """Зоны достижимости за заданное время."""
        return self.call("isochrone", payload)

    def route(self, payload: dict) -> dict:
        """Маршрут между точками."""
        return self.call("route", payload)

    def status(self) -> dict:
        """Состояние службы: версия и охват графа."""
        return self.call("status", {"verbose": True})

    # ------------------------------------------------------------------ обмен

    def call(self, endpoint: str, payload: dict) -> dict:
        """Выполнить обращение к службе, обслужив его из кеша при совпадении."""
        if not self.url:
            raise RouterNotConfiguredError(
                "Адрес службы маршрутизации не задан: расчёт по графу дорог недоступен"
            )

        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        key = f"routing:{endpoint}:{hashlib.sha256(body.encode()).hexdigest()[:32]}"

        cached = cache.get(key)
        if cached is not None:
            return cached

        result = self._request(endpoint, body)
        if self.cache_ttl:
            cache.set(key, result, self.cache_ttl)
        return result

    def _request(self, endpoint: str, body: str) -> dict[str, Any]:
        """Отправить запрос и разобрать ответ."""
        request = urllib.request.Request(  # noqa: S310 — адрес задан настройкой
            f"{self.url}/{endpoint}",
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RouterUnavailableError(_explain(error)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            logger.warning("Служба маршрутизации недоступна: %s", error)
            raise RouterUnavailableError(
                "Служба маршрутизации не отвечает"
            ) from error
        except json.JSONDecodeError as error:
            raise RouterUnavailableError("Ответ службы маршрутизации не разобран") from error


def _explain(error: urllib.error.HTTPError) -> str:
    """Пояснение отказа службы.

    Отказ маршрутизатора содержателен: «точка не привязана к дороге» и
    «маршрут не найден» — разные обстоятельства, и пользователю нужно
    сообщить, какое из них наступило, а не общий код ошибки.
    """
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (ValueError, OSError):
        payload = {}

    message = payload.get("error") or ""
    if message:
        return str(message)
    return f"Служба маршрутизации отказала в расчёте (код {error.code})"


__all__ = [
    "RouterNotConfiguredError",
    "RouterUnavailableError",
    "RoutingClient",
    "RoutingError",
    "base_url",
    "is_configured",
]
