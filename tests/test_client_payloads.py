"""Проверка данных, передаваемых со страниц в клиентские сценарии.

Метод: негативное тестирование локализации в сочетании с функциональной
проверкой разметки.

Числовые значения, подставленные в разметку напрямую, проходят через
локализацию: при русском языке ``55.7522`` выводится как ``55,7522``.
Встроенный в страницу JSON от этого перестаёт быть валидным, а массив
координат — распадаться на вдвое большее число элементов, оставаясь при этом
синтаксически корректным. Проверять поэтому нужно не только разбор,
но и состав разобранного.

Правило, которое закрепляет набор: всё, что страница передаёт сценарию,
обязано разбираться как JSON при **каждом** поддерживаемом языке
и сохранять размерность величин.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse
from django.utils import translation

pytestmark = pytest.mark.django_db

#: Языки, на которых система обязана работать одинаково.
LANGUAGES = ["ru", "en"]

#: Разбор содержимого элемента ``<script type="application/json" id="…">``,
#: который формирует шаблонный фильтр ``json_script``.
_JSON_SCRIPT_RE = (
    r'<script id="{ident}" type="application/json">(?P<payload>.*?)</script>'
)


def json_script_payload(html: str, ident: str) -> dict | list:
    """Извлечь и разобрать данные, переданные через ``json_script``."""
    match = re.search(_JSON_SCRIPT_RE.format(ident=re.escape(ident)), html, re.DOTALL)
    assert match, f"на странице нет элемента данных «{ident}»"
    # json_script экранирует угловые скобки и амперсанд, чтобы содержимое
    # нельзя было выдать за разметку; на разбор JSON это не влияет.
    raw = match.group("payload")
    raw = raw.replace("\\u003C", "<").replace("\\u003E", ">").replace("\\u0026", "&")
    return json.loads(raw)


class TestMapSettings:
    """Настройки карты, передаваемые странице ``core:map``."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_settings_parse_as_json(self, client, full_dataset, language):
        """Настройки карты разбираются как JSON на каждом языке."""
        with translation.override(language):
            response = client.get(reverse("core:map"), headers={"accept-language": language})
        assert response.status_code == 200
        # Разбор не должен зависеть от языка интерфейса.
        json_script_payload(response.content.decode(), "map-settings")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_center_is_pair_of_numbers(self, client, full_dataset, language):
        """Центр карты — пара вещественных чисел, а не четыре целых.

        При десятичной запятой ``[55,7522, 37,6156]`` остаётся синтаксически
        корректным массивом JSON, поэтому проверять надо не разбор, а состав:
        именно длина массива отличает исправную страницу от сломанной.
        """
        with translation.override(language):
            response = client.get(reverse("core:map"))
        settings_payload = json_script_payload(response.content.decode(), "map-settings")

        center = settings_payload["center"]
        assert len(center) == 2, f"центр карты разобран как {center}"
        latitude, longitude = center
        assert 55.0 < latitude < 56.5, latitude
        assert 36.5 < longitude < 38.5, longitude

    def test_source_addresses_present(self, client, full_dataset):
        """Адреса источника тайлов и поиска по точке переданы клиенту."""
        response = client.get(reverse("core:map"))
        payload = json_script_payload(response.content.decode(), "map-settings")
        assert set(payload["urls"]) == {"tilejson", "nearby"}
        for url in payload["urls"].values():
            assert url.startswith("/")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_choropleth_limits_are_numbers(self, client, full_dataset, language):
        """Границы шкалы раскраски остаются числами на любом языке.

        Значение шкалы задаёт цвет заливки: локализованная запятая сделала бы
        из одного числа два, и раскраска перестала бы соответствовать данным.
        """
        with translation.override(language):
            response = client.get(reverse("core:map"))
        payload = json_script_payload(response.content.decode(), "map-settings")
        for metric in payload["choropleth"]:
            assert isinstance(metric["max"], (int, float)), metric

    def test_district_labels_carry_coordinates(self, client, full_dataset):
        """Подписи округов приходят с координатами их центров."""
        response = client.get(reverse("core:map"))
        payload = json_script_payload(response.content.decode(), "map-settings")
        assert payload["districts"]
        for district in payload["districts"]:
            assert 36.5 < district["lon"] < 38.5
            assert 55.0 < district["lat"] < 56.5

    def test_no_raw_numbers_in_data_attributes(self, client, full_dataset):
        """На странице карты не осталось чисел, подставленных в разметку.

        Проверка охраняет само правило, а не один его случай: появление
        ``data-settings`` снова открыло бы дорогу локализованным числам.
        """
        content = client.get(reverse("core:map")).content.decode()
        assert "data-settings" not in content


class TestGeometryPayloads:
    """Геометрия, передаваемая карточкам участков и маршрутов."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_road_geometry_parses(self, client, roads, language):
        """Трасса участка разбирается как GeoJSON на каждом языке."""
        with translation.override(language):
            response = client.get(reverse("core:road_detail", args=[roads[0].pk]))
        geometry = json_script_payload(response.content.decode(), "road-line")
        assert geometry["type"] == "LineString"
        assert len(geometry["coordinates"]) >= 2
        # Координата — пара чисел; при локализации она распалась бы на четыре.
        for point in geometry["coordinates"]:
            assert len(point) == 2
            assert all(isinstance(value, (int, float)) for value in point)

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_route_geometry_parses(self, client, routes, language):
        """Трасса маршрута разбирается как GeoJSON на каждом языке."""
        with translation.override(language):
            response = client.get(reverse("core:route_detail", args=[routes[0].pk]))
        geometry = json_script_payload(response.content.decode(), "route-line")
        assert geometry["type"] == "LineString"
        assert len(geometry["coordinates"]) >= 2

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_point_coordinates_are_unlocalized(self, client, objects, language):
        """Координаты объекта выведены с десятичной точкой.

        Карточка объекта передаёт широту и долготу отдельными атрибутами,
        а клиент разбирает их через ``parseFloat``. Десятичная запятая
        обрезала бы дробную часть и сместила бы отметку на карте.
        """
        with translation.override(language):
            response = client.get(reverse("core:object_detail", args=[objects[0].pk]))
        content = response.content.decode()

        for attribute in ("data-lat", "data-lon"):
            match = re.search(rf'{attribute}="([^"]+)"', content)
            assert match, f"на карточке нет атрибута {attribute}"
            value = match.group(1)
            assert "," not in value, f"{attribute}={value!r} содержит десятичную запятую"
            float(value)
