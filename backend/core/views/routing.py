"""Расчёты по графу дорог: зоны доступности и маршруты.

Конечные точки обслуживают инструменты карты. Отказ службы маршрутизации
передаётся клиенту как есть — с кодом состояния и объяснением, — чтобы
интерфейс мог сказать, что расчёт не выполнен, вместо того чтобы показать
правдоподобную линию, проложенную неизвестно как.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET
from routing import profiles, service
from routing.client import RouterNotConfiguredError, RoutingError

#: Код состояния для отказа из-за ненастроенной службы. Отличается от отказа
#: самой службы: в первом случае контур развёрнут не полностью, во втором
#: расчёт не удался.
NOT_CONFIGURED_STATUS = 501
UNAVAILABLE_STATUS = 503


def _point(request, name: str) -> tuple[float, float] | None:
    """Разобрать параметр вида ``lon,lat``."""
    raw = (request.GET.get(name) or "").strip()
    if not raw:
        return None
    try:
        lon, lat = (float(part) for part in raw.split(","))
    except ValueError:
        return None
    return lon, lat


def _minutes(request) -> list[int] | None:
    """Разобрать перечень интервалов доступности."""
    raw = (request.GET.get("minutes") or "").strip()
    if not raw:
        return None
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        return None


def _failure(error: RoutingError) -> JsonResponse:
    """Ответ об отказе расчёта."""
    configured = not isinstance(error, RouterNotConfiguredError)
    return JsonResponse(
        {"configured": configured, "error": str(error)},
        status=UNAVAILABLE_STATUS if configured else NOT_CONFIGURED_STATUS,
    )


@require_GET
def isochrones(request) -> JsonResponse:
    """Зоны доступности от точки за заданное время хода."""
    origin = _point(request, "point")
    if origin is None:
        return JsonResponse({"error": _("Не указана точка расчёта")}, status=400)

    minutes = _minutes(request)
    if minutes is not None and not minutes:
        return JsonResponse({"error": _("Не разобран перечень интервалов")}, status=400)

    profile = profiles.get(request.GET.get("profile"))
    try:
        contours = service.isochrones(origin[0], origin[1], minutes, profile)
    except RoutingError as error:
        return _failure(error)

    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": [item.as_feature() for item in contours],
            "profile": {"code": profile.code, "title": str(profile.title)},
            "origin": {"lon": origin[0], "lat": origin[1]},
        }
    )


@require_GET
def route(request) -> JsonResponse:
    """Маршрут между двумя точками с условиями проезда."""
    origin = _point(request, "from")
    destination = _point(request, "to")
    if origin is None or destination is None:
        return JsonResponse({"error": _("Не указаны начало и конец маршрута")}, status=400)

    profile = profiles.get(request.GET.get("profile"))
    try:
        result = service.route([origin, destination], profile)
    except RoutingError as error:
        return _failure(error)

    payload = result.as_payload()
    payload["profile"] = {
        "code": profile.code,
        "title": str(profile.title),
        "mass_tons": float(profile.mass_tons),
    }
    return JsonResponse(payload)


@require_GET
def router_status(request) -> JsonResponse:
    """Состояние службы маршрутизации."""
    return JsonResponse(service.availability())
