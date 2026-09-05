"""Проверки доступности интерфейса.

Метод: функциональное тестирование разметки, выданной сервером.

Система рассчитана на работу без мыши и с программой чтения с экрана.
Требования эти проверяются по готовой странице, а не по шаблону: разметку
собирают вложенные шаблоны, теги и фильтры, и недостача появляется
на стыке, а не в одном месте.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

#: Разделы с реестрами, таблицами и формами — там, где недостача проявится.
PAGES = [
    "core:home", "core:object_list", "core:district_list", "core:road_list",
    "core:traffic", "core:incident_list", "core:route_list", "core:source_list",
    "core:type_list", "core:cargo_list", "core:methodology", "core:etl_log",
    "analytics:index", "analytics:spatial", "analytics:typology",
]

#: Открывающий тег заголовка столбца.
HEADER = re.compile(r"<th(?P<attrs>(?:\s[^>]*)?)>")

#: Строка реестра, ведущая на карточку записи.
CLICKABLE_ROW = re.compile(r'<tr data-href="[^"]*">(?P<body>.*?)</tr>', re.S)

#: Кнопка целиком: у кнопки со значком вместо надписи имя задаётся атрибутом.
BUTTON = re.compile(r"<button(?P<attrs>(?:\s[^>]*)?)>(?P<label>.*?)</button>", re.S)

#: Разметка внутри кнопки: рисунок именем не считается.
MARKUP = re.compile(r"<[^>]+>|\s+")


def render(client, route: str) -> str:
    response = client.get(reverse(route))
    assert response.status_code == 200, route
    return response.content.decode()


class TestKeyboard:
    """Работа без мыши."""

    def test_skip_link_is_reachable(self, client, full_dataset):
        """Переход к содержанию открывает обход навигации.

        Ссылка выведена за кромку окна и возвращается по фокусу: спрятанная
        приёмом для программ чтения с экрана, она получала бы фокус
        невидимой, и первое нажатие уводило бы работающего с клавиатуры
        неизвестно куда.
        """
        content = render(client, "core:home")
        assert 'class="skip-link" href="#content"' in content
        assert 'class="visually-hidden" href="#content"' not in content

    @pytest.mark.parametrize("route", PAGES)
    def test_record_is_reachable_without_a_mouse(
        self, client, full_dataset, route
    ):
        """Строка реестра ведёт на карточку записи настоящей ссылкой.

        Переход по нажатию на строку — удобство для мыши. Держаться на нём
        переход не может: до строки не добирается ни клавиша Tab,
        ни программа чтения с экрана.
        """
        mouse_only = [
            " ".join(row.group("body").split())[:80]
            for row in CLICKABLE_ROW.finditer(render(client, route))
            if "<a " not in row.group("body")
        ]
        assert mouse_only == [], f"{route}: строки без ссылки — {mouse_only}"


class TestScreenReader:
    """Разметка для программы чтения с экрана."""

    @pytest.mark.parametrize("route", PAGES)
    def test_table_headers_are_declared(self, client, full_dataset, route):
        """Заголовок столбца объявлен заголовком столбца.

        Без ``scope`` программа чтения с экрана не связывает ячейку
        с её заголовком, и таблица перестаёт быть таблицей: остаётся
        последовательность чисел без указания, что каждое означает.
        """
        loose = [
            header.group("attrs").strip()[:60]
            for header in HEADER.finditer(render(client, route))
            if "scope=" not in header.group("attrs")
        ]
        assert loose == [], f"{route}: заголовки без области — {loose}"

    @pytest.mark.parametrize("route", PAGES)
    def test_every_button_has_a_name(self, client, full_dataset, route):
        """Кнопка со значком вместо надписи названа атрибутом."""
        nameless = [
            button.group("attrs").strip()[:70]
            for button in BUTTON.finditer(render(client, route))
            if not MARKUP.sub("", button.group("label"))
            and "aria-label=" not in button.group("attrs")
            and "title=" not in button.group("attrs")
        ]
        assert nameless == [], f"{route}: кнопки без имени — {nameless}"

    @pytest.mark.parametrize("route", PAGES)
    def test_page_declares_its_language(self, client, full_dataset, route):
        """Страница объявляет язык: от него зависит произношение."""
        assert 'lang="ru"' in render(client, route)
