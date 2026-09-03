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
from .queries import (
    annotate_distance,
    bbox_of,
    in_bbox,
    nearest,
    simplify,
    to_feature_collection,
)

__all__ = [
    "DEFAULT_SRID",
    "Geometry",
    "GeometryError",
    "GeometryField",
    "LineStringField",
    "MultiLineStringField",
    "MultiPolygonField",
    "PointField",
    "annotate_distance",
    "bbox_of",
    "feature_collection",
    "haversine_km",
    "in_bbox",
    "nearest",
    "simplify",
    "to_feature_collection",
]
