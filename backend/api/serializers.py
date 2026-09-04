"""Сериализаторы REST API.

Геометрия отдаётся в формате GeoJSON: это позволяет клиенту напрямую передать
ответ в картографическую библиотеку без промежуточных преобразований.
Ссылочные поля разворачиваются в человекочитаемые названия — потребителю API
редко нужен числовой идентификатор справочника без его расшифровки.
"""

from __future__ import annotations

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
    TrafficCondition,
    TrafficIncident,
)
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


@extend_schema_field(
    {
        "type": "object",
        "nullable": True,
        "description": "Геометрия в формате GeoJSON, система координат WGS-84",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["Point", "LineString", "Polygon", "MultiPolygon"],
            },
            "coordinates": {"type": "array", "items": {}},
        },
    }
)
class GeometryField(serializers.Field):
    """Поле геометрии в представлении GeoJSON.

    Структура поля объявлена явно: генератор спецификации не в состоянии
    вывести её самостоятельно для нестандартного типа.
    """

    def to_representation(self, value):
        return value.geojson if value is not None else None

    def to_internal_value(self, data):
        from geo import Geometry

        return Geometry.from_geojson(data)


class DistrictSerializer(serializers.ModelSerializer):
    """Административный округ."""

    center = GeometryField(read_only=True)
    density = serializers.FloatField(read_only=True)

    class Meta:
        model = District
        fields = ("id", "name", "short_name", "area_sq_km", "population", "density", "center")


class InfrastructureTypeSerializer(serializers.ModelSerializer):
    """Тип объекта инфраструктуры."""

    objects_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = InfrastructureType
        fields = ("id", "code", "name", "description", "objects_count")


class CargoCategorySerializer(serializers.ModelSerializer):
    """Категория груза."""

    hazard_label = serializers.CharField(read_only=True)
    is_hazardous = serializers.BooleanField(read_only=True)

    class Meta:
        model = CargoCategory
        fields = ("id", "code", "name", "hazard_class", "hazard_label", "is_hazardous")


class DataSourceSerializer(serializers.ModelSerializer):
    """Источник данных."""

    source_type_label = serializers.CharField(source="get_source_type_display", read_only=True)

    class Meta:
        model = DataSource
        fields = (
            "id", "code", "name", "source_type", "source_type_label",
            "url", "update_frequency", "is_active",
        )


class InfrastructureObjectSerializer(serializers.ModelSerializer):
    """Объект логистической инфраструктуры."""

    type_name = serializers.CharField(source="type.name", read_only=True)
    district_name = serializers.CharField(source="district.short_name", read_only=True)
    source_name = serializers.CharField(source="source.name", read_only=True, default=None)
    geometry = GeometryField(source="geom", read_only=True)
    distance_km = serializers.FloatField(read_only=True, required=False)

    class Meta:
        model = InfrastructureObject
        fields = (
            "id", "name", "address", "type", "type_name", "district", "district_name",
            "capacity_tons", "area_sq_m", "operating_hours", "geometry",
            "source", "source_name", "distance_km", "created_at", "updated_at",
        )


class RoadSegmentSerializer(serializers.ModelSerializer):
    """Участок улично-дорожной сети."""

    road_class_label = serializers.CharField(source="get_road_class_display", read_only=True)
    district_name = serializers.CharField(source="district.short_name", read_only=True, default=None)
    geometry = GeometryField(source="geom", read_only=True)

    class Meta:
        model = RoadSegment
        fields = (
            "id", "name", "road_class", "road_class_label", "lanes", "length_km",
            "speed_limit_kmh", "district", "district_name", "geometry",
        )


class TrafficConditionSerializer(serializers.ModelSerializer):
    """Замер дорожной обстановки."""

    road_name = serializers.CharField(source="road.name", read_only=True)
    state = serializers.SerializerMethodField()

    class Meta:
        model = TrafficCondition
        fields = (
            "id", "road", "road_name", "recorded_at", "congestion_level", "state",
            "avg_speed_kmh", "travel_time_min", "vehicle_density", "incident_flag",
        )

    def get_state(self, obj) -> dict:
        """Кодовое обозначение и подпись состояния движения."""
        code, label, tone = obj.state
        return {"code": code, "label": label, "tone": tone}


class TrafficIncidentSerializer(serializers.ModelSerializer):
    """Дорожный инцидент."""

    incident_type_label = serializers.CharField(source="get_incident_type_display", read_only=True)
    road_name = serializers.CharField(source="road.name", read_only=True, default=None)
    district_name = serializers.CharField(
        source="district.short_name", read_only=True, default=None
    )
    geometry = GeometryField(source="geom", read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    duration_hours = serializers.FloatField(read_only=True)

    class Meta:
        model = TrafficIncident
        fields = (
            "id", "incident_type", "incident_type_label", "severity", "reported_at",
            "resolved_at", "is_open", "duration_hours", "road", "road_name",
            "district", "district_name",
            "description", "affects_cargo", "geometry",
        )


class CargoRouteSerializer(serializers.ModelSerializer):
    """Грузовой маршрут."""

    route_type_label = serializers.CharField(source="get_route_type_display", read_only=True)
    geometry = GeometryField(source="geom", read_only=True)
    avg_speed_kmh = serializers.FloatField(read_only=True)

    class Meta:
        model = CargoRoute
        fields = (
            "id", "name", "route_type", "route_type_label", "origin_region", "destination",
            "distance_km", "avg_duration_h", "avg_speed_kmh", "truck_count_day", "geometry",
        )


class FreightFlowSerializer(serializers.ModelSerializer):
    """Показатель грузопотока за период."""

    direction_label = serializers.CharField(source="get_direction_display", read_only=True)
    scope_label = serializers.CharField(source="get_scope_display", read_only=True)
    district_name = serializers.CharField(source="district.short_name", read_only=True, default=None)
    category_name = serializers.CharField(
        source="cargo_category.name", read_only=True, default=None
    )
    average_haul_km = serializers.FloatField(read_only=True)

    class Meta:
        model = FreightFlowStat
        fields = (
            "id", "period_date", "period_type", "territory",
            "direction", "direction_label", "scope", "scope_label",
            "district", "district_name", "cargo_category", "category_name", "route",
            "volume_tons", "turnover_ton_km", "average_haul_km",
            "vehicle_count", "avg_speed_kmh", "origin",
        )


class EtlRunSerializer(serializers.ModelSerializer):
    """Запись журнала загрузки данных."""

    source_name = serializers.CharField(source="source.name", read_only=True, default=None)
    duration_minutes = serializers.FloatField(read_only=True)

    class Meta:
        model = EtlRun
        fields = (
            "id", "started_at", "finished_at", "duration_minutes", "source", "source_name",
            "target_table", "records_loaded", "records_errors", "status", "error_message",
        )
