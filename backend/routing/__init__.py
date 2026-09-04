"""Расчёты по графу дорог: доступность и маршруты грузового транспорта.

Пакет обращается к службе маршрутизации, работающей на графе дорог
OpenStreetMap, и переводит её ответы в величины и геометрию системы.
Прикладной код обращается только к тому, что перечислено ниже.
"""

from .client import RouterNotConfiguredError, RouterUnavailableError, RoutingError, is_configured
from .profiles import PROFILES, TruckProfile
from .service import Isochrone, Route, availability, isochrones, route

__all__ = [
    "PROFILES",
    "Isochrone",
    "Route",
    "RouterNotConfiguredError",
    "RouterUnavailableError",
    "RoutingError",
    "TruckProfile",
    "availability",
    "is_configured",
    "isochrones",
    "route",
]
