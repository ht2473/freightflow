"""Проверки отдачи векторных тайлов карты.

Методы: функциональное тестирование конечных точек, проверка граничных
значений масштаба, контроль числа обращений к базе данных.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from geo import Geometry
from geo.tiles import tile_of
from mvt_reader import read_tile

pytestmark = pytest.mark.django_db

# Тайл, накрывающий центр города на масштабе городского обзора.
CITY = (37.61, 55.75)


def tile_url(z: int, point=CITY) -> str:
    """Адрес тайла, в который попадает точка."""
    x, y = tile_of(point[0], point[1], z)
    return reverse("core:map_tile", kwargs={"z": z, "x": x, "y": y})


@pytest.fixture
def bounded_districts(districts):
    """Округа с границами: без них слой округов пуст."""
    spans = [
        (37.55, 55.70, 37.70, 55.80),
        (37.45, 55.80, 37.65, 55.90),
        (37.55, 55.55, 37.75, 55.70),
    ]
    for district, (min_lon, min_lat, max_lon, max_lat) in zip(districts, spans, strict=True):
        district.geom = Geometry(
            "MULTIPOLYGON",
            [
                [
                    [
                        [min_lon, min_lat],
                        [max_lon, min_lat],
                        [max_lon, max_lat],
                        [min_lon, max_lat],
                        [min_lon, min_lat],
                    ]
                ]
            ],
        )
        district.save(update_fields=["geom"])
    return districts


class TestVectorTile:
    """Содержимое тайла."""

    def test_tile_carries_the_layers_of_its_zoom(self, client, bounded_districts, objects, roads):
        response = client.get(tile_url(10))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.mapbox-vector-tile"

        layers = read_tile(response.content)
        assert {"districts", "roads", "objects"} <= set(layers)
        assert all(layer["extent"] == 4096 for layer in layers.values())

    def test_object_properties_reach_the_client(self, client, objects):
        layers = read_tile(client.get(tile_url(12)).content)
        names = {
            feature["properties"]["name"] for feature in layers["objects"]["features"]
        }
        assert "Склад «Центр»" in names

        centre = next(
            feature
            for feature in layers["objects"]["features"]
            if feature["properties"]["name"] == "Склад «Центр»"
        )
        assert centre["properties"]["type"] == "Склад"
        assert centre["properties"]["district"] == "ЦАО"
        assert centre["id"] == next(o.id for o in objects if o.name == "Склад «Центр»")

    def test_layer_appears_only_from_its_zoom(self, client, objects, roads):
        """Реестр объектов не попадает в тайл обзорного масштаба."""
        overview = read_tile(client.get(tile_url(8)).content)
        assert "objects" not in overview
        assert "roads" in overview

        detailed = read_tile(client.get(tile_url(12)).content)
        assert "objects" in detailed

    def test_backdrop_layers_are_served(self, client, districts):
        """Вода и зелень приходят тем же тайлом, что и данные поверх них."""
        from core.choices import NaturalKind
        from core.models import NaturalArea

        for kind, name, shift in ((NaturalKind.WATER, "Река", 0.0), (NaturalKind.GREEN, "Парк", 0.01)):
            NaturalArea.objects.create(
                kind=kind,
                name=name,
                area_sq_m=200_000,
                geom=Geometry(
                    "MULTIPOLYGON",
                    [
                        [
                            [
                                [37.60 + shift, 55.74],
                                [37.62 + shift, 55.74],
                                [37.62 + shift, 55.76],
                                [37.60 + shift, 55.76],
                                [37.60 + shift, 55.74],
                            ]
                        ]
                    ],
                ),
            )

        layers = read_tile(client.get(tile_url(12)).content)
        assert layers["water"]["features"][0]["properties"]["name"] == "Река"
        assert layers["green"]["features"][0]["properties"]["name"] == "Парк"

    def test_road_carries_freight_frame_flag(self, client, roads):
        """Признак грузового каркаса приходит вместе с магистралью."""
        roads[0].in_freight_frame = True
        roads[0].save(update_fields=["in_freight_frame"])

        features = read_tile(client.get(tile_url(12)).content)["roads"]["features"]
        marked = {
            feature["properties"]["name"]: feature["properties"].get("freight_frame")
            for feature in features
        }
        assert marked[roads[0].name] is True
        # Магистраль, о которой сведений нет, не объявляется исключённой:
        # свойство отсутствует, а не равно «нет».
        assert marked.get(roads[1].name) is None

    def test_zoom_outside_the_range_is_not_served(self, client):
        assert client.get(tile_url(4)).status_code == 404
        assert client.get(tile_url(17)).status_code == 404

    def test_tile_without_data_is_empty(self, client, objects):
        """За пределами города квадрат существует, но пуст."""
        response = client.get(tile_url(10, point=(2.35, 48.85)))
        assert response.status_code == 204
        assert response.content == b""

    def test_district_carries_its_index(self, client, bounded_districts, full_dataset):
        layers = read_tile(client.get(tile_url(9)).content)
        properties = layers["districts"]["features"][0]["properties"]
        assert properties["short_name"]
        assert properties["index"] is not None
        assert properties["rank"] >= 1

    def test_repeated_request_is_served_from_cache(
        self, client, django_assert_num_queries, objects, roads
    ):
        url = tile_url(11)
        assert client.get(url).status_code == 200
        with django_assert_num_queries(0):
            assert client.get(url).status_code == 200

    def test_loaded_data_replaces_the_tile(self, client, objects, infrastructure_types, districts):
        """Загрузка данных делает собранные тайлы недействительными."""
        from core.models import InfrastructureObject
        from core.selectors import invalidate_caches

        url = tile_url(12)
        before = read_tile(client.get(url).content)["objects"]["features"]

        InfrastructureObject.objects.create(
            name="Склад «Новый»",
            type=infrastructure_types[0],
            district=districts[0],
            geom=Geometry.point(37.611, 55.751),
        )
        invalidate_caches()

        after = read_tile(client.get(url).content)["objects"]["features"]
        assert len(after) == len(before) + 1


class TestTileJson:
    """Описание источника тайлов."""

    def test_describes_every_layer_of_the_registry(self, client):
        from core.tilelayers import LAYERS

        payload = client.get(reverse("core:map_tilejson")).json()
        assert payload["tilejson"] == "3.0.0"
        assert [layer["id"] for layer in payload["vector_layers"]] == [
            layer.name for layer in LAYERS
        ]

    def test_tile_address_is_a_template(self, client):
        payload = client.get(reverse("core:map_tilejson")).json()
        assert payload["tiles"][0].endswith("/tiles/{z}/{x}/{y}.pbf")
        assert payload["minzoom"] < payload["maxzoom"]

    def test_fields_are_described(self, client):
        payload = client.get(reverse("core:map_tilejson")).json()
        objects = next(item for item in payload["vector_layers"] if item["id"] == "objects")
        assert objects["fields"]["capacity"] == "Мощность хранения, т"
