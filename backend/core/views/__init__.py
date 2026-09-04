"""Публичные представления системы.

Модуль собирает представления из тематических подмодулей в единое
пространство имён, чтобы маршруты объявлялись компактно и единообразно.
"""

from .mapview import map_page, nearby_objects
from .monitoring import flow_overview, incident_detail, incident_list, traffic
from .pages import about, api_docs, health, help_page, home, methodology, sitemap_page
from .registry import (
    cargo_list,
    district_detail,
    district_list,
    etl_log,
    object_detail,
    object_list,
    road_detail,
    road_list,
    route_detail,
    route_list,
    source_detail,
    source_list,
    type_list,
)
from .tileview import tilejson, vector_tile

__all__ = [
    "about",
    "api_docs",
    "cargo_list",
    "district_detail",
    "district_list",
    "etl_log",
    "flow_overview",
    "health",
    "help_page",
    "home",
    "incident_detail",
    "incident_list",
    "map_page",
    "methodology",
    "nearby_objects",
    "object_detail",
    "object_list",
    "road_detail",
    "road_list",
    "route_detail",
    "route_list",
    "sitemap_page",
    "source_detail",
    "source_list",
    "tilejson",
    "traffic",
    "type_list",
    "vector_tile",
]
