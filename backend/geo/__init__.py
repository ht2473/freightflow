"""Геопространственный слой ИС «ГрузПоток».

Пакет инкапсулирует всю работу с координатами: разбор и формирование
геометрии, поле модели Django и пространственные запросы. Прикладные модули
импортируют только то, что перечислено ниже, и не зависят от конкретной СУБД.
"""

from .fields import (
    GeometryField,
    LineStringField,
    MultiLineStringField,
    MultiPolygonField,
    PointField,
)
from .geometry import DEFAULT_SRID, Geometry, GeometryError, feature_collection, haversine_km
from .mvt import TILE_CONTENT_TYPE, TileFeature, render_tile
from .queries import (
    annotate_distance,
    bbox_of,
    in_bbox,
    nearest,
    simplify,
    to_feature_collection,
)
from .tiles import TILE_BUFFER, TILE_EXTENT, TileError, tile_bounds, tile_of

__all__ = [
    "DEFAULT_SRID",
    "TILE_BUFFER",
    "TILE_CONTENT_TYPE",
    "TILE_EXTENT",
    "Geometry",
    "GeometryError",
    "GeometryField",
    "LineStringField",
    "MultiLineStringField",
    "MultiPolygonField",
    "PointField",
    "TileError",
    "TileFeature",
    "annotate_distance",
    "bbox_of",
    "feature_collection",
    "haversine_km",
    "in_bbox",
    "nearest",
    "render_tile",
    "simplify",
    "tile_bounds",
    "tile_of",
    "to_feature_collection",
]
