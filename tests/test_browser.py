"""Проверки в настоящем браузере.

Метод: системное тестирование методом чёрного ящика, динамическое.

Тестовый клиент Django получает разметку, но не исполняет сценарии и не
разбирает встроенные в страницу данные, поэтому поведение клиентской части
остаётся вне его охвата. Здесь страницы открываются управляемым браузером,
и любое сообщение об ошибке в консоли считается отказом.

Внешняя сеть при этом не используется: запросы к тайловому серверу и к
службе шрифтов перехватываются и обслуживаются заглушками. Проверка не
должна зависеть от доступности стороннего сервиса — иначе она начнёт
падать по причинам, к приложению не относящимся.

Запуск:
    pytest tests/test_browser.py
Требуется однократная установка браузера:
    python -m playwright install chromium
"""

from __future__ import annotations

import base64
import os

import pytest

# Синхронный интерфейс Playwright работает поверх цикла событий, и Django
# считает такой вызов небезопасным: защита рассчитана на прикладной код,
# случайно обратившийся к базе из сопрограммы. Здесь обращения выполняет сам
# набор проверок, из отдельного потока сервера, поэтому защита снимается.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

pytest.importorskip("playwright", reason="Playwright не установлен")

pytestmark = [pytest.mark.django_db, pytest.mark.browser]

#: Прозрачный однопиксельный PNG — заглушка тайла подложки.
BLANK_TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

#: Адреса, которые страница запрашивает у сторонних служб.
EXTERNAL_PATTERNS = (
    "**/*.basemaps.cartocdn.com/**",
    "**/fonts.googleapis.com/**",
    "**/fonts.gstatic.com/**",
)


@pytest.fixture
def offline_page(page):
    """Страница браузера, отрезанная от внешних служб."""

    def stub_tile(route):
        route.fulfill(status=200, content_type="image/png", body=BLANK_TILE)

    def stub_text(route):
        route.fulfill(status=200, content_type="text/css", body="")

    page.route("**/*.basemaps.cartocdn.com/**", stub_tile)
    page.route("**/fonts.googleapis.com/**", stub_text)
    page.route("**/fonts.gstatic.com/**", stub_text)
    return page


@pytest.fixture
def console_errors(offline_page):
    """Собрать сообщения об ошибках, возникшие на странице."""
    collected: list[str] = []
    offline_page.on(
        "console",
        lambda message: collected.append(f"console.{message.type}: {message.text}")
        if message.type == "error"
        else None,
    )
    offline_page.on("pageerror", lambda error: collected.append(f"pageerror: {error}"))
    return collected


#: Разделы, которые обязаны открываться без единой ошибки в консоли.
PAGES = [
    ("/", "главная"),
    ("/map/", "карта"),
    ("/objects/", "реестр объектов"),
    ("/traffic/", "дорожная обстановка"),
    ("/flows/", "грузопотоки"),
    ("/analytics/", "аналитика"),
    ("/analytics/forecast/", "прогноз"),
    ("/analytics/sensitivity/", "чувствительность индекса"),
    ("/analytics/typology/", "типология"),
    # Картограмма рисуется разметкой SVG, а числа в разметке проходят
    # через локализацию: запятая в дробной части останавливает разбор
    # атрибута, и об этом сообщает только консоль браузера.
    ("/analytics/spatial/", "пространственный анализ"),
    ("/methodology/", "методология"),
]


@pytest.mark.parametrize("path,title", PAGES)
def test_page_has_no_console_errors(
    live_server, offline_page, console_errors, full_dataset, path, title
):
    """Раздел «{title}» открывается без ошибок в консоли браузера."""
    offline_page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert console_errors == [], f"страница {path} ({title}): {console_errors}"


class TestMap:
    """Карта — раздел, ради которого этот набор и появился."""

    def test_map_initializes(self, live_server, offline_page, console_errors, full_dataset):
        """Карта создаётся и отрисовывает подложку.

        Проверяется не отсутствие исключения, а результат: контейнер получил
        разметку Leaflet и на нём появились плитки подложки.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")

        assert console_errors == [], console_errors
        assert offline_page.locator("#map-canvas.leaflet-container").count() == 1
        offline_page.wait_for_selector("img.leaflet-tile", timeout=10_000)
        assert offline_page.locator("img.leaflet-tile").count() > 0

    def test_map_centered_on_moscow(self, live_server, offline_page, full_dataset):
        """Карта центрирована на Москве, а не на нулевом меридиане.

        Проверка смотрит на настройки в том виде, в каком их разобрал
        клиент, а не на разметку: локализованное число остаётся в разметке
        синтаксически корректным и на глаз неотличимо.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        center = offline_page.evaluate(
            "JSON.parse(document.getElementById('map-settings').textContent).center"
        )
        assert len(center) == 2
        latitude, longitude = center
        assert 55.0 < latitude < 56.5
        assert 36.5 < longitude < 38.5

    def test_object_layer_renders(self, live_server, offline_page, console_errors, full_dataset):
        """Слой объектов инфраструктуры загружается и отрисовывается."""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_function(
            "document.querySelectorAll("
            "'#map-canvas canvas, #map-canvas .leaflet-overlay-pane path'"
            ").length > 0",
            timeout=10_000,
        )
        assert console_errors == [], console_errors


class TestTheme:
    """Переключение оформления."""

    def test_theme_switch_keeps_map_alive(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Смена оформления не роняет карту.

        Подложка меняется наблюдателем за атрибутом data-theme. Ошибка
        в этом обработчике не видна на снимке страницы, но ломает карту
        при первом же переключении темы.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("img.leaflet-tile", timeout=10_000)

        offline_page.click("button[data-action='theme']")
        offline_page.wait_for_timeout(500)

        assert console_errors == [], console_errors
        assert offline_page.locator("img.leaflet-tile").count() > 0
