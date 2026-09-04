-- =============================================================================
--  ИС «ГрузПоток» (FreightFlow) — логистическая инфраструктура города Москвы
--  Схема операционно-аналитической базы данных: PostgreSQL 15+ / PostGIS 3.x
--
--  Файл является каноническим описанием доменной части хранилища. Django-модели
--  приложения (backend/core/models.py) отображаются на эти же таблицы один в
--  один: имена таблиц, колонок и типы согласованы, поэтому базу можно поднять
--  как миграциями Django, так и «сырым» SQL — результат идентичен.
--
--  Порядок применения:
--      psql -d freightflow -f db/001_schema.sql              (схема)
--      psql -d freightflow -f db/003_views.sql               (представления)
--      psql -d freightflow -f db/004_district_centers.sql    (центры округов)
--
--  Данными база наполняется загрузкой из источников, а не поставляемым
--  набором: python backend/manage.py etl --all
-- =============================================================================

BEGIN;

-- Расширения. PostGIS обеспечивает геометрические типы и пространственные
-- операции (ST_AsText, ST_DWithin, ST_Distance), pg_trgm — быстрый поиск по
-- подстроке в названиях объектов (индексы GIN ниже).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- -----------------------------------------------------------------------------
--  1. СПРАВОЧНИКИ
-- -----------------------------------------------------------------------------

-- Административные округа Москвы (12 шт.).
-- geom   — граница округа (заполняется опционально, из открытых данных);
-- center — координата условного центра округа, используется для подписей карты
--          и как fallback при отсутствии границ.
CREATE TABLE IF NOT EXISTS districts (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(120) NOT NULL UNIQUE,
    short_name  VARCHAR(16)  NOT NULL UNIQUE,
    area_sq_km  NUMERIC(10, 2),
    population  INTEGER,
    geom        geometry(MultiPolygon, 4326),
    center      geometry(Point, 4326),
    CONSTRAINT districts_area_positive CHECK (area_sq_km IS NULL OR area_sq_km > 0),
    CONSTRAINT districts_population_positive CHECK (population IS NULL OR population >= 0)
);

COMMENT ON TABLE  districts IS 'Административные округа города Москвы';
COMMENT ON COLUMN districts.short_name IS 'Аббревиатура округа: ЦАО, САО, СВАО, …';

-- Типы объектов логистической инфраструктуры: склад, терминал, грузовой двор и т.д.
CREATE TABLE IF NOT EXISTS infrastructure_types (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(32)  NOT NULL UNIQUE,
    name        VARCHAR(120) NOT NULL,
    description TEXT
);

COMMENT ON TABLE infrastructure_types IS 'Классификатор типов объектов инфраструктуры';

-- Классификатор грузов. hazard_class — класс опасности по ДОПОГ/ADR:
-- 0 — груз не опасен, 1…9 — соответствующий класс опасности.
CREATE TABLE IF NOT EXISTS cargo_categories (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(32)  NOT NULL UNIQUE,
    name         VARCHAR(120) NOT NULL,
    hazard_class SMALLINT     NOT NULL DEFAULT 0,
    CONSTRAINT cargo_categories_hazard_range CHECK (hazard_class BETWEEN 0 AND 9)
);

COMMENT ON TABLE  cargo_categories IS 'Классификатор категорий перевозимых грузов';
COMMENT ON COLUMN cargo_categories.hazard_class IS 'Класс опасности ADR: 0 — неопасный груз';

-- Реестр источников данных, интегрированных в систему. Источник объявляется
-- здесь только вместе с работающим загрузчиком: запись означает, что данные
-- оттуда действительно поступают.
-- source_type: api | csv | open_data | gis_service | manual | model.
CREATE TABLE IF NOT EXISTS data_sources (
    id               SERIAL PRIMARY KEY,
    code             VARCHAR(32)  NOT NULL UNIQUE,
    name             VARCHAR(200) NOT NULL,
    source_type      VARCHAR(32)  NOT NULL,
    url              VARCHAR(500),
    update_frequency VARCHAR(32),
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT data_sources_type_allowed CHECK (
        source_type IN ('api', 'csv', 'open_data', 'gis_service', 'manual', 'model')
    )
);

COMMENT ON TABLE  data_sources IS 'Реестр источников данных, наполняющих систему';
COMMENT ON COLUMN data_sources.update_frequency IS 'Регламент обновления: hourly | daily | weekly | monthly | quarterly';

-- -----------------------------------------------------------------------------
--  2. ОБЪЕКТЫ ПРЕДМЕТНОЙ ОБЛАСТИ
-- -----------------------------------------------------------------------------

-- Точечные объекты логистической инфраструктуры: склады, терминалы, грузовые
-- дворы, стоянки грузового транспорта, весовые пункты, распределительные центры.
CREATE TABLE IF NOT EXISTS infrastructure_objects (
    id              SERIAL PRIMARY KEY,
    type_id         INTEGER NOT NULL REFERENCES infrastructure_types (id) ON DELETE RESTRICT,
    district_id     INTEGER NOT NULL REFERENCES districts (id) ON DELETE RESTRICT,
    name            VARCHAR(200) NOT NULL,
    address         VARCHAR(300),
    capacity_tons   NUMERIC(12, 2),
    area_sq_m       NUMERIC(12, 2),
    capacity_origin VARCHAR(16),
    area_origin     VARCHAR(16),
    operating_hours VARCHAR(64),
    operator        VARCHAR(200),
    website         VARCHAR(300),
    phone           VARCHAR(64),
    geom            geometry(Point, 4326),
    footprint       geometry(MultiPolygon, 4326),
    osm_type        VARCHAR(10),
    osm_id          BIGINT,
    classification_rule VARCHAR(32),
    source_updated_at   TIMESTAMPTZ,
    source_id       INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT infra_capacity_positive CHECK (capacity_tons IS NULL OR capacity_tons >= 0),
    CONSTRAINT infra_area_positive CHECK (area_sq_m IS NULL OR area_sq_m >= 0)
);

-- Ключ исходного элемента уникален: повторная загрузка обновляет запись,
-- а не создаёт вторую. Нумерация точек, линий и отношений в OpenStreetMap
-- независима, поэтому в ключ входит и разновидность элемента.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_infra_osm_element
    ON infrastructure_objects (osm_type, osm_id) WHERE osm_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_infra_osm
    ON infrastructure_objects (osm_type, osm_id);

COMMENT ON TABLE  infrastructure_objects IS 'Объекты логистической инфраструктуры (точечные)';
COMMENT ON COLUMN infrastructure_objects.capacity_tons IS 'Проектная мощность единовременного хранения, тонн';
COMMENT ON COLUMN infrastructure_objects.capacity_origin IS 'Происхождение мощности: measured | derived | modelled';
COMMENT ON COLUMN infrastructure_objects.area_origin IS 'Происхождение площади: measured | derived | modelled';
COMMENT ON COLUMN infrastructure_objects.footprint IS 'Контур объекта; площадь по нему является измеренной величиной';
COMMENT ON COLUMN infrastructure_objects.classification_rule IS 'Обозначение правила, по которому объект отнесён к типу';

-- Ключевые участки улично-дорожной сети, по которым ведётся мониторинг.
-- road_class: highway (магистраль) | arterial (городская магистраль) | collector (связующая).
CREATE TABLE IF NOT EXISTS road_segments (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    ref             VARCHAR(32),
    road_class      VARCHAR(32)  NOT NULL,
    lanes           SMALLINT,
    length_km       NUMERIC(8, 2),
    length_origin   VARCHAR(16),
    allows_hgv      BOOLEAN,
    segment_count   INTEGER NOT NULL DEFAULT 0,
    source_updated_at TIMESTAMPTZ,
    speed_limit_kmh SMALLINT,
    district_id     INTEGER REFERENCES districts (id) ON DELETE SET NULL,
    geom            geometry(MultiLineString, 4326),
    source_id       INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    CONSTRAINT road_class_allowed CHECK (road_class IN ('highway', 'arterial', 'collector')),
    CONSTRAINT road_lanes_range CHECK (lanes IS NULL OR lanes BETWEEN 1 AND 16),
    CONSTRAINT road_speed_range CHECK (speed_limit_kmh IS NULL OR speed_limit_kmh BETWEEN 5 AND 130)
);

COMMENT ON TABLE infrastructure_objects IS 'Объекты логистической инфраструктуры (точечные)';
COMMENT ON TABLE road_segments IS 'Участки улично-дорожной сети под мониторингом';

-- Грузовые маршруты и транспортные коридоры.
-- route_type: inbound (ввоз в город) | outbound (вывоз) | transit (транзит).
CREATE TABLE IF NOT EXISTS cargo_routes (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    route_type      VARCHAR(16)  NOT NULL,
    origin_region   VARCHAR(120),
    destination     VARCHAR(120),
    distance_km     NUMERIC(10, 2),
    avg_duration_h  NUMERIC(8, 2),
    truck_count_day INTEGER,
    geom            geometry(MultiLineString, 4326),
    source_id       INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    CONSTRAINT route_type_allowed CHECK (route_type IN ('inbound', 'outbound', 'transit')),
    CONSTRAINT route_distance_positive CHECK (distance_km IS NULL OR distance_km > 0),
    CONSTRAINT route_trucks_positive CHECK (truck_count_day IS NULL OR truck_count_day >= 0)
);

COMMENT ON TABLE  cargo_routes IS 'Грузовые маршруты и транспортные коридоры';
COMMENT ON COLUMN cargo_routes.truck_count_day IS 'Среднесуточная интенсивность движения грузового транспорта, ТС/сут.';

-- -----------------------------------------------------------------------------
--  3. ВРЕМЕННЫЕ РЯДЫ И СОБЫТИЯ
-- -----------------------------------------------------------------------------

-- Агрегированная статистика грузопотоков по периодам.
-- direction: in (ввоз) | out (вывоз) | transit (транзит) | total (без деления).
-- scope:     all (все перевозчики) | commercial (перевозки на коммерческой основе).
-- Внутригородской ряд привязан к округу и маршруту; ведомственный приходит
-- в разрезе территорий (territory) и круга перевозчиков, без направлений.
CREATE TABLE IF NOT EXISTS freight_flow_stats (
    id              SERIAL PRIMARY KEY,
    period_date     DATE        NOT NULL,
    period_type     VARCHAR(16) NOT NULL DEFAULT 'month',
    route_id        INTEGER REFERENCES cargo_routes (id) ON DELETE SET NULL,
    district_id     INTEGER REFERENCES districts (id) ON DELETE SET NULL,
    cargo_cat_id    INTEGER REFERENCES cargo_categories (id) ON DELETE SET NULL,
    territory       VARCHAR(120) NOT NULL DEFAULT '',
    direction       VARCHAR(16) NOT NULL,
    scope           VARCHAR(16) NOT NULL DEFAULT 'all',
    volume_tons     NUMERIC(14, 2),
    turnover_ton_km NUMERIC(18, 2),
    vehicle_count   INTEGER,
    origin          VARCHAR(16),
    avg_speed_kmh   NUMERIC(6, 2),
    source_id       INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    external_key    VARCHAR(120) NOT NULL DEFAULT '',
    CONSTRAINT flow_period_allowed CHECK (period_type IN ('day', 'week', 'month', 'quarter', 'year')),
    CONSTRAINT flow_direction_allowed CHECK (direction IN ('in', 'out', 'transit', 'total')),
    CONSTRAINT flow_scope_allowed CHECK (scope IN ('all', 'commercial')),
    CONSTRAINT flow_volume_positive CHECK (volume_tons IS NULL OR volume_tons >= 0),
    CONSTRAINT flow_turnover_positive CHECK (turnover_ton_km IS NULL OR turnover_ton_km >= 0)
);

-- Ключ записи в источнике уникален в пределах источника: повторная загрузка
-- обновляет строку, а не добавляет вторую. Ряды, введённые вручную, ключа
-- не имеют и ограничением не связаны — отсюда частичный индекс.
CREATE UNIQUE INDEX IF NOT EXISTS uq_flow_external_key
    ON freight_flow_stats (source_id, external_key) WHERE external_key <> '';

COMMENT ON TABLE  freight_flow_stats IS 'Статистика грузопотоков в разрезе периодов, округов и категорий грузов';
COMMENT ON COLUMN freight_flow_stats.turnover_ton_km IS 'Грузооборот: произведение массы груза на расстояние перевозки';
COMMENT ON COLUMN freight_flow_stats.external_key IS 'Ключ сопоставления с записью источника при повторной загрузке';

-- Замеры дорожной обстановки. congestion_level — балл загруженности по шкале
-- ЦОДД (0 — свободно, 10 — движение парализовано).
CREATE TABLE IF NOT EXISTS traffic_conditions (
    id               SERIAL PRIMARY KEY,
    recorded_at      TIMESTAMPTZ NOT NULL,
    road_id          INTEGER NOT NULL REFERENCES road_segments (id) ON DELETE CASCADE,
    congestion_level SMALLINT NOT NULL,
    avg_speed_kmh    NUMERIC(6, 2),
    travel_time_min  NUMERIC(8, 2),
    vehicle_density  INTEGER,
    incident_flag    BOOLEAN NOT NULL DEFAULT FALSE,
    source_id        INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    CONSTRAINT traffic_congestion_range CHECK (congestion_level BETWEEN 0 AND 10),
    CONSTRAINT traffic_speed_positive CHECK (avg_speed_kmh IS NULL OR avg_speed_kmh >= 0)
);

COMMENT ON COLUMN traffic_conditions.congestion_level IS 'Балл загруженности 0–10 (шкала ЦОДД)';
COMMENT ON COLUMN traffic_conditions.vehicle_density IS 'Плотность потока, ТС на километр полосы';

-- Дорожные события: ДТП, ремонтные работы, ограничения, погодные явления.
-- severity: 1 — незначительное, 5 — критическое (полное перекрытие).
CREATE TABLE IF NOT EXISTS traffic_incidents (
    id            SERIAL PRIMARY KEY,
    reported_at   TIMESTAMPTZ NOT NULL,
    resolved_at   TIMESTAMPTZ,
    incident_type VARCHAR(32) NOT NULL,
    severity      SMALLINT    NOT NULL DEFAULT 1,
    road_id       INTEGER REFERENCES road_segments (id) ON DELETE SET NULL,
    description   TEXT,
    geom          geometry(Point, 4326),
    affects_cargo BOOLEAN     NOT NULL DEFAULT FALSE,
    origin        VARCHAR(16),
    source_id     INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    external_key  VARCHAR(120) NOT NULL DEFAULT '',
    CONSTRAINT incident_type_allowed CHECK (
        incident_type IN ('accident', 'roadworks', 'restriction', 'weather', 'event', 'other')
    ),
    CONSTRAINT incident_severity_range CHECK (severity BETWEEN 1 AND 5),
    CONSTRAINT incident_period_valid CHECK (resolved_at IS NULL OR resolved_at >= reported_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_external_key
    ON traffic_incidents (source_id, external_key) WHERE external_key <> '';

COMMENT ON TABLE  traffic_incidents IS 'Инциденты на улично-дорожной сети';
COMMENT ON COLUMN traffic_incidents.affects_cargo IS 'Признак влияния инцидента на движение грузового транспорта';
COMMENT ON COLUMN traffic_incidents.external_key IS 'Ключ сопоставления с записью источника при повторной загрузке';

-- -----------------------------------------------------------------------------
--  4. СЛУЖЕБНЫЕ ТАБЛИЦЫ
-- -----------------------------------------------------------------------------

-- Журнал загрузок ETL: одна строка на одно прохождение конвейера по одному
-- набору данных. Счётчики разделены — по соотношению «создано / обновлено /
-- без изменений» видно, работает ли инкрементальная загрузка: при исправном
-- источнике повторный запуск оставляет почти всё без изменений.
CREATE TABLE IF NOT EXISTS etl_log (
    id                SERIAL PRIMARY KEY,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    source_id         INTEGER REFERENCES data_sources (id) ON DELETE SET NULL,
    pipeline          VARCHAR(64) NOT NULL DEFAULT '',
    target_table      VARCHAR(64) NOT NULL,
    trigger           VARCHAR(16) NOT NULL DEFAULT 'cli',
    actor_id          INTEGER REFERENCES auth_user (id) ON DELETE SET NULL,
    parameters        VARCHAR(200) NOT NULL DEFAULT '',
    records_loaded    INTEGER NOT NULL DEFAULT 0,
    records_created   INTEGER NOT NULL DEFAULT 0,
    records_updated   INTEGER NOT NULL DEFAULT 0,
    records_unchanged INTEGER NOT NULL DEFAULT 0,
    records_removed   INTEGER NOT NULL DEFAULT 0,
    records_errors    INTEGER NOT NULL DEFAULT 0,
    status            VARCHAR(16) NOT NULL DEFAULT 'running',
    error_message     TEXT,
    CONSTRAINT etl_status_allowed CHECK (status IN ('running', 'success', 'partial', 'failed')),
    CONSTRAINT etl_trigger_allowed CHECK (trigger IN ('schedule', 'console', 'upload', 'cli')),
    CONSTRAINT etl_counters_positive CHECK (records_loaded >= 0 AND records_errors >= 0)
);

COMMENT ON TABLE etl_log IS 'Журнал запусков процедур загрузки данных (ETL)';

-- Карантин загрузки: записи источника, не прошедшие проверку качества.
-- Отклонённая запись сохраняется целиком вместе с не пройденной проверкой —
-- иначе о качестве источника можно было бы судить только по счётчику ошибок,
-- а причина отклонения оставалась бы неизвестной.
CREATE TABLE IF NOT EXISTS etl_rejects (
    id             SERIAL PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES etl_log (id) ON DELETE CASCADE,
    position       VARCHAR(120) NOT NULL DEFAULT '',
    record_key     VARCHAR(200) NOT NULL DEFAULT '',
    check_code     VARCHAR(64)  NOT NULL,
    message        VARCHAR(500) NOT NULL,
    payload        TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    reviewed_at    TIMESTAMPTZ,
    reviewed_by_id INTEGER REFERENCES auth_user (id) ON DELETE SET NULL
);

COMMENT ON TABLE  etl_rejects IS 'Карантин: записи источника, отклонённые проверками качества';
COMMENT ON COLUMN etl_rejects.position IS 'Элемент выгрузки или номер строки файла';
COMMENT ON COLUMN etl_rejects.check_code IS 'Код не пройденной проверки';

-- -----------------------------------------------------------------------------
--  5. ИНДЕКСЫ
--  Состав определён по фактическим запросам приложения: фильтрация реестров,
--  выборки временных рядов «последние N», пространственные запросы карты.
-- -----------------------------------------------------------------------------

-- Пространственные (GiST) — для запросов «в границах экрана» и «ближайшие».
CREATE INDEX IF NOT EXISTS idx_infra_geom      ON infrastructure_objects USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_roads_geom      ON road_segments          USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_routes_geom     ON cargo_routes           USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_incidents_geom  ON traffic_incidents      USING GIST (geom);

-- Внешние ключи и типовые фильтры реестров.
CREATE INDEX IF NOT EXISTS idx_infra_district  ON infrastructure_objects (district_id);
CREATE INDEX IF NOT EXISTS idx_infra_type      ON infrastructure_objects (type_id);
CREATE INDEX IF NOT EXISTS idx_infra_source    ON infrastructure_objects (source_id);
CREATE INDEX IF NOT EXISTS idx_roads_district  ON road_segments (district_id);
CREATE INDEX IF NOT EXISTS idx_routes_type     ON cargo_routes (route_type);

-- Поиск по названию объекта без учёта регистра и по подстроке (pg_trgm).
CREATE INDEX IF NOT EXISTS idx_infra_name_trgm ON infrastructure_objects USING GIN (name gin_trgm_ops);

-- Временные ряды: выборка «последние замеры по участку» и агрегаты по периодам.
CREATE INDEX IF NOT EXISTS idx_traffic_road_time ON traffic_conditions (road_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_time      ON traffic_conditions (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_flow_period       ON freight_flow_stats (period_date);
CREATE INDEX IF NOT EXISTS idx_flow_district     ON freight_flow_stats (district_id, period_date);
CREATE INDEX IF NOT EXISTS idx_flow_category     ON freight_flow_stats (cargo_cat_id, period_date);
CREATE INDEX IF NOT EXISTS idx_incidents_time    ON traffic_incidents (reported_at DESC);

-- Открытые инциденты (resolved_at IS NULL) запрашиваются на каждой странице
-- мониторинга — частичный индекс существенно дешевле полного.
CREATE INDEX IF NOT EXISTS idx_incidents_open ON traffic_incidents (reported_at DESC)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_etl_started ON etl_log (started_at DESC);

-- Карантин разбирается двумя способами: по времени поступления и по коду
-- проверки — «покажи всё, что отклонено из-за отсутствия координат».
CREATE INDEX IF NOT EXISTS idx_rejects_time  ON etl_rejects (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rejects_check ON etl_rejects (check_code);

COMMIT;

-- ============================================================================
--  Зоны ограничения движения грузового транспорта
-- ============================================================================

-- Основание — постановление Правительства Москвы № 379-ПП от 22.08.2011.
-- Зоны вложены одна в другую: Садовое кольцо внутри Третьего транспортного,
-- оно — внутри МКАД. Границы не задаются отдельно: постановление определяет
-- их через кольцевые магистрали, и геометрия зоны выводится из геометрии
-- соответствующего кольца.
CREATE TABLE IF NOT EXISTS restriction_zones (
    id                        SERIAL PRIMARY KEY,
    code                      VARCHAR(16)  NOT NULL UNIQUE,
    name                      VARCHAR(120) NOT NULL,
    short_name                VARCHAR(32)  NOT NULL,
    description               TEXT,
    level                     SMALLINT     NOT NULL,
    boundary_road_id          INTEGER REFERENCES road_segments (id) ON DELETE SET NULL,
    permit_required_from_tons NUMERIC(6, 2) NOT NULL,
    min_ecological_class      SMALLINT,
    seasonal_limit_tons       NUMERIC(6, 2),
    fine_rubles               INTEGER,
    legal_basis               VARCHAR(200),
    geom                      geometry(MultiPolygon, 4326),
    area_sq_km                NUMERIC(10, 2),
    perimeter_km              NUMERIC(8, 2),
    geometry_origin           VARCHAR(16),
    source_updated_at         TIMESTAMPTZ,
    CONSTRAINT zone_level_positive CHECK (level >= 1)
);

CREATE INDEX IF NOT EXISTS idx_zone_geom ON restriction_zones USING GIST (geom);

COMMENT ON TABLE  restriction_zones IS 'Зоны ограничения движения грузового транспорта (ПП № 379-ПП)';
COMMENT ON COLUMN restriction_zones.level IS '1 — внешняя зона; пропуск во внутреннюю действует во внешних';
COMMENT ON COLUMN restriction_zones.permit_required_from_tons IS 'РММ, начиная с которой требуется пропуск';
COMMENT ON COLUMN restriction_zones.seasonal_limit_tons IS 'Ограничение с 1 мая по 1 октября в выходные дни';
