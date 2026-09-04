"""Отдача векторных тайлов карты.

Тайл собирается из тех слоёв реестра, которые существуют на запрошенном
масштабе, и содержит только геометрию, попавшую в его квадрат. Клиент
получает столько данных, сколько видит, а не весь слой целиком.

Готовый тайл кладётся в кеш: содержимое зависит только от номера квадрата
и от состояния данных, поэтому один и тот же тайл, запрошенный десятью
пользователями, собирается один раз. Загрузка данных сбрасывает счётчик
поколений, и следующий запрос собирает тайл заново.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET
from geo import TILE_CONTENT_TYPE, TileError, render_tile, tile_bounds
from geo.tiles import TILE_BUFFER

from ..selectors import tile_generation
from ..tilelayers import LAYERS, MAX_ZOOM, MIN_ZOOM, intersects_city, layers_for_zoom


@require_GET
def vector_tile(request, z: int, x: int, y: int) -> HttpResponse:
    """Тайл векторной сетки в формате Mapbox Vector Tile.

    Пустой тайл отдаётся кодом 204: сетка покрывает весь мир, и подавляющая
    часть её квадратов данных не содержит. Отказ 404 в этом случае был бы
    неверен — квадрат существует, просто он пуст.
    """
    if not MIN_ZOOM <= z <= MAX_ZOOM:
        raise Http404("Масштаб вне диапазона тайлов карты")

    cache_key = f"map:tile:{tile_generation()}:{z}:{x}:{y}"
    body = cache.get(cache_key)

    if body is None:
        try:
            bounds = tile_bounds(z, x, y, buffer_units=TILE_BUFFER)
        except TileError as error:
            raise Http404(str(error)) from error

        if intersects_city(bounds):
            body = render_tile(
                z,
                x,
                y,
                {layer.name: layer.build(bounds) for layer in layers_for_zoom(z)},
            )
        else:
            body = b""
        cache.set(cache_key, body, settings.MAP_TILE_CACHE_TTL)

    if not body:
        return HttpResponse(status=204, headers=_cache_headers())

    return HttpResponse(body, content_type=TILE_CONTENT_TYPE, headers=_cache_headers())


def _cache_headers() -> dict[str, str]:
    """Заголовки хранения тайла у клиента и на обратном прокси."""
    return {"Cache-Control": f"public, max-age={settings.MAP_TILE_CACHE_TTL}"}


@require_GET
def tilejson(request) -> JsonResponse:
    """Описание источника тайлов по спецификации TileJSON.

    Клиент получает адрес сетки, диапазон масштабов и состав слоёв со всеми
    их свойствами из одного места — того же реестра, по которому тайл
    собирается.
    """
    template = request.build_absolute_uri(
        reverse("core:map_tile", kwargs={"z": 0, "x": 0, "y": 0})
    ).replace("/0/0/0.pbf", "/{z}/{x}/{y}.pbf")

    return JsonResponse(
        {
            "tilejson": "3.0.0",
            "name": "ГрузПоток",
            "description": "Логистическая инфраструктура города Москвы",
            "attribution": settings.MAP_ATTRIBUTION,
            "scheme": "xyz",
            "tiles": [template],
            "minzoom": MIN_ZOOM,
            "maxzoom": MAX_ZOOM,
            "bounds": list(settings.MAP_CITY_BOUNDS),
            "center": [
                settings.MAP_DEFAULT_CENTER[1],
                settings.MAP_DEFAULT_CENTER[0],
                settings.MAP_DEFAULT_ZOOM,
            ],
            "vector_layers": [
                {
                    "id": layer.name,
                    "description": layer.title,
                    "minzoom": max(layer.min_zoom, MIN_ZOOM),
                    "maxzoom": MAX_ZOOM,
                    "fields": layer.fields,
                }
                for layer in LAYERS
            ],
        }
    )
