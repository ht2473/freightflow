"""Сборка магистралей улично-дорожной сети из частей OpenStreetMap.

Метод: модульное тестирование, проверка граничных случаев разметки.

Дорога в исходных данных разбита на части по перекрёсткам, сменам
характеристик и направлениям движения. Сборка их в одну запись — место,
где ошибка тиха: реестр наполнится, но окажется раздробленным или, наоборот,
склеит разные дороги, и заметно это станет только при взгляде на карту.
"""

from __future__ import annotations

import pytest
from etl.osm import roads


class TestNameNormalization:
    """Приведение наименования части к наименованию магистрали."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("МКАД, 68-й километр", "МКАД"),
            ("МКАД, 8-й километр", "МКАД"),
            ("МКАД, 109-й километр", "МКАД"),
            ("МКАД,  4-й  километр", "МКАД"),
        ],
    )
    def test_kilometre_marks_are_stripped(self, raw, expected):
        """Покилометровая разметка сводится к наименованию магистрали.

        Без этого МКАД распался бы в реестре на сто девять записей
        по километру каждая, и магистрали как таковой в нём не было бы.
        """
        assert roads.normalize_road_name(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Садовое кольцо (внутренняя сторона)", "Садовое кольцо"),
            ("Садовое кольцо (внешняя сторона)", "Садовое кольцо"),
        ],
    )
    def test_side_marks_are_stripped(self, raw, expected):
        """Сторона кольца обозначает направление, а не отдельную дорогу."""
        assert roads.normalize_road_name(raw) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "Варшавское шоссе",
            "Третье транспортное кольцо",
            "1-я Тверская-Ямская улица",
            "проспект Багратиона",
        ],
    )
    def test_ordinary_names_are_untouched(self, name):
        """Обычное наименование не изменяется.

        Проверка существенна: «1-я Тверская-Ямская улица» содержит порядковое
        числительное с тем же окончанием, что и покилометровая разметка.
        """
        assert roads.normalize_road_name(name) == name

    def test_empty_name(self):
        assert roads.normalize_road_name("") == ""


class TestGroupingKey:
    """Ключ, по которому части объединяются в магистраль."""

    def test_name_preferred(self):
        assert roads._road_key({"name": "Каширское шоссе", "ref": "А-105"}) == (
            "Каширское шоссе"
        )

    def test_ref_used_without_name(self):
        """Безымянная часть с учётным номером относится к трассе."""
        assert roads._road_key({"ref": "М-4"}) == "М-4"

    def test_unnamed_part_is_not_grouped(self):
        """Съезды и развязочные связки самостоятельной магистралью не являются."""
        assert roads._road_key({"highway": "trunk_link"}) is None


class TestTagParsing:
    """Разбор числовых характеристик разметки."""

    @pytest.mark.parametrize(
        "value,expected",
        [("60", 60), ("90", 90), ("60 mph", 60), ("", None), (None, None)],
    )
    def test_speed_values(self, value, expected):
        tags = {"maxspeed": value} if value is not None else {}
        assert roads._int_tag(tags, "maxspeed") == expected

    def test_non_numeric_value_is_discarded(self):
        """Условное обозначение вместо числа отбрасывается.

        В разметке встречается ``RU:urban`` — ссылка на общее правило,
        а не конкретное ограничение. Подставлять вместо него значение
        по умолчанию нельзя.
        """
        assert roads._int_tag({"maxspeed": "RU:urban"}, "maxspeed") is None

    def test_multi_value_lanes(self):
        """Из записи «2;3» берётся первое значение."""
        assert roads._int_tag({"lanes": "2;3"}, "lanes") == 2


class TestHgvAccess:
    """Признак допуска грузового движения."""

    def test_absent_marking_leaves_unknown(self):
        """Без явной разметки признак остаётся неопределённым.

        Ограничения движения грузового транспорта в Москве задаются
        нормативным актом, а не разметкой OpenStreetMap. Умолчание здесь
        создало бы ложную уверенность.
        """
        assert roads._hgv_allowed([{"highway": "primary"}]) is None

    def test_prohibition_wins(self):
        """Запрет хотя бы на одном участке распространяется на магистраль."""
        assert roads._hgv_allowed([{"hgv": "yes"}, {"hgv": "no"}]) is False

    def test_explicit_permission(self):
        assert roads._hgv_allowed([{"hgv": "designated"}]) is True


class TestRoadAssembly:
    """Сборка записи магистрали из частей."""

    @staticmethod
    def way(points, **tags):
        return {
            "type": "way",
            "geometry": [{"lon": x, "lat": y} for x, y in points],
            "tags": {"highway": "primary", **tags},
        }

    def test_parts_are_merged_into_one_record(self):
        """Части объединяются в набор линий, длины складываются."""
        ways = [
            self.way([(37.60, 55.75), (37.65, 55.75)], name="Тестовое шоссе"),
            self.way([(37.70, 55.75), (37.75, 55.75)], name="Тестовое шоссе"),
        ]
        record = roads._build_road("Тестовое шоссе", ways, [], None, None)

        assert record["geom"].geom_type == "MULTILINESTRING"
        assert record["segment_count"] == 2
        assert record["length_km"] > 5

    def test_most_significant_class_wins(self):
        """У магистрали из частей разных классов берётся высший класс."""
        ways = [
            self.way([(37.60, 55.75), (37.70, 55.75)], name="Т", highway="primary"),
            self.way([(37.70, 55.75), (37.80, 55.75)], name="Т", highway="motorway"),
        ]
        record = roads._build_road("Т", ways, [], None, None)
        assert record["road_class"] == roads.RoadClass.HIGHWAY

    def test_lanes_take_maximum(self):
        """Число полос — наибольшее из размеченных: оно задаёт пропускную способность."""
        ways = [
            self.way([(37.60, 55.75), (37.70, 55.75)], name="Т", lanes="3"),
            self.way([(37.70, 55.75), (37.80, 55.75)], name="Т", lanes="6"),
        ]
        assert roads._build_road("Т", ways, [], None, None)["lanes"] == 6

    def test_short_link_is_rejected(self):
        """Короткая связка в реестр магистралей не попадает."""
        ways = [self.way([(37.600, 55.750), (37.6005, 55.7501)], name="Съезд")]
        assert roads._build_road("Съезд", ways, [], None, None) is None

    def test_length_is_marked_measured(self):
        """Протяжённость помечается как измеренная величина."""
        ways = [self.way([(37.60, 55.75), (37.70, 55.75)], name="Т")]
        record = roads._build_road("Т", ways, [], None, None)
        assert record["length_origin"] == "measured"

    def test_geometry_without_points_is_rejected(self):
        """Часть без координат записи не образует."""
        ways = [{"type": "way", "geometry": [], "tags": {"name": "Т", "highway": "primary"}}]
        assert roads._build_road("Т", ways, [], None, None) is None
