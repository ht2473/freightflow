"""Конечные точки REST API.

Все справочники и реестры доступны на чтение без авторизации: сведения
являются открытыми. Изменяющие операции в текущей версии интерфейса не
предусмотрены — данные поступают через процедуры загрузки, а не через API.
"""

from __future__ import annotations

from analytics import services as analytics_services
from core import selectors
from core.models import (
    CargoCategory,
    CargoRoute,
    DataSource,
    District,
    EtlRun,
    FreightFlowStat,
    InfrastructureObject,
    InfrastructureType,
    RoadSegment,
    TrafficIncident,
)
from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from . import serializers


class ReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    """Базовый набор представлений «только чтение»."""

    def _int(self, name: str) -> int | None:
        """Прочитать целочисленный параметр запроса."""
        raw = self.request.query_params.get(name)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None


@extend_schema(tags=["Справочники"])
class DistrictViewSet(ReadOnlyViewSet):
    """Административные округа города Москвы."""

    queryset = District.objects.order_by("id")
    serializer_class = serializers.DistrictSerializer

    @extend_schema(summary="Агрегированные показатели округов")
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Профили округов: инфраструктура, грузопоток, загруженность."""
        rows = [
            {
                "id": profile["district"].id,
                "name": profile["district"].name,
                "short_name": profile["district"].short_name,
                "object_count": profile["object_count"],
                "capacity_tons": profile["capacity_tons"],
                "volume_tons": profile["volume_tons"],
                "road_length_km": profile["road_length_km"],
                "congestion": profile["congestion"],
            }
            for profile in selectors.district_profiles()
        ]
        return Response({"count": len(rows), "results": rows})


@extend_schema(tags=["Справочники"])
class InfrastructureTypeViewSet(ReadOnlyViewSet):
    """Классификатор типов объектов инфраструктуры."""

    queryset = InfrastructureType.objects.annotate(objects_count=Count("facilities")).order_by("id")
    serializer_class = serializers.InfrastructureTypeSerializer


@extend_schema(tags=["Справочники"])
class CargoCategoryViewSet(ReadOnlyViewSet):
    """Классификатор категорий грузов."""

    queryset = CargoCategory.objects.order_by("id")
    serializer_class = serializers.CargoCategorySerializer


@extend_schema(tags=["Справочники"])
class DataSourceViewSet(ReadOnlyViewSet):
    """Реестр источников данных."""

    queryset = DataSource.objects.order_by("id")
    serializer_class = serializers.DataSourceSerializer


@extend_schema(
    tags=["Инфраструктура"],
    parameters=[
        OpenApiParameter("district", int, description="Идентификатор округа"),
        OpenApiParameter("type", int, description="Идентификатор типа объекта"),
        OpenApiParameter("q", str, description="Поиск по наименованию и адресу"),
    ],
)
class InfrastructureObjectViewSet(ReadOnlyViewSet):
    """Объекты логистической инфраструктуры."""

    serializer_class = serializers.InfrastructureObjectSerializer

    def get_queryset(self):
        """Применить условия отбора из параметров запроса."""
        queryset = InfrastructureObject.objects.with_refs()
        return (
            queryset.in_district(self._int("district"))
            .of_type(self._int("type"))
            .search(self.request.query_params.get("q"))
        )

    @extend_schema(
        summary="Ближайшие объекты к точке",
        parameters=[
            OpenApiParameter("lon", float, required=True, description="Долгота, градусы"),
            OpenApiParameter("lat", float, required=True, description="Широта, градусы"),
            OpenApiParameter("radius", float, description="Радиус поиска, км (по умолчанию 3)"),
            OpenApiParameter("limit", int, description="Максимальное число результатов"),
        ],
    )
    @action(detail=False, methods=["get"], url_path="nearby")
    def nearby(self, request):
        """Поиск объектов в заданном радиусе от произвольной точки."""
        from geo import nearest

        try:
            lon = float(request.query_params["lon"])
            lat = float(request.query_params["lat"])
        except (KeyError, TypeError, ValueError):
            return Response({"detail": "Требуются параметры lon и lat"}, status=400)

        radius = min(float(request.query_params.get("radius", 3)), 25.0)
        limit = min(self._int("limit") or 20, 100)

        results = nearest(
            InfrastructureObject.objects.with_refs().located(), lon, lat, radius, limit
        )
        payload = []
        for obj, distance in results:
            data = self.get_serializer(obj).data
            data["distance_km"] = round(distance, 3)
            payload.append(data)
        return Response(
            {"origin": {"lon": lon, "lat": lat}, "radius_km": radius,
             "count": len(payload), "results": payload}
        )


@extend_schema(tags=["Дорожная сеть"])
class RoadSegmentViewSet(ReadOnlyViewSet):
    """Участки улично-дорожной сети."""

    serializer_class = serializers.RoadSegmentSerializer

    def get_queryset(self):
        queryset = RoadSegment.objects.select_related("district").defer("district__geom")
        district = self._int("district")
        road_class = self.request.query_params.get("class")
        if district:
            queryset = queryset.filter(district_id=district)
        if road_class:
            queryset = queryset.filter(road_class=road_class)
        return queryset


@extend_schema(tags=["Дорожная сеть"])
class TrafficIncidentViewSet(ReadOnlyViewSet):
    """Журнал дорожных инцидентов."""

    serializer_class = serializers.TrafficIncidentSerializer

    def get_queryset(self):
        queryset = TrafficIncident.objects.with_refs()
        params = self.request.query_params
        if params.get("type"):
            queryset = queryset.filter(incident_type=params["type"])
        if params.get("state") == "open":
            queryset = queryset.open()
        elif params.get("state") == "closed":
            queryset = queryset.filter(resolved_at__isnull=False)
        if params.get("cargo") == "1":
            queryset = queryset.affecting_cargo()
        severity = self._int("severity")
        if severity:
            queryset = queryset.filter(severity__gte=severity)
        return queryset.order_by("-reported_at")


@extend_schema(tags=["Грузопотоки"])
class CargoRouteViewSet(ReadOnlyViewSet):
    """Грузовые маршруты и транспортные коридоры."""

    serializer_class = serializers.CargoRouteSerializer

    def get_queryset(self):
        queryset = CargoRoute.objects.all()
        route_type = self.request.query_params.get("type")
        return queryset.filter(route_type=route_type) if route_type else queryset


@extend_schema(tags=["Грузопотоки"])
class FreightFlowViewSet(ReadOnlyViewSet):
    """Статистика грузопотоков."""

    serializer_class = serializers.FreightFlowSerializer

    def get_queryset(self):
        queryset = FreightFlowStat.objects.select_related(
        "district", "cargo_category", "route"
    ).defer("district__geom")
        params = self.request.query_params
        district, category = self._int("district"), self._int("category")
        if district:
            queryset = queryset.filter(district_id=district)
        if category:
            queryset = queryset.filter(cargo_category_id=category)
        if params.get("direction"):
            queryset = queryset.filter(direction=params["direction"])
        if params.get("territory"):
            queryset = queryset.filter(territory=params["territory"])
        if params.get("scope"):
            queryset = queryset.filter(scope=params["scope"])
        if params.get("period_from"):
            queryset = queryset.filter(period_date__gte=params["period_from"])
        if params.get("period_to"):
            queryset = queryset.filter(period_date__lte=params["period_to"])
        return queryset.order_by("-period_date")


@extend_schema(tags=["Данные"])
class EtlRunViewSet(ReadOnlyViewSet):
    """Журнал загрузок данных."""

    queryset = EtlRun.objects.select_related("source").order_by("-started_at")
    serializer_class = serializers.EtlRunSerializer


# ---------------------------------------------------------------------------
#  Функциональные конечные точки
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Дорожная сеть"],
    summary="Текущая дорожная обстановка",
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def current_traffic(request):
    """Последний замер загруженности по каждому участку сети."""
    rows = selectors.latest_conditions()
    district = request.query_params.get("district")
    if district and district.isdigit():
        rows = [row for row in rows if row.road.district_id == int(district)]
    return Response(
        {
            "count": len(rows),
            "results": serializers.TrafficConditionSerializer(rows, many=True).data,
        }
    )


@extend_schema(
    tags=["Аналитика"],
    summary="Индекс логистической нагрузки",
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def load_index(request):
    """Композитная оценка нагрузки на инфраструктуру округов."""
    rows = [
        {
            "rank": row["rank"],
            "district_id": row["district"].id,
            "district": row["district"].name,
            "short_name": row["district"].short_name,
            "score": row["score"],
            "components": row["components"],
            "values": row["raw"],
        }
        for row in analytics_services.load_index()
    ]
    return Response(
        {
            "components": [
                {
                    "key": item.key,
                    "title": str(item.title),
                    "unit": str(item.unit),
                    "formula": item.formula,
                    "weight": item.weight,
                    "inverse": item.inverse,
                    "origin": item.origin,
                    "source": str(item.source),
                }
                for item in analytics_services.COMPONENTS
            ],
            "count": len(rows),
            "results": rows,
        }
    )


@extend_schema(
    tags=["Аналитика"],
    summary="Прогноз объёма перевозок",
    parameters=[
        OpenApiParameter("territory", str, description="Территория ряда"),
        OpenApiParameter("horizon", int, description="Горизонт прогноза, шагов ряда (1–10)"),
    ],
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def forecast(request):
    """Продолжение ряда перевозок с оценкой качества на отложенной выборке."""
    territory = (request.query_params.get("territory") or "").strip()
    horizon = request.query_params.get("horizon", "5")
    result = analytics_services.forecast_flow(
        territory or None,
        min(max(int(horizon) if horizon.isdigit() else 5, 1), 10),
        model_code=(request.query_params.get("model") or "").strip() or None,
    )
    if not result.get("available"):
        return Response({"detail": result.get("reason", "Прогноз недоступен")}, status=422)

    comparison = result["comparison"]
    return Response(
        {
            "territory": result["territory"],
            "granularity": result["granularity"],
            "model": {
                "code": result["model"].code,
                "title": str(result["model"].title),
                "note": str(result["model"].note),
            },
            "quality": {
                "mae": result["mae"],
                "rmse": result["rmse"],
                "mape": result["mape"],
                "r_squared": result["r_squared"],
                "holdout": result["holdout"],
                "gain_over_naive": result["gain"],
                "label": result["quality"],
            },
            "models": [
                {
                    "code": item["model"].code,
                    "title": str(item["model"].title),
                    "position": item["position"],
                    "mae": round(item["mae"], 1),
                    "rmse": round(item["rmse"], 1),
                    "mape": None if item["mape"] is None else round(item["mape"], 1),
                    "gain_over_naive": item["gain"],
                }
                for item in comparison["outcomes"]
            ],
            "rejected": [
                {"code": item["model"].code, "reason": item["reason"]}
                for item in comparison["rejected"]
            ],
            "history": [
                {"period": row["period"].isoformat(), "volume": row["volume"],
                 "turnover_ton_km": row["turnover"]}
                for row in result["history"]
            ],
            "forecast": [
                {"period": row["period"].isoformat(), "value": row["value"]}
                for row in result["forecast"]
            ],
        }
    )


@extend_schema(
    tags=["Аналитика"],
    summary="Типология округов",
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def typology(request):
    """Разбиение округов на однородные группы."""
    k = request.query_params.get("k", "4")
    result = analytics_services.typology(min(max(int(k) if k.isdigit() else 4, 2), 6))
    return Response(
        {
            "k": result.get("k"),
            "inertia": result.get("inertia"),
            "clusters": [
                {
                    "index": cluster["index"],
                    "name": cluster["name"],
                    "size": cluster["size"],
                    "avg_score": cluster["avg_score"],
                    "districts": [m["district"].short_name for m in cluster["members"]],
                }
                for cluster in result.get("clusters", [])
            ],
        }
    )
