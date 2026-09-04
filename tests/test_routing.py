"""Проверки расчётов по графу дорог.

Служба маршрутизации в проверках не участвует: её ответы подменяются
заготовками. Проверяется то, за что отвечает система, — состав запроса,
разбор ответа, поведение при отказе и заключение о пропусках по маршруту.
"""

from __future__ import annotations

import json
import urllib.error
from decimal import Decimal
from io import BytesIO

import pytest
from django.urls import reverse
from routing import polyline, profiles, service
from routing.client import (
    RouterNotConfiguredError,
    RouterUnavailableError,
    RoutingClient,
    is_configured,
)

#: Адрес службы, подставляемый в проверках. Обращений по нему не происходит:
#: обмен подменяется заготовкой.
ROUTER_URL = "http://router.test:8002"


@pytest.fixture
def configured(settings):
    """Настроенная служба маршрутизации."""
    settings.VALHALLA_URL = ROUTER_URL
    return settings


@pytest.fixture
def unconfigured(settings):
    """Служба маршрутизации не настроена."""
    settings.VALHALLA_URL = ""
    return settings


def stub(monkeypatch, response: dict) -> list[tuple[str, dict]]:
    """Подменить обмен со службой; возвращает журнал обращений."""
    calls: list[tuple[str, dict]] = []

    def fake_request(self, endpoint: str, body: str) -> dict:
        calls.append((endpoint, json.loads(body)))
        return response

    monkeypatch.setattr(RoutingClient, "_request", fake_request)
    return calls


# ---------------------------------------------------------------------------
#  Сжатая запись ломаной
# ---------------------------------------------------------------------------


class TestPolyline:
    """Разбор сжатой записи геометрии маршрута."""

    def test_known_record_is_decoded(self):
        """Запись из спецификации формата разворачивается в свои координаты."""
        points = polyline.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        expected = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]
        assert len(points) == len(expected)
        for actual, awaited in zip(points, expected, strict=True):
            assert actual == pytest.approx(awaited, abs=1e-3)

    def test_order_is_longitude_first(self):
        """Порядок координат приводится к принятому в системе."""
        lon, lat = polyline.decode("_p~iF~ps|U", precision=5)[0]
        assert -121 < lon < -120
        assert 38 < lat < 39

    def test_empty_record_gives_no_points(self):
        assert polyline.decode("") == []

    def test_truncated_record_returns_what_was_read(self):
        """Оборванная запись не роняет разбор."""
        points = polyline.decode("_p~iF~ps|U_ulL", precision=5)
        assert len(points) == 1
        assert points[0] == pytest.approx([-120.2, 38.5], abs=1e-3)


# ---------------------------------------------------------------------------
#  Профили транспорта
# ---------------------------------------------------------------------------


class TestProfiles:
    """Характеристики транспортного средства для расчёта."""

    def test_unknown_code_falls_back_to_default(self):
        assert profiles.get("несуществующий").code == profiles.DEFAULT_PROFILE

    def test_costing_options_carry_dimensions(self):
        options = profiles.get("semi").costing_options()["truck"]
        assert options["weight"] == 40.0
        assert options["height"] == 4.0
        assert options["width"] == 2.55

    def test_light_profile_is_below_permit_threshold(self):
        """Малотоннажный профиль не превышает порога, с которого нужен пропуск."""
        assert profiles.get("light").mass_tons <= Decimal("3.5")

    def test_choices_cover_all_profiles(self):
        assert len(profiles.choices()) == len(profiles.PROFILES)


# ---------------------------------------------------------------------------
#  Клиент
# ---------------------------------------------------------------------------


class TestClient:
    """Обращение к службе и обработка отказов."""

    def test_unconfigured_router_is_reported(self, unconfigured):
        assert is_configured() is False
        with pytest.raises(RouterNotConfiguredError):
            RoutingClient().status()

    def test_response_is_cached(self, configured, monkeypatch):
        """Повторный одинаковый запрос обслуживается без обращения к службе."""
        calls = stub(monkeypatch, {"trip": {}})
        client = RoutingClient()
        client.route({"locations": []})
        client.route({"locations": []})
        assert len(calls) == 1

    def test_different_requests_are_not_confused(self, configured, monkeypatch):
        calls = stub(monkeypatch, {"trip": {}})
        client = RoutingClient()
        client.route({"locations": [1]})
        client.route({"locations": [2]})
        assert len(calls) == 2

    def test_service_message_reaches_the_caller(self, configured, monkeypatch):
        """Отказ службы передаётся её же словами, а не общим кодом."""

        def failing(request, timeout=None):
            raise urllib.error.HTTPError(
                ROUTER_URL,
                400,
                "Bad Request",
                {},
                BytesIO(json.dumps({"error": "Точка не привязана к дороге"}).encode()),
            )

        monkeypatch.setattr("routing.client.urllib.request.urlopen", failing)
        with pytest.raises(RouterUnavailableError, match="не привязана"):
            RoutingClient(cache_ttl=0).route({"locations": []})

    def test_unreachable_service_is_reported(self, configured, monkeypatch):
        def failing(request, timeout=None):
            raise urllib.error.URLError("соединение отклонено")

        monkeypatch.setattr("routing.client.urllib.request.urlopen", failing)
        with pytest.raises(RouterUnavailableError, match="не отвечает"):
            RoutingClient(cache_ttl=0).status()


# ---------------------------------------------------------------------------
#  Расчёты
# ---------------------------------------------------------------------------

ISOCHRONE_RESPONSE = {
    "features": [
        {
            "type": "Feature",
            "properties": {"contour": 15, "metric": "time"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [37.55, 55.70],
                        [37.70, 55.70],
                        [37.70, 55.80],
                        [37.55, 55.80],
                        [37.55, 55.70],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"contour": 5},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [37.60, 55.74],
                        [37.63, 55.74],
                        [37.63, 55.76],
                        [37.60, 55.76],
                        [37.60, 55.74],
                    ]
                ],
            },
        },
        # Служба возвращает и линейные контуры, когда их запросили: они
        # площади не образуют и в расчёт доступности не идут.
        {"type": "Feature", "properties": {"contour": 5}, "geometry": {"type": "LineString"}},
    ]
}


class TestIsochrones:
    """Зоны доступности."""

    def test_request_carries_truck_costing(self, configured, monkeypatch):
        calls = stub(monkeypatch, ISOCHRONE_RESPONSE)
        service.isochrones(37.61, 55.75, [5, 15], profiles.get("semi"))
        endpoint, payload = calls[0]
        assert endpoint == "isochrone"
        assert payload["costing"] == "truck"
        assert payload["costing_options"]["truck"]["weight"] == 40.0
        assert payload["contours"] == [{"time": 5}, {"time": 15}]

    def test_areas_are_measured(self, configured, monkeypatch):
        stub(monkeypatch, ISOCHRONE_RESPONSE)
        contours = service.isochrones(37.61, 55.75, [5, 15])
        assert [item.minutes for item in contours] == [15, 5]
        assert contours[0].area_sq_km > contours[1].area_sq_km

    def test_intervals_are_bounded(self, configured, monkeypatch):
        """Число и длительность интервалов ограничены: каждый — обход графа."""
        calls = stub(monkeypatch, ISOCHRONE_RESPONSE)
        service.isochrones(37.61, 55.75, [1, 2, 3, 4, 5, 6, 7, 900])
        assert len(calls[0][1]["contours"]) == service.MAX_CONTOURS

    def test_empty_selection_falls_back_to_default(self, configured, monkeypatch):
        calls = stub(monkeypatch, ISOCHRONE_RESPONSE)
        service.isochrones(37.61, 55.75, [])
        assert len(calls[0][1]["contours"]) == len(service.DEFAULT_CONTOURS)


#: Ответ службы: маршрут из двух звеньев с одним указанием.
ROUTE_RESPONSE = {
    "trip": {
        "summary": {"length": 12.4, "time": 1500},
        "legs": [
            {
                # Ломаная из ответа службы: участок Театрального проезда
                # в центре Москвы, шесть вершин.
                "shape": "g`ziiBcz{vfA`@sMrCiw@j@_MZcFl@}C",
                "maneuvers": [
                    {"instruction": "Двигайтесь на север", "length": 1.2, "time": 180}
                ],
            }
        ],
    }
}


@pytest.fixture
def central_zone(db):
    """Зона ограничения, накрывающая центр города."""
    from core.models import RestrictionZone
    from geo import Geometry

    return RestrictionZone.objects.create(
        code="sk",
        name="Садовое кольцо",
        short_name="СК",
        level=3,
        permit_required_from_tons=Decimal("1.0"),
        fine_rubles=5000,
        geom=Geometry(
            "MULTIPOLYGON",
            [[[[37.5, 55.6], [37.8, 55.6], [37.8, 55.9], [37.5, 55.9], [37.5, 55.6]]]],
        ),
    )


class TestRoute:
    """Маршрут и условия проезда по нему."""

    def test_summary_is_read_from_the_service(self, configured, monkeypatch, db):
        stub(monkeypatch, ROUTE_RESPONSE)
        result = service.route([(37.61, 55.75), (37.53, 55.83)])
        assert result.distance_km == 12.4
        assert result.duration_min == 25.0
        assert result.steps[0]["instruction"] == "Двигайтесь на север"

    def test_geometry_is_decoded(self, configured, monkeypatch, db):
        stub(monkeypatch, ROUTE_RESPONSE)
        result = service.route([(37.61, 55.75), (37.53, 55.83)])
        assert result.geometry.geom_type == "LINESTRING"
        assert len(result.geometry.points) >= 2

    def test_permit_is_required_by_the_route_itself(
        self, configured, monkeypatch, central_zone
    ):
        """Пропуск определяется зонами, через которые проходит путь."""
        stub(monkeypatch, ROUTE_RESPONSE)
        result = service.route([(37.61, 55.75), (37.53, 55.83)], profiles.get("semi"))
        assert result.verdict.required_permit == central_zone
        assert result.verdict.fine_rubles == 5000

    def test_light_vehicle_needs_no_permit(self, configured, monkeypatch, central_zone):
        central_zone.permit_required_from_tons = Decimal("3.5")
        central_zone.save(update_fields=["permit_required_from_tons"])
        stub(monkeypatch, ROUTE_RESPONSE)
        result = service.route([(37.61, 55.75), (37.53, 55.83)], profiles.get("light"))
        assert result.verdict.required_permit is None

    def test_route_without_legs_is_reported(self, configured, monkeypatch, db):
        stub(monkeypatch, {"trip": {"legs": []}})
        with pytest.raises(service.RoutingError, match="не найден"):
            service.route([(37.61, 55.75), (37.53, 55.83)])


class TestAvailability:
    """Состояние службы, показываемое пользователю."""

    def test_unconfigured_router_is_named(self, unconfigured):
        state = service.availability()
        assert state["configured"] is False
        assert "по прямой" in state["message"]

    def test_unreachable_router_is_named(self, configured, monkeypatch):
        def failing(self, endpoint, body):
            raise RouterUnavailableError("Служба маршрутизации не отвечает")

        monkeypatch.setattr(RoutingClient, "_request", failing)
        state = service.availability()
        assert state == {
            "configured": True,
            "reachable": False,
            "message": "Служба маршрутизации не отвечает",
        }

    def test_working_router_reports_version(self, configured, monkeypatch):
        stub(monkeypatch, {"version": "3.5.1"})
        state = service.availability()
        assert state["reachable"] is True
        assert state["version"] == "3.5.1"


# ---------------------------------------------------------------------------
#  Конечные точки
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEndpoints:
    """Обслуживание расчётов по сети."""

    def test_isochrones_are_served(self, client, configured, monkeypatch):
        stub(monkeypatch, ISOCHRONE_RESPONSE)
        response = client.get(
            reverse("core:routing_isochrones"), {"point": "37.61,55.75", "minutes": "5,15"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "FeatureCollection"
        assert payload["features"][0]["properties"]["minutes"] == 15

    def test_route_is_served(self, client, configured, monkeypatch, central_zone):
        stub(monkeypatch, ROUTE_RESPONSE)
        response = client.get(
            reverse("core:routing_route"),
            {"from": "37.61,55.75", "to": "37.53,55.83", "profile": "semi"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["distance_km"] == 12.4
        assert payload["permit"] == "СК"
        assert payload["profile"]["mass_tons"] == 40.0

    def test_missing_point_is_rejected(self, client, configured):
        assert client.get(reverse("core:routing_isochrones")).status_code == 400

    def test_malformed_point_is_rejected(self, client, configured):
        response = client.get(reverse("core:routing_isochrones"), {"point": "север"})
        assert response.status_code == 400

    def test_unconfigured_router_answers_plainly(self, client, unconfigured):
        """Отсутствие службы отличается от её отказа кодом состояния."""
        response = client.get(reverse("core:routing_isochrones"), {"point": "37.61,55.75"})
        assert response.status_code == 501
        assert response.json()["configured"] is False

    def test_unavailable_router_answers_plainly(self, client, configured, monkeypatch):
        def failing(self, endpoint, body):
            raise RouterUnavailableError("Служба маршрутизации не отвечает")

        monkeypatch.setattr(RoutingClient, "_request", failing)
        response = client.get(reverse("core:routing_route"),
                              {"from": "37.61,55.75", "to": "37.53,55.83"})
        assert response.status_code == 503
        assert response.json() == {
            "configured": True,
            "error": "Служба маршрутизации не отвечает",
        }

    def test_status_is_served(self, client, unconfigured):
        assert client.get(reverse("core:routing_status")).json()["configured"] is False


@pytest.mark.django_db
class TestMapPage:
    """Страница карты сообщает о состоянии расчёта по графу."""

    def test_tools_appear_when_router_is_configured(self, client, configured, full_dataset):
        content = client.get(reverse("core:map")).content.decode()
        assert 'id="map-isochrone"' in content
        assert 'id="map-route"' in content

    def test_absence_of_router_is_stated(self, client, unconfigured, full_dataset):
        content = client.get(reverse("core:map")).content.decode()
        assert 'id="map-isochrone"' not in content
        assert "по прямой" in content
