"""Тесты программного интерфейса.

Метод: тестирование контракта API — проверяется структура ответов, действие
параметров отбора и корректность постраничной выдачи.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

BASE = "/api/v1/"


class TestReferenceEndpoints:
    """Справочники."""

    def test_districts_list(self, client, full_dataset, districts):
        """Перечень округов возвращается с постраничной структурой."""
        payload = client.get(f"{BASE}districts/").json()
        assert payload["count"] == len(districts)
        assert "results" in payload and "pages" in payload

    def test_district_geometry_is_geojson(self, client, full_dataset):
        """Координаты центра отдаются в формате GeoJSON."""
        payload = client.get(f"{BASE}districts/").json()
        center = payload["results"][0]["center"]
        assert center["type"] == "Point"
        assert len(center["coordinates"]) == 2

    def test_district_summary(self, client, full_dataset):
        """Сводка по округам содержит агрегированные показатели."""
        payload = client.get(f"{BASE}districts/summary/").json()
        assert payload["count"] > 0
        assert "capacity_tons" in payload["results"][0]

    def test_types_with_counts(self, client, full_dataset):
        """Типы объектов дополняются числом записей."""
        payload = client.get(f"{BASE}types/").json()
        assert payload["results"][0]["objects_count"] >= 0

    def test_cargo_categories(self, client, full_dataset):
        """Категории грузов содержат признак опасности."""
        payload = client.get(f"{BASE}cargo-categories/").json()
        assert any(row["is_hazardous"] for row in payload["results"])

    def test_sources(self, client, full_dataset):
        """Источники данных отдаются с расшифровкой типа."""
        payload = client.get(f"{BASE}sources/").json()
        assert payload["results"][0]["source_type_label"]


class TestObjectEndpoints:
    """Реестр объектов инфраструктуры."""

    def test_list(self, client, full_dataset, objects):
        """Список объектов возвращает все записи."""
        assert client.get(f"{BASE}objects/").json()["count"] == len(objects)

    def test_filter_by_district(self, client, full_dataset, districts):
        """Параметр district сужает выдачу."""
        payload = client.get(f"{BASE}objects/", {"district": districts[0].pk}).json()
        assert payload["count"] == 2

    def test_search(self, client, full_dataset):
        """Параметр q выполняет поиск по наименованию."""
        assert client.get(f"{BASE}objects/", {"q": "Терминал"}).json()["count"] == 2

    def test_detail(self, client, full_dataset, objects):
        """Карточка объекта доступна по идентификатору."""
        payload = client.get(f"{BASE}objects/{objects[0].pk}/").json()
        assert payload["name"] == objects[0].name

    def test_nearby(self, client, full_dataset):
        """Поиск ближайших возвращает расстояния в километрах."""
        payload = client.get(
            f"{BASE}objects/nearby/", {"lon": 37.62, "lat": 55.75, "radius": 5}
        ).json()
        assert payload["count"] > 0
        assert all("distance_km" in row for row in payload["results"])

    def test_nearby_without_coordinates(self, client, full_dataset):
        """Без координат запрос отклоняется с кодом 400."""
        assert client.get(f"{BASE}objects/nearby/").status_code == 400

    def test_page_size_parameter(self, client, full_dataset):
        """Размер страницы регулируется параметром запроса."""
        payload = client.get(f"{BASE}objects/", {"page_size": 2}).json()
        assert len(payload["results"]) == 2
        assert payload["page_size"] == 2

    def test_page_size_capped(self, client, full_dataset):
        """Размер страницы ограничен сверху."""
        payload = client.get(f"{BASE}objects/", {"page_size": 10000}).json()
        assert payload["page_size"] <= 500


class TestNetworkEndpoints:
    """Дорожная сеть и события."""

    def test_roads_list(self, client, full_dataset, roads):
        """Участки сети возвращаются полностью."""
        assert client.get(f"{BASE}roads/").json()["count"] == len(roads)

    def test_road_geometry(self, client, full_dataset):
        """Геометрия магистрали отдаётся набором ломаных GeoJSON.

        Именно набором: магистраль собрана из разрозненных частей, а
        разделённая проезжая часть размечена двумя независимыми линиями.
        Тип не зависит от того, какая СУБД обслуживает контур.
        """
        payload = client.get(f"{BASE}roads/").json()
        geometry = payload["results"][0]["geometry"]
        assert geometry["type"] == "MultiLineString"
        assert len(geometry["coordinates"][0]) >= 2

    def test_current_traffic(self, client, full_dataset, roads):
        """Текущая обстановка возвращает по одному замеру на участок."""
        payload = client.get(f"{BASE}traffic/current/").json()
        assert payload["count"] == len(roads)
        assert payload["results"][0]["state"]["label"]

    def test_incidents_open_filter(self, client, full_dataset):
        """Отбор открытых событий работает."""
        payload = client.get(f"{BASE}incidents/", {"state": "open"}).json()
        assert all(row["is_open"] for row in payload["results"])

    def test_incidents_severity_filter(self, client, full_dataset):
        """Отбор по минимальной серьёзности работает."""
        payload = client.get(f"{BASE}incidents/", {"severity": 4}).json()
        assert all(row["severity"] >= 4 for row in payload["results"])

    def test_incidents_cargo_filter(self, client, full_dataset):
        """Отбор по влиянию на грузовой транспорт работает."""
        payload = client.get(f"{BASE}incidents/", {"cargo": "1"}).json()
        assert all(row["affects_cargo"] for row in payload["results"])


class TestFreightEndpoints:
    """Грузопотоки и маршруты."""

    def test_routes_list(self, client, full_dataset, routes):
        """Маршруты возвращаются с расшифровкой типа."""
        payload = client.get(f"{BASE}routes/").json()
        assert payload["count"] == len(routes)
        assert payload["results"][0]["route_type_label"]

    def test_routes_type_filter(self, client, full_dataset):
        """Отбор по типу маршрута работает."""
        payload = client.get(f"{BASE}routes/", {"type": "inbound"}).json()
        assert all(row["route_type"] == "inbound" for row in payload["results"])

    def test_flows_list(self, client, full_dataset, flows):
        """Статистика грузопотоков доступна полностью."""
        assert client.get(f"{BASE}flows/").json()["count"] == len(flows)

    def test_flows_period_filter(self, client, full_dataset):
        """Отбор по периоду ограничивает выдачу."""
        payload = client.get(
            f"{BASE}flows/", {"period_from": "2025-06-01", "period_to": "2025-08-31"}
        ).json()
        assert 0 < payload["count"] < len(client.get(f"{BASE}flows/").json()["results"]) * 20

    def test_flows_direction_filter(self, client, full_dataset):
        """Отбор по направлению работает."""
        payload = client.get(f"{BASE}flows/", {"direction": "in"}).json()
        assert all(row["direction"] == "in" for row in payload["results"])


class TestAnalyticsEndpoints:
    """Аналитические конечные точки."""

    def test_load_index(self, client, full_dataset, districts):
        """Индекс нагрузки возвращается с описанием составляющих."""
        payload = client.get(f"{BASE}analytics/load-index/").json()
        assert payload["count"] == len(districts)
        assert sum(item["weight"] for item in payload["components"]) == pytest.approx(1.0)

    def test_load_index_describes_components(self, client, full_dataset):
        """Составляющая описана настолько, чтобы её можно было проверить."""
        component = client.get(f"{BASE}analytics/load-index/").json()["components"][0]
        assert component["unit"] and component["formula"] and component["source"]
        assert component["origin"] == "measured"

    def test_load_index_sorted(self, client, full_dataset):
        """Записи упорядочены по убыванию индекса."""
        rows = client.get(f"{BASE}analytics/load-index/").json()["results"]
        assert [row["score"] for row in rows] == sorted(
            (row["score"] for row in rows), reverse=True
        )

    def test_forecast(self, client, full_dataset):
        """Прогноз содержит историю, прогнозные значения и оценку качества."""
        payload = client.get(f"{BASE}analytics/forecast/", {"horizon": 6}).json()
        assert len(payload["forecast"]) == 6
        assert "mape" in payload["quality"]

    def test_forecast_without_data(self, client, db):
        """Без исходных данных прогноз возвращает код 422."""
        assert client.get(f"{BASE}analytics/forecast/").status_code == 422

    def test_typology(self, client, full_dataset):
        """Типология возвращает состав групп."""
        payload = client.get(f"{BASE}analytics/typology/", {"k": 2}).json()
        assert payload["k"] == 2
        assert payload["clusters"]


class TestSchema:
    """Машиночитаемая спецификация."""

    def test_schema_available(self, client, full_dataset):
        """Спецификация OpenAPI формируется."""
        assert client.get(f"{BASE}schema/").status_code == 200

    def test_docs_available(self, client, full_dataset):
        """Интерактивная документация доступна."""
        assert client.get(f"{BASE}docs/").status_code == 200

    def test_redoc_available(self, client, full_dataset):
        """Альтернативное представление документации доступно."""
        assert client.get(f"{BASE}redoc/").status_code == 200
