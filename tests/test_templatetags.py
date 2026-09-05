"""Проверки фильтров разметки.

Метод: модульное тестирование. Фильтры выполняются при выводе каждой
страницы, и ошибка в них проявляется не отказом, а неверным числом
в таблице — то есть тем, что глазами не отличить от верного.
"""

from __future__ import annotations

import pytest
from core.templatetags.ff import bar, coverage_tone, lookup, origin, plural
from django.template import Context, Template


class TestPlural:
    """Форма существительного при числе."""

    FORMS = "округ,округа,округов"

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (1, "округ"),
            (2, "округа"),
            (4, "округа"),
            (5, "округов"),
            (0, "округов"),
            (12, "округов"),
            (21, "округ"),
            (22, "округа"),
            (25, "округов"),
            (111, "округов"),
            (112, "округов"),
            (114, "округов"),
        ],
    )
    def test_forms(self, count, expected):
        """Форма выбирается по последним двум разрядам числа."""
        assert plural(count, self.FORMS) == expected

    def test_teens_take_the_many_form(self):
        """Числа второго десятка получают форму множественного числа.

        Правило нельзя вывести из последней цифры: у 11 она та же, что у 21,
        а формы разные.
        """
        assert plural(11, self.FORMS) == "округов"
        assert plural(21, self.FORMS) == "округ"

    def test_missing_value(self):
        """Отсутствующее число не приводит к отказу вывода страницы."""
        assert plural(None, self.FORMS) == "округов"


class TestLookup:
    """Обращение к значению по ключу, известному во время вывода."""

    def test_returns_value(self):
        assert lookup({"storage": 42}, "storage") == 42

    def test_unknown_key(self):
        assert lookup({"storage": 42}, "network") is None

    def test_non_mapping(self):
        """Обращение не к словарю не роняет страницу."""
        assert lookup(None, "storage") is None


class TestOriginMark:
    """Отметка происхождения величины."""

    def test_known_kind_gets_a_mark(self):
        """Известное происхождение даёт код, подпись и пояснение."""

        mark = origin("modelled")
        assert mark["code"] == "modelled"
        assert str(mark["label"]) == "Смоделировано"
        assert "имитационной" in mark["meaning"]

    def test_unknown_kind_gets_no_mark(self):
        """Неизвестное происхождение отметки не даёт.

        Ложное указание на источник хуже отсутствия указания: читатель
        принимает решение, полагаясь на отметку.
        """

        assert origin("guessed") == {"code": "", "label": "", "meaning": ""}

    def test_mark_is_rendered_by_its_kind(self):
        """Разметка отметки несёт признак происхождения."""

        rendered = Template(
            "{% load ff %}{% origin 'measured' %}"
        ).render(Context({}))
        assert 'class="origin origin--measured"' in rendered
        assert "Измерено" in rendered

    def test_unknown_kind_renders_nothing(self):
        """Неизвестное происхождение не выводит ничего."""

        rendered = Template("{% load ff %}{% origin 'guessed' %}").render(Context({}))
        assert rendered.strip() == ""


class TestBar:
    """Полоса доли."""

    @pytest.mark.parametrize(
        ("value", "total", "expected"),
        [
            (0, 100, "0.0"),
            (50, 100, "50.0"),
            (7.4, 100, "7.4"),
            (1, 3, "33.3"),
            # Величина больше опорной полосу не переполняет.
            (150, 100, "100.0"),
            # Опорная величина, равная нулю, оставляет полосу пустой.
            (5, 0, "0.0"),
            (None, 100, "0.0"),
        ],
    )
    def test_width_is_written_for_a_style_rule(self, value, total, expected):
        """Ширина записывается с точкой в дробной части.

        Разделитель определяет действительность правила CSS: браузер
        отбрасывает ``width:7,4%`` целиком, и полоса занимает всю дорожку
        независимо от величины.
        """
        assert bar(value, total)["width"] == expected

    def test_width_stays_decimal_under_russian_locale(self):
        """Русская локаль на запись ширины не влияет."""
        from django.utils import translation

        with translation.override("ru"):
            assert "," not in bar(7.4, 100)["width"]

    def test_bar_is_hidden_from_screen_readers(self):
        """Полоса объявлена оформительской: она повторяет соседнее число."""
        rendered = Template("{% load ff %}{% bar 40 100 %}").render(Context({}))
        assert 'aria-hidden="true"' in rendered
        assert "width:40.0%" in rendered

    def test_tone_becomes_a_modifier(self):
        """Оттенок полосы задаётся модификатором класса."""
        rendered = Template(
            '{% load ff %}{% bar 40 100 tone="alert" %}'
        ).render(Context({}))
        assert "bar__fill--alert" in rendered

    def test_empty_tone_leaves_the_bar_neutral(self):
        """Пустой оттенок модификатора не добавляет."""
        rendered = Template("{% load ff %}{% bar 40 100 %}").render(Context({}))
        assert "bar__fill--" not in rendered


class TestCoverageTone:
    """Оттенок обеспеченности округа."""

    @pytest.mark.parametrize(
        ("share", "expected"),
        [(100, "ok"), (75, "ok"), (74.9, "warn"), (50, "warn"), (49, "alert"),
         (0, "alert"), (None, "muted")],
    )
    def test_share_maps_to_a_tone(self, share, expected):
        """Доля обеспеченности переводится в ступень шкалы."""
        assert coverage_tone(share) == expected
