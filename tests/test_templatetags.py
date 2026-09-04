"""Проверки фильтров разметки.

Метод: модульное тестирование. Фильтры выполняются при выводе каждой
страницы, и ошибка в них проявляется не отказом, а неверным числом
в таблице — то есть тем, что глазами не отличить от верного.
"""

from __future__ import annotations

import pytest
from core.templatetags.ff import lookup, plural


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
