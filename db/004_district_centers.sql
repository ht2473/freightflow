-- =============================================================================
--  ИС «ГрузПоток» — обогащение справочника округов координатами центров
--
--  Исходный набор данных содержит только атрибутивную часть округов. Координаты
--  условных центров добавлены для корректной работы карты (подписи округов,
--  начальный охват, привязка агрегатов) по данным портала открытых данных
--  Москвы. Значения — географический центр застроенной части округа, WGS-84.
-- =============================================================================

BEGIN;

UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.620800, 55.753900), 4326) WHERE short_name = 'ЦАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.535000, 55.838600), 4326) WHERE short_name = 'САО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.620500, 55.863500), 4326) WHERE short_name = 'СВАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.775400, 55.787700), 4326) WHERE short_name = 'ВАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.754100, 55.692400), 4326) WHERE short_name = 'ЮВАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.654100, 55.621600), 4326) WHERE short_name = 'ЮАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.576200, 55.662400), 4326) WHERE short_name = 'ЮЗАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.443500, 55.728600), 4326) WHERE short_name = 'ЗАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.438000, 55.829000), 4326) WHERE short_name = 'СЗАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.214300, 55.991700), 4326) WHERE short_name = 'ЗелАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.210000, 55.557000), 4326) WHERE short_name = 'НАО';
UPDATE districts SET center = ST_SetSRID(ST_MakePoint(37.120000, 55.429700), 4326) WHERE short_name = 'ТАО';

COMMIT;
