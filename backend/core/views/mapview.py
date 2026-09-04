"""Интерактивная карта.

Страница отдаёт каркас и настройки, а содержимое приходит векторными
тайлами: клиент получает данные того квадрата сетки, который видит, и с той
подробностью, которая различима на его масштабе. Включение слоёв, отбор по
округу и смена показателя раскраски выполняются на уже полученном тайле,
без обращения к серверу.
"""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from .. import selectors
from ..models import District, InfrastructureType
from ..tilelayers import MAX_ZOOM, MIN_ZOOM
from .base import int_param, page_context

#: Наибольший масштаб, до которого карта увеличивается. Тайлы собираются
#: до шестнадцатого; дальше клиент растягивает последний, и это верно —
#: подробнее исходных данных карта всё равно не станет.
MAP_MAX_ZOOM = 18

#: Запас вокруг города, за который карта не выпускает вид. Без него
#: пользователь уводит карту в пустой океан и не понимает, куда вернуться.
BOUNDS_MARGIN = 0.35


def choropleth_metrics(profiles: list[dict], index: list[dict]) -> list[dict]:
    """Показатели, по которым раскрашиваются округа.

    Верхняя граница шкалы берётся из самих данных, а не назначается: она
    определяет, что считать насыщенным цветом, и при другом составе округов
    должна быть другой. Показатель, не измеренный ни по одному округу,
    в перечень не попадает — раскрашивать по нему нечего.
    """
    scores = [row["score"] for row in index if row.get("score") is not None]
    congestion = [
        float(row["congestion"]) for row in profiles if row.get("congestion") is not None
    ]
    counts = [row["object_count"] for row in profiles if row.get("object_count")]

    candidates = [
        {
            "key": "index",
            "title": _("Индекс логистической нагрузки"),
            "property": "index",
            "unit": _("баллов"),
            "max": max(scores) if scores else 0,
        },
        {
            "key": "congestion",
            "title": _("Загруженность сети"),
            "property": "congestion",
            "unit": _("баллов"),
            "max": max(congestion) if congestion else 0,
        },
        {
            "key": "objects",
            "title": _("Число объектов"),
            "property": "objects",
            "unit": _("объектов"),
            "max": max(counts) if counts else 0,
        },
    ]
    return [item for item in candidates if item["max"]]


def map_settings(metrics: list[dict]) -> dict:
    """Собрать настройки карты для передачи в клиентский сценарий.

    Словарь формируется здесь, а не в разметке. Подстановка чисел в шаблон
    проходит через локализацию: при русском языке координата ``55.7522``
    выводится как ``55,7522``, и встроенный в страницу JSON перестаёт быть
    валидным. Значения, собранные на стороне Python и переданные через
    ``json_script``, сериализуются средствами Python и локализации
    не подчиняются.
    """
    min_lon, min_lat, max_lon, max_lat = settings.MAP_CITY_BOUNDS
    districts = [
        {
            "name": district.name,
            "short_name": district.short_name,
            "lon": center[0],
            "lat": center[1],
        }
        for district in District.objects.with_geometry()
        if (center := district.map_center) is not None
    ]

    return {
        "center": [settings.MAP_DEFAULT_CENTER[0], settings.MAP_DEFAULT_CENTER[1]],
        "zoom": settings.MAP_DEFAULT_ZOOM,
        "minZoom": MIN_ZOOM,
        "maxZoom": MAP_MAX_ZOOM,
        "sourceMaxZoom": MAX_ZOOM,
        "maxBounds": [
            [min_lon - BOUNDS_MARGIN, min_lat - BOUNDS_MARGIN],
            [max_lon + BOUNDS_MARGIN, max_lat + BOUNDS_MARGIN],
        ],
        "attribution": settings.MAP_ATTRIBUTION,
        "districts": districts,
        "choropleth": [
            {
                "key": item["key"],
                "title": str(item["title"]),
                "property": item["property"],
                "unit": str(item["unit"]),
                "max": float(item["max"]),
            }
            for item in metrics
        ],
        "urls": {
            "tilejson": reverse("core:map_tilejson"),
            "nearby": reverse("core:layer_nearby"),
        },
    }


def map_page(request):
    """Страница интерактивной карты логистической инфраструктуры."""
    from analytics.services import load_index

    metrics = choropleth_metrics(selectors.district_profiles(), load_index())
    context = page_context(
        request,
        title=_("Карта логистической инфраструктуры"),
        lead=_(
            "Пространственное распределение складских мощностей, магистралей "
            "и дорожных событий на территории Москвы."
        ),
        active="map",
        crumbs=[(_("Карта"),)],
        districts=District.objects.all(),
        types=InfrastructureType.objects.all(),
        metrics=metrics,
        summary=selectors.dashboard_summary(),
        map_settings=map_settings(metrics),
    )
    return render(request, "pages/map.html", context)


@require_GET
def nearby_objects(request) -> JsonResponse:
    """Поиск объектов инфраструктуры вблизи произвольной точки.

    Используется инструментом карты «что рядом»: пользователь указывает точку,
    система возвращает ближайшие объекты с расстоянием до каждого.
    """
    from geo import nearest

    from ..models import InfrastructureObject

    try:
        lon = float(request.GET.get("lon"))
        lat = float(request.GET.get("lat"))
    except (TypeError, ValueError):
        return JsonResponse({"error": _("Не указаны координаты точки")}, status=400)

    radius = min(float(request.GET.get("radius", 3)), 25.0)
    limit = min(int_param(request, "limit", 15) or 15, 50)

    queryset = InfrastructureObject.objects.with_refs().located()
    results = nearest(queryset, lon, lat, radius, limit)

    return JsonResponse(
        {
            "origin": {"lon": lon, "lat": lat},
            "radius_km": radius,
            "count": len(results),
            "results": [
                {
                    "id": obj.id,
                    "name": obj.name,
                    "type": obj.type.name,
                    "district": obj.district.short_name,
                    "address": obj.address or "",
                    "capacity": float(obj.capacity_tons) if obj.capacity_tons else None,
                    "distance_km": round(distance, 2),
                    "lon": obj.geom.lon,
                    "lat": obj.geom.lat,
                    "url": obj.get_absolute_url(),
                }
                for obj, distance in results
            ],
        }
    )
