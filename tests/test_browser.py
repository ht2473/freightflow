"""Проверки в настоящем браузере.

Метод: системное тестирование методом чёрного ящика, динамическое.

Тестовый клиент Django получает разметку, но не исполняет сценарии и не
разбирает встроенные в страницу данные, поэтому поведение клиентской части
остаётся вне его охвата. Здесь страницы открываются управляемым браузером,
и любое сообщение об ошибке в консоли считается отказом.

Внешняя сеть при этом не используется: тайлы карты система отдаёт сама,
а запросы к службе шрифтов перехватываются и обслуживаются заглушками.
Проверка не должна зависеть от доступности стороннего сервиса — иначе она
начнёт падать по причинам, к приложению не относящимся.

Запуск:
    pytest tests/test_browser.py
Требуется однократная установка браузера:
    python -m playwright install chromium
"""

from __future__ import annotations

import io
import os

import pytest

# Синхронный интерфейс Playwright работает поверх цикла событий, и Django
# считает такой вызов небезопасным: защита рассчитана на прикладной код,
# случайно обратившийся к базе из сопрограммы. Здесь обращения выполняет сам
# набор проверок, из отдельного потока сервера, поэтому защита снимается.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

pytest.importorskip("playwright", reason="Playwright не установлен")

pytestmark = [pytest.mark.django_db, pytest.mark.browser]

#: Адреса, которые страница запрашивает у сторонних служб.
EXTERNAL_PATTERNS = (
    "**/fonts.googleapis.com/**",
    "**/fonts.gstatic.com/**",
)


@pytest.fixture
def offline_page(page):
    """Страница браузера, отрезанная от внешних служб."""

    def stub_text(route):
        route.fulfill(status=200, content_type="text/css", body="")

    for pattern in EXTERNAL_PATTERNS:
        page.route(pattern, stub_text)
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
        """Карта создаётся и сообщает о готовности.

        Проверяется не отсутствие исключения, а результат: холст WebGL создан
        и сценарий довёл построение до конца.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        assert console_errors == [], console_errors
        assert offline_page.locator("#map-canvas canvas.maplibregl-canvas").count() == 1

    def test_map_requests_vector_tiles(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Данные приходят тайлами со своего домена, а не сторонней службы.

        Запросы считаются по журналу браузера, а не по ``performance``
        страницы: тайлы забирает и разбирает рабочий поток, и его обращения
        в перечень ресурсов главного потока не попадают.
        """
        requested: list[str] = []
        offline_page.on("request", lambda request: requested.append(request.url))

        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        offline_page.wait_for_timeout(2000)

        tiles = [url for url in requested if url.endswith(".pbf")]
        assert tiles, requested
        assert any(url.endswith("/tiles/tiles.json") for url in requested), requested
        assert all(url.startswith(live_server.url) for url in tiles), tiles

        # Всё, что относится к карте, приходит со своего домена: внешними
        # остаются только запросы к службе шрифтов, перехваченные заглушкой.
        external = [url for url in requested if not url.startswith(live_server.url)]
        assert all("fonts.g" in url for url in external), external
        assert console_errors == [], console_errors

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

    def test_layers_are_drawn(self, live_server, offline_page, console_errors, full_dataset):
        """На холсте появляется изображение, а не однотонная заливка.

        Слои рисуются средствами WebGL, поэтому проверяется само
        изображение: снимок холста снимается браузером, потому что чтение
        буфера из сценария даёт пустоту — содержимое кадра после отрисовки
        не сохраняется.
        """
        from PIL import Image

        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        offline_page.wait_for_timeout(2500)

        shot = offline_page.locator("#map-canvas canvas.maplibregl-canvas").screenshot()
        colours = Image.open(io.BytesIO(shot)).convert("RGB").getcolors(maxcolors=1 << 20)

        assert colours is not None and len(colours) > 8, "на холсте карты только фон"
        assert console_errors == [], console_errors

    def test_district_labels_are_shown(self, live_server, offline_page, full_dataset):
        """Названия округов выводятся разметкой поверх карты."""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        assert offline_page.locator(".map-label").count() > 0

    def test_layer_toggle_does_not_reload(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Включение слоя выполняется на полученных данных, без запроса."""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        requested: list[str] = []
        offline_page.on("request", lambda request: requested.append(request.url))
        offline_page.click("input[data-layer='incidents']")
        offline_page.wait_for_timeout(500)

        assert not [url for url in requested if url.endswith(".pbf")], requested
        assert console_errors == [], console_errors


class TestTheme:
    """Переключение оформления."""

    def test_theme_switch_keeps_map_alive(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Смена оформления не роняет карту.

        Стиль собирается заново теми же переменными, что и остальной
        интерфейс. Ошибка в этом обработчике не видна на снимке страницы,
        но ломает карту при первом же переключении темы.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        offline_page.click("button[data-action='theme']")
        offline_page.wait_for_timeout(1000)

        assert console_errors == [], console_errors
        assert offline_page.locator("#map-canvas canvas.maplibregl-canvas").count() == 1
