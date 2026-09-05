-- =============================================================================
--  ИС «ГрузПоток» — аналитические представления
--  Применять после 001_schema.sql и загрузки данных.
-- =============================================================================

BEGIN;

-- Сводка по административным округам: инфраструктура, грузопоток и средняя
-- загруженность дорог. Используется на странице «Округа» и в виджетах главной.
CREATE OR REPLACE VIEW v_district_summary AS
SELECT
    d.id                                            AS district_id,
    d.name                                          AS district_name,
    d.short_name,
    d.area_sq_km,
    d.population,
    COALESCE(obj.object_count, 0)                   AS object_count,
    COALESCE(obj.total_capacity_tons, 0)            AS total_capacity_tons,
    COALESCE(obj.total_area_sq_m, 0)                AS total_area_sq_m,
    COALESCE(flow.volume_tons, 0)                   AS freight_volume_tons,
    COALESCE(flow.vehicle_count, 0)                 AS freight_vehicle_count,
    road.road_count,
    road.total_road_km,
    traffic.avg_congestion
FROM districts d
LEFT JOIN (
    SELECT district_id,
           COUNT(*)                AS object_count,
           SUM(capacity_tons)      AS total_capacity_tons,
           SUM(area_sq_m)          AS total_area_sq_m
    FROM infrastructure_objects
    GROUP BY district_id
) obj ON obj.district_id = d.id
LEFT JOIN (
    SELECT district_id,
           SUM(volume_tons)        AS volume_tons,
           SUM(vehicle_count)      AS vehicle_count
    FROM freight_flow_stats
    GROUP BY district_id
) flow ON flow.district_id = d.id
LEFT JOIN (
    SELECT district_id,
           COUNT(*)                AS road_count,
           SUM(length_km)          AS total_road_km
    FROM road_segments
    GROUP BY district_id
) road ON road.district_id = d.id
LEFT JOIN (
    SELECT r.district_id,
           AVG(tc.congestion_level)::numeric(4, 2) AS avg_congestion
    FROM traffic_conditions tc
    JOIN road_segments r ON r.id = tc.road_id
    GROUP BY r.district_id
) traffic ON traffic.district_id = d.id;

COMMENT ON VIEW v_district_summary IS 'Агрегированный профиль округа: объекты, грузопоток, дороги, загруженность';

-- Текущая дорожная обстановка: по одному последнему замеру на каждый участок.
-- DISTINCT ON — конструкция PostgreSQL, эффективно берёт «первую строку в группе».
CREATE OR REPLACE VIEW v_current_traffic AS
SELECT DISTINCT ON (tc.road_id)
    tc.road_id,
    r.name                AS road_name,
    r.road_class,
    r.district_id,
    tc.recorded_at,
    tc.congestion_level,
    tc.avg_speed_kmh,
    tc.travel_time_min,
    tc.vehicle_density,
    tc.incident_flag
FROM traffic_conditions tc
JOIN road_segments r ON r.id = tc.road_id
ORDER BY tc.road_id, tc.recorded_at DESC;

COMMENT ON VIEW v_current_traffic IS 'Последний по времени замер обстановки для каждого участка дороги';

COMMIT;
